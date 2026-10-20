from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_signal.consumers import ConsumedMessage, HandlerContext
from ai_signal.db import create_session_factory
from ai_signal.market_data.job import RunWindow, run_once
from ai_signal.market_data.repository import KST
from ai_signal.moves.sink import MoveSink
from ai_signal.outbox import OutboxWriter
from alembic import command
from tests.fakes import FakeSerializer
from tests.test_market_data_db import (
    BETA_START,
    END,
    START,
    FakeKis,
    PostgresContainer,
    _asyncpg_url,
    _seed_stock_closes,
)

MOVE_DAY = date(2026, 3, 30)
PENDING_DAY = date(2026, 4, 1)


def _value(
    day: date, version: int, state: str = "POSITIVE", **kw: Any
) -> dict[str, Any]:
    end = datetime(day.year, day.month, day.day, 15, 30, tzinfo=KST)
    return {
        "move_id": kw.get("move_id", str(uuid4())),
        "move_type": "DAILY",
        "symbol": kw.get("symbol", "005930"),
        "window_start": (end - timedelta(days=1)).astimezone(UTC),
        "window_end": end.astimezone(UTC),
        "return_pct": 0.031,
        "price_state": state,
        "signal_status": "NO_CANDIDATE",
        "version": version,
    }


async def _handle(engine: AsyncEngine, value: dict[str, Any]) -> None:
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        ctx = HandlerContext(session, OutboxWriter(session, FakeSerializer()))
        message = ConsumedMessage("price-moves", 0, 0, value["symbol"], value, {}, None)
        await MoveSink()(message, ctx)


async def _row(engine: AsyncEngine, move_id: str) -> dict[str, Any]:
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM gold.move WHERE move_id = :id"), {"id": move_id}
                )
            )
            .mappings()
            .one()
        )
        requests = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM outbox.events WHERE aggregatetype = 'signal-requests'"
                )
            )
        ).scalar()
    return {**row, "requests": requests}


def test_move_sink_versions_decomposition_and_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_dir = Path(__file__).resolve().parents[1]
    config = Config(str(service_dir / "alembic.ini"))
    config.set_main_option("script_location", str(service_dir / "alembic"))

    with PostgresContainer("postgres:16-alpine") as container:
        url = _asyncpg_url(container)
        monkeypatch.setenv("AI_SIGNAL_DATABASE_URL", url)
        command.upgrade(config, "head")

        async def scenario() -> dict[str, Any]:
            engine = create_async_engine(url)
            try:
                await _seed_stock_closes(engine)
                window = RunWindow(beta_start=BETA_START, end=END, fetch_start=START)
                await run_once(engine, FakeKis(), window, history_days=400)
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "UPDATE reference.entity_symbol SET signal_enabled = "
                            "(ticker = '005930')"
                        )
                    )
                out: dict[str, Any] = {}
                v2 = _value(MOVE_DAY, 2, "NEGATIVE")
                move_id = v2["move_id"]
                await _handle(engine, v2)
                await _handle(engine, v2)
                await _handle(engine, _value(MOVE_DAY, 1, move_id=move_id))
                out["daily"] = await _row(engine, move_id)
                await _handle(engine, _value(MOVE_DAY, 1, symbol="000660"))
                out["requests_after_disabled"] = (await _row(engine, move_id))[
                    "requests"
                ]
                pending = _value(PENDING_DAY, 1)
                await _handle(engine, pending)
                out["pending"] = await _row(engine, pending["move_id"])
                out["filled"] = await asyncio.wait_for(
                    _fill_after_load(engine), timeout=120
                )
                out["completed"] = await _row(engine, pending["move_id"])
                return out
            finally:
                await engine.dispose()

        out = asyncio.run(scenario())

    daily = out["daily"]
    assert daily["version"] == 2
    assert daily["price_state"] == "NEGATIVE"
    assert daily["window_start"] == datetime(2026, 3, 27, 15, 30, tzinfo=KST)
    assert daily["beta_method"] in {"OLS_4L", "UNIT_4L"}
    assert daily["beta_as_of"] == date(2026, 3, 27)
    assert daily["dominant_layer"] in {"GLOBAL", "MARKET", "SECTOR", "IDIO"}
    contribs = ("contrib_global", "contrib_market", "contrib_sector", "contrib_idio")
    assert all(daily[c] is not None for c in contribs)
    assert daily["requests"] == 1
    assert out["requests_after_disabled"] == 1
    assert out["pending"]["contrib_idio"] is None
    assert out["pending"]["window_start"] == datetime(2026, 3, 31, 15, 30, tzinfo=KST)
    assert out["filled"] == 1
    assert out["completed"]["contrib_idio"] is not None
    assert out["completed"]["requests"] == 2


async def _fill_after_load(engine: AsyncEngine) -> int:
    from tests import test_market_data_db as fixtures

    extra = date(2026, 4, 1)
    key = extra.strftime("%Y%m%d")
    for levels in fixtures.INDEX_LEVELS.values():
        levels[key] = list(levels.values())[-1] * 1.01
    for levels in fixtures.US_LEVELS.values():
        levels["20260331"] = list(levels.values())[-1] * 0.99
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO silver.symbol_daily_ohlc (symbol, interval, trade_date, close) "
                "SELECT symbol, 'd', :d, close + 100 FROM silver.symbol_daily_ohlc "
                "WHERE trade_date = :prev"
            ),
            {"d": extra, "prev": END},
        )
    window = RunWindow(beta_start=END, end=extra, fetch_start=END)
    summary = await run_once(engine, FakeKis(), window, history_days=400)
    return summary.filled_moves
