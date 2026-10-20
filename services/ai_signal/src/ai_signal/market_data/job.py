from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_signal.config import AiSignalSettings
from ai_signal.db import create_engine
from ai_signal.market_data.beta import SymbolHistory, estimate_symbol_betas, symbol_ids
from ai_signal.market_data.config import MarketDataSettings, load_market_data_settings
from ai_signal.market_data.fetch import (
    DailyClose,
    fetch_constituent_weights,
    fetch_index_daily,
    fetch_us_daily,
)
from ai_signal.market_data.kis_client import KisGetter, KisRestClient
from ai_signal.market_data.repository import (
    KST,
    MARKET_INDEX_TICKER,
    Universe,
    WeightSnapshot,
    index_dates,
    load_index_closes,
    load_stock_closes,
    load_universe,
    load_us_closes,
    load_weight_snapshots,
    upsert_constituent_weights,
    upsert_index_values,
    upsert_symbol_betas,
    upsert_us_etf_daily,
)
from ai_signal.moves.pending import complete_pending_moves

logger = logging.getLogger(__name__)

US_BROAD_TICKER = "SPY"
US_DATE_SLACK = timedelta(days=7)


@dataclass(frozen=True)
class SessionClose:
    zone: ZoneInfo
    final_after: time


KRX_SESSION = SessionClose(KST, time(15, 45))
US_SESSION = SessionClose(ZoneInfo("America/New_York"), time(16, 30))


def closed_sessions(
    rows: list[DailyClose], session: SessionClose, now: datetime
) -> list[DailyClose]:
    """Drop the current session's row until its close is final."""
    local = now.astimezone(session.zone)
    today = local.date()
    final_today = local.time() >= session.final_after
    return [
        row
        for row in rows
        if row.business_date < today or (row.business_date == today and final_today)
    ]


@dataclass
class RunSummary:
    index_rows: int = 0
    us_rows: int = 0
    weight_rows: int = 0
    beta_rows: int = 0
    filled_moves: int = 0
    beta_methods: dict[str, int] = field(default_factory=dict)
    skipped_symbols: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunWindow:
    beta_start: date
    end: date
    fetch_start: date
    fetch_weights: bool = True


async def collect_closes(
    engine: AsyncEngine,
    client: KisGetter,
    universe: Universe,
    start: date,
    end: date,
    summary: RunSummary,
    now: datetime | None = None,
) -> None:
    clock = now or datetime.now(UTC)
    for index in universe.domestic:
        fetched = await fetch_index_daily(client, index.ticker, start, end)
        rows = closed_sessions(fetched, KRX_SESSION, clock)
        async with engine.begin() as conn:
            await upsert_index_values(conn, index.entity_id, rows, datetime.now(UTC))
        summary.index_rows += len(rows)
        logger.info("index %s: %d daily closes", index.ticker, len(rows))
    for etf in universe.us_etfs:
        fetched = await fetch_us_daily(
            client, etf.ticker, etf.exchange_code, start - US_DATE_SLACK, end
        )
        rows = closed_sessions(fetched, US_SESSION, clock)
        async with engine.begin() as conn:
            await upsert_us_etf_daily(conn, etf.entity_id, rows, datetime.now(UTC))
        summary.us_rows += len(rows)
        logger.info(
            "us etf %s/%s: %d daily closes", etf.exchange_code, etf.ticker, len(rows)
        )


async def collect_weights(
    engine: AsyncEngine,
    client: KisGetter,
    universe: Universe,
    as_of_date: date,
    summary: RunSummary,
) -> None:
    ids = symbol_ids(universe.symbols)
    sector_codes = {s.sector_code for s in universe.symbols if s.sector_code}
    for index in universe.domestic:
        if index.ticker != MARKET_INDEX_TICKER and index.ticker not in sector_codes:
            continue
        weights = await fetch_constituent_weights(client, index.ticker)
        async with engine.begin() as conn:
            written = await upsert_constituent_weights(
                conn, index.entity_id, as_of_date, weights, ids, datetime.now(UTC)
            )
        summary.weight_rows += written
        logger.info(
            "weights %s as of %s: %d symbols", index.ticker, as_of_date, written
        )


