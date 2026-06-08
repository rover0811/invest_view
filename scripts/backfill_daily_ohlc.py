#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportAny=false, reportExplicitAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false
"""One-shot KIS daily/weekly/monthly OHLC backfill into silver.symbol_daily_ohlc."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/invest_view"
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
INTERVAL_TO_PERIOD_DIV = {"d": "D", "w": "W", "m": "M"}
WINDOW_DAYS_BY_INTERVAL = {"d": 100, "w": 700, "m": 3000}
_TOKEN_ISSUE_RATE_LIMIT_CODE = "EGW00133"
_TOKEN_ISSUE_RETRY_SECONDS = 65
_TOKEN_ISSUE_MAX_RETRIES = 3


def _add_service_paths() -> None:
    for rel_path in (
        "services/tick_persistence/src",
        "services/kis_ingestion/src",
    ):
        path = str(ROOT_DIR / rel_path)
        if path not in sys.path:
            sys.path.insert(0, path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill KIS daily/weekly/monthly OHLC bars into silver.symbol_daily_ohlc.",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols. Defaults to KIS_WATCH_SYMBOLS JSON array from root .env.",
    )
    parser.add_argument("--years", type=int, default=10, help="History years to fetch. Default: 10.")
    parser.add_argument(
        "--intervals",
        default="d,w,m",
        help="Comma-separated intervals among d,w,m. Default: d,w,m.",
    )
    parser.add_argument(
        "--adjusted",
        dest="adjusted",
        action="store_true",
        default=True,
        help="Fetch adjusted prices. Default: enabled.",
    )
    parser.add_argument(
        "--no-adjusted",
        dest="adjusted",
        action="store_false",
        help="Fetch raw/unadjusted prices.",
    )
    return parser.parse_args()


def _load_root_env() -> None:
    env_path = ROOT_DIR / ".env"
    if not env_path.is_file():
        raise FileNotFoundError(f"root .env not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = _strip_env_value(value.strip())
        os.environ.setdefault(key, value)


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def _parse_symbols(raw_symbols: str | None) -> list[str]:
    if raw_symbols:
        symbols = [part.strip() for part in raw_symbols.split(",") if part.strip()]
    else:
        raw_watch_symbols = _require_env("KIS_WATCH_SYMBOLS")
        try:
            decoded = json.loads(raw_watch_symbols)
        except json.JSONDecodeError as exc:
            raise RuntimeError("KIS_WATCH_SYMBOLS must be a JSON array string") from exc
        if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
            raise RuntimeError("KIS_WATCH_SYMBOLS must be a JSON array of strings")
        symbols = [item.strip() for item in decoded if item.strip()]
    if not symbols:
        raise RuntimeError("no symbols were provided")
    return symbols


def _parse_intervals(raw_intervals: str) -> list[str]:
    intervals = [part.strip().lower() for part in raw_intervals.split(",") if part.strip()]
    unknown = sorted(set(intervals) - set(INTERVAL_TO_PERIOD_DIV))
    if unknown:
        raise RuntimeError(f"unsupported interval(s): {','.join(unknown)}; expected d,w,m")
    if not intervals:
        raise RuntimeError("no intervals were provided")
    return list(dict.fromkeys(intervals))


@dataclass(frozen=True)
class BackfillConfig:
    app_key: str
    app_secret: str
    database_url: str
    symbols: Sequence[str]
    intervals: Sequence[str]
    years: int
    adjusted: bool


def _build_config(args: argparse.Namespace) -> BackfillConfig:
    _load_root_env()
    if args.years < 1:
        raise RuntimeError("--years must be >= 1")
    return BackfillConfig(
        app_key=_require_env("KIS_APP_KEY"),
        app_secret=_require_env("KIS_APP_SECRET"),
        database_url=os.environ.get("TICK_PERSISTENCE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or DEFAULT_DB_URL,
        symbols=_parse_symbols(args.symbols),
        intervals=_parse_intervals(args.intervals),
        years=args.years,
        adjusted=bool(args.adjusted),
    )


async def _run(config: BackfillConfig) -> int:
    _add_service_paths()
    import sqlalchemy as sa
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from kis_ingestion.token_manager import KISTokenManager
    from tick_persistence.db.session import create_engine, create_session_factory
    from tick_persistence.kis.daily_client import fetch_all_history

    logger = logging.getLogger("backfill_daily_ohlc")
    metadata = sa.MetaData()
    ohlc_table = _define_ohlc_table(sa, metadata)

    engine = create_engine(config.database_url)
    session_factory = create_session_factory(engine)
    source = "kis_daily_adj" if config.adjusted else "kis_daily_raw"
    started = time.monotonic()
    total_rows = 0

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as http_client:
            token_manager = KISTokenManager(
                base_url=KIS_BASE_URL,
                app_key=config.app_key,
                app_secret=config.app_secret,
                http_client=http_client,
            )
            await _get_initial_token(token_manager, logger)
            logger.info(
                "Starting KIS OHLC backfill: symbols=%d intervals=%s years=%d adjusted=%s",
                len(config.symbols),
                ",".join(config.intervals),
                config.years,
                config.adjusted,
            )
            for symbol in config.symbols:
                logger.info("Symbol %s: start", symbol)
                for interval in config.intervals:
                    period_div = INTERVAL_TO_PERIOD_DIV[interval]
                    try:
                        rows = await fetch_all_history(
                            http_client,
                            token_manager,
                            config.app_key,
                            config.app_secret,
                            symbol,
                            period_div,
                            years=config.years,
                            adjusted=config.adjusted,
                            window_days=WINDOW_DAYS_BY_INTERVAL[interval],
                        )
                    except Exception as exc:  # noqa: BLE001 - one failed symbol/interval must not abort the run.
                        logger.exception(
                            "Symbol %s interval %s: skipped after KIS fetch failure: %s",
                            symbol,
                            interval,
                            exc,
                        )
                        continue
                    if not rows:
                        logger.info("Symbol %s interval %s: no rows returned; skipped", symbol, interval)
                        continue

                    async with session_factory() as session:
                        upserted = await _upsert_rows(
                            session=session,
                            table=ohlc_table,
                            pg_insert=pg_insert,
                            func=func,
                            symbol=symbol,
                            interval=interval,
                            rows=rows,
                            source=source,
                        )
                        await session.commit()
                    total_rows += upserted
                    logger.info("Symbol %s interval %s: upserted %d rows", symbol, interval, upserted)
                logger.info("Symbol %s: done", symbol)
    finally:
        await engine.dispose()

    elapsed = time.monotonic() - started
    logger.info("Backfill complete: upserted=%d elapsed_seconds=%.1f", total_rows, elapsed)
    return 0


async def _get_initial_token(token_manager: Any, logger: logging.Logger) -> str:
    for attempt in range(_TOKEN_ISSUE_MAX_RETRIES + 1):
        try:
            return await token_manager.get_token()
        except httpx.HTTPStatusError as exc:
            response_text = exc.response.text
            can_retry = (
                exc.response.status_code == 403
                and _TOKEN_ISSUE_RATE_LIMIT_CODE in response_text
                and attempt < _TOKEN_ISSUE_MAX_RETRIES
            )
            if not can_retry:
                raise
            logger.warning(
                "KIS token issuance rate-limited (%s, 1/min); waiting %d seconds before retry %d/%d",
                _TOKEN_ISSUE_RATE_LIMIT_CODE,
                _TOKEN_ISSUE_RETRY_SECONDS,
                attempt + 1,
                _TOKEN_ISSUE_MAX_RETRIES,
            )
            await asyncio.sleep(_TOKEN_ISSUE_RETRY_SECONDS)
    raise RuntimeError("unreachable token retry state")


def _define_ohlc_table(sa: Any, metadata: Any) -> Any:
    return sa.Table(
        "symbol_daily_ohlc",
        metadata,
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("interval", sa.Text, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("open", sa.Integer),
        sa.Column("high", sa.Integer),
        sa.Column("low", sa.Integer),
        sa.Column("close", sa.Integer),
        sa.Column("volume", sa.BigInteger),
        sa.Column("trade_amount", sa.BigInteger),
        sa.Column("source", sa.Text),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False),
        schema="silver",
    )


async def _upsert_rows(
    *,
    session: Any,
    table: Any,
    pg_insert: Any,
    func: Any,
    symbol: str,
    interval: str,
    rows: Iterable[Mapping[str, object]],
    source: str,
) -> int:
    fetched_at = datetime.now(UTC)
    values = [
        {
            "symbol": symbol,
            "interval": interval,
            "trade_date": _as_date(row["trade_date"]),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "volume": row.get("volume"),
            "trade_amount": row.get("trade_amount"),
            "source": source,
            "fetched_at": fetched_at,
        }
        for row in rows
    ]
    if not values:
        return 0

    stmt = pg_insert(table).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "interval", "trade_date"],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
            "trade_amount": stmt.excluded.trade_amount,
            "source": stmt.excluded.source,
            "fetched_at": func.now(),
        },
    )
    await session.execute(stmt)
    return len(values)


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    raise TypeError(f"expected trade_date date, got {type(value).__name__}")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()
    try:
        config = _build_config(args)
        return asyncio.run(_run(config))
    except KeyboardInterrupt:
        logging.getLogger("backfill_daily_ohlc").warning("Interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI should show concise operational errors.
        logging.getLogger("backfill_daily_ohlc").error("Backfill failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