async def compute_betas(
    engine: AsyncEngine,
    universe: Universe,
    beta_start: date,
    end: date,
    history_days: int,
    summary: RunSummary,
) -> None:
    market = universe.domestic_by_ticker(MARKET_INDEX_TICKER)
    spy = universe.us_by_ticker(US_BROAD_TICKER)
    if market is None or spy is None:
        raise RuntimeError("reference seeds lack KOSPI 0001 or SPY")
    load_start = beta_start - timedelta(days=history_days)
    async with engine.connect() as conn:
        index_closes = await load_index_closes(
            conn, [i.entity_id for i in universe.domestic], load_start, end
        )
        us_closes = await load_us_closes(
            conn,
            [i.entity_id for i in universe.us_etfs],
            load_start - US_DATE_SLACK,
            end,
        )
        stock_closes = await load_stock_closes(
            conn, [s.ticker for s in universe.symbols], load_start, end
        )
        snapshots = await load_weight_snapshots(conn)

    market_close = index_closes.get(market.entity_id, pd.Series(dtype=float))
    spy_close = us_closes.get(spy.entity_id, pd.Series(dtype=float))
    targets = [day for day in index_dates(market_close) if beta_start <= day <= end]
    for symbol in universe.symbols:
        stock_close = stock_closes.get(symbol.ticker)
        if stock_close is None or stock_close.empty:
            summary.skipped_symbols.append(symbol.ticker)
            continue
        sector = universe.domestic_by_ticker(symbol.sector_code or "")
        history = SymbolHistory(
            symbol=symbol,
            stock_close=stock_close,
            market_close=market_close,
            spy_close=spy_close,
            market_weights=_snapshots(snapshots, market.entity_id, symbol.entity_id),
            sector_close=index_closes.get(sector.entity_id) if sector else None,
            sector_weights=_snapshots(snapshots, sector.entity_id, symbol.entity_id)
            if sector
            else None,
            etf_close=us_closes.get(symbol.proxy_etf_entity_id)
            if symbol.proxy_etf_entity_id
            else None,
        )
        rows = estimate_symbol_betas(history, targets)
        async with engine.begin() as conn:
            await upsert_symbol_betas(conn, rows)
        summary.beta_rows += len(rows)
        for row in rows:
            summary.beta_methods[row.method] = (
                summary.beta_methods.get(row.method, 0) + 1
            )
    if summary.skipped_symbols:
        logger.warning(
            "no silver.symbol_daily_ohlc closes for %s",
            ",".join(summary.skipped_symbols),
        )


def _snapshots(
    snapshots: dict[tuple[UUID, UUID], list[WeightSnapshot]],
    index_id: UUID,
    symbol_id: UUID,
) -> list[WeightSnapshot]:
    return snapshots.get((index_id, symbol_id), [])


async def latest_business_date(
    engine: AsyncEngine, universe: Universe, end: date
) -> date | None:
    market = universe.domestic_by_ticker(MARKET_INDEX_TICKER)
    if market is None:
        return None
    async with engine.connect() as conn:
        closes = await load_index_closes(
            conn, [market.entity_id], end - timedelta(days=14), end
        )
    series = closes.get(market.entity_id)
    if series is None or series.empty:
        return None
    return index_dates(series)[-1]


async def run_once(
    engine: AsyncEngine,
    client: KisGetter,
    window: RunWindow,
    history_days: int,
) -> RunSummary:
    summary = RunSummary()
    async with engine.connect() as conn:
        universe = await load_universe(conn)
    await collect_closes(
        engine, client, universe, window.fetch_start, window.end, summary
    )
    if window.fetch_weights:
        as_of = await latest_business_date(engine, universe, window.end)
        if as_of is not None:
            await collect_weights(engine, client, universe, as_of, summary)
    await compute_betas(
        engine, universe, window.beta_start, window.end, history_days, summary
    )
    summary.filled_moves = await complete_pending_moves(engine)
    logger.info(
        "market data done: index=%d us=%d weights=%d betas=%d %s",
        summary.index_rows,
        summary.us_rows,
        summary.weight_rows,
        summary.beta_rows,
        summary.beta_methods,
    )
    return summary


def kst_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(KST).date()


def daily_window(settings: MarketDataSettings, today: date) -> RunWindow:
    start = today - timedelta(days=settings.refresh_days)
    return RunWindow(beta_start=start, end=today, fetch_start=start, fetch_weights=True)


def next_run_at(now: datetime, settings: MarketDataSettings) -> datetime:
    local = now.astimezone(KST)
    candidate = local.replace(
        hour=settings.run_at_kst.hour,
        minute=settings.run_at_kst.minute,
        second=0,
        microsecond=0,
    )
    while candidate <= local or candidate.weekday() >= 5:
        candidate = (candidate + timedelta(days=1)).replace(
            hour=settings.run_at_kst.hour, minute=settings.run_at_kst.minute
        )
    return candidate


async def execute(
    database_url: str, settings: MarketDataSettings, window: RunWindow
) -> RunSummary:
    engine = create_engine(database_url)
    try:
        async with httpx.AsyncClient(
            base_url=settings.kis_base_url, timeout=15.0
        ) as http:
            client = KisRestClient(
                http,
                settings.kis_app_key,
                settings.kis_app_secret,
                min_interval_s=settings.min_call_interval_s,
            )
            summary = await run_once(engine, client, window, settings.history_days)
            logger.info("KIS calls: %d", client.calls)
            return summary
    finally:
        await engine.dispose()


async def run_market_data(settings: AiSignalSettings) -> None:
    market_settings = load_market_data_settings()
    while True:
        now = datetime.now(UTC)
        run_at = next_run_at(now, market_settings)
        logger.info("market data job next run at %s", run_at.isoformat())
        await asyncio.sleep((run_at - now).total_seconds())
        try:
            await execute(
                settings.database_url,
                market_settings,
                daily_window(market_settings, kst_today()),
            )
        except Exception:
            logger.exception("market data job failed; retrying at the next schedule")
