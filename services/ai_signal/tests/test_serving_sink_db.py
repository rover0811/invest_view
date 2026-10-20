from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_signal.consumers import ConsumedMessage, HandlerContext
from ai_signal.db import create_session_factory
from ai_signal.outbox import OutboxWriter
from ai_signal.serving import ServingSink
from alembic import command
from tests.fakes import FakeSerializer
from tests.test_market_data_db import PostgresContainer, _asyncpg_url

SYMBOL = "005930"
DAY1_END = datetime(2026, 3, 30, 6, 30, tzinfo=UTC)
DAY2_END = DAY1_END + timedelta(days=1)
DOC_A, DOC_B, DOC_C = uuid4(), uuid4(), uuid4()


def _update(
    move_id: UUID,
    move_version: int,
    version: int,
    window_end: datetime,
    status: str = "SHOWN",
    **kw: Any,
) -> dict[str, Any]:
    causes = kw.get(
        "causes",
        [
            {
                "rank": 1,
                "explanation": f"m{move_version}v{version} c1",
                "source_count": 2,
                "source_document_ids": [str(DOC_A), str(DOC_B)],
            },
            {
                "rank": 2,
                "explanation": f"m{move_version}v{version} c2",
                "source_count": 1,
                "source_document_ids": [str(DOC_C)],
            },
        ],
    )
    return {
        "symbol": SYMBOL,
        "symbol_name": kw.get("symbol_name", "삼성전자"),
        "move_id": str(move_id),
        "version": version,
        "move_version": move_version,
        "move_window_end": window_end,
        "status": status,
        "headline_label": kw.get("label", f"label m{move_version}v{version}"),
        "generation_method": kw.get("method", "llm"),
        "causes": causes if status == "SHOWN" else [],
        "updated_at": kw.get("updated_at", datetime(2030, 1, 1, tzinfo=UTC)),
    }


async def _seed_move(engine: AsyncEngine, move_id: UUID, window_end: datetime) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO gold.move (
                    move_id, move_type, symbol_entity_id, window_start, window_end,
                    return_pct, price_state, signal_status, version
                )
                SELECT :move_id, 'DAILY', entity_id, :start, :end, 0.03,
                       'POSITIVE', 'NO_CANDIDATE', 1
                FROM reference.entity_symbol WHERE ticker = :symbol
                """
            ),
            {
                "move_id": move_id,
                "start": window_end - timedelta(days=1),
                "end": window_end,
                "symbol": SYMBOL,
            },
        )


async def _handle(engine: AsyncEngine, value: dict[str, Any]) -> None:
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        ctx = HandlerContext(session, OutboxWriter(session, FakeSerializer()))
        message = ConsumedMessage("signal-updates", 0, 0, SYMBOL, value, {}, None)
        await ServingSink()(message, ctx)


async def _current(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM serving.symbol_signal WHERE symbol = :s"),
                    {"s": SYMBOL},
                )
            )
            .mappings()
            .one()
        )
        causes = (
            await conn.execute(
                text(
                    "SELECT explanation FROM serving.symbol_signal_cause "
                    "WHERE symbol = :s ORDER BY rank"
                ),
                {"s": SYMBOL},
            )
        ).scalars()
        history = (
            await conn.execute(
                text("SELECT count(*) FROM serving.symbol_signal_history")
            )
        ).scalar()
        history_causes = (
            await conn.execute(
                text("SELECT count(*) FROM serving.symbol_signal_history_cause")
            )
        ).scalar()
        sources = (
            await conn.execute(
                text(
                    "SELECT rank, document_id FROM serving.symbol_signal_cause_source "
                    "WHERE symbol = :s"
                ),
                {"s": SYMBOL},
            )
        ).all()
        history_sources = (
            await conn.execute(
                text("SELECT count(*) FROM serving.symbol_signal_history_cause_source")
            )
        ).scalar()
        history_names = (
            await conn.execute(
                text("SELECT DISTINCT symbol_name FROM serving.symbol_signal_history")
            )
        ).scalars()
    return {
        "move_id": row["move_id"],
        "key": (row["move_version"], row["version"]),
        "status": row["status"],
        "method": row["generation_method"],
        "symbol_name": row["symbol_name"],
        "sources": {(int(rank), document_id) for rank, document_id in sources},
        "history_sources": history_sources,
        "history_names": set(history_names),
        "causes": list(causes),
        "history": history,
        "history_causes": history_causes,
    }


def test_serving_sink_orders_by_move_then_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_dir = Path(__file__).resolve().parents[1]
    config = Config(str(service_dir / "alembic.ini"))
    config.set_main_option("script_location", str(service_dir / "alembic"))

    with PostgresContainer("postgres:16-alpine") as container:
        url = _asyncpg_url(container)
        monkeypatch.setenv("AI_SIGNAL_DATABASE_URL", url)
        command.upgrade(config, "head")

        async def scenario() -> dict[str, dict[str, Any]]:
            engine = create_async_engine(url)
            move1, move2 = uuid4(), uuid4()
            try:
                await _seed_move(engine, move1, DAY1_END)
                await _seed_move(engine, move2, DAY2_END)
                out: dict[str, dict[str, Any]] = {}

                await _handle(engine, _update(move1, 1, 2, DAY1_END))
                out["first"] = await _current(engine)

                await _handle(engine, _update(move1, 1, 2, DAY1_END))
                out["redelivered"] = await _current(engine)

                older_version = _update(
                    move1, 1, 1, DAY1_END, updated_at=datetime(2031, 1, 1, tzinfo=UTC)
                )
                await _handle(engine, older_version)
                out["out_of_order"] = await _current(engine)

                await _handle(
                    engine, _update(move1, 2, 1, DAY1_END + timedelta(minutes=5))
                )
                out["newer_move_version"] = await _current(engine)

                await _handle(
                    engine, _update(move2, 1, 1, DAY2_END, method="decomposition")
                )
                out["newer_move"] = await _current(engine)

                await _handle(
                    engine, _update(move1, 3, 9, DAY1_END + timedelta(hours=1))
                )
                out["older_move_ignored"] = await _current(engine)

                await _handle(engine, _update(move2, 1, 2, DAY2_END, status="HIDDEN"))
                out["hidden"] = await _current(engine)

                await _handle(engine, _update(move2, 1, 3, DAY2_END, status="UNKNOWN"))
                out["unknown_status"] = await _current(engine)

                mismatched = [
                    {
                        "rank": 1,
                        "explanation": "count mismatch",
                        "source_count": 2,
                        "source_document_ids": [str(DOC_A)],
                    },
                    {
                        "rank": 2,
                        "explanation": "valid",
                        "source_count": 1,
                        "source_document_ids": [str(DOC_C)],
                    },
                ]
                await _handle(engine, _update(move2, 1, 4, DAY2_END, causes=mismatched))
                out["mismatched"] = await _current(engine)
                return out
            finally:
                await engine.dispose()

        out = asyncio.run(scenario())

    first = out["first"]
    assert first["key"] == (1, 2)
    assert first["causes"] == ["m1v2 c1", "m1v2 c2"]
    assert first["history"] == 1
    assert first["history_causes"] == 2
    assert first["symbol_name"] == "삼성전자"
    assert first["history_names"] == {"삼성전자"}
    assert first["sources"] == {(1, DOC_A), (1, DOC_B), (2, DOC_C)}
    assert first["history_sources"] == 3

    assert out["redelivered"] == first

    out_of_order = out["out_of_order"]
    assert out_of_order["key"] == (1, 2)
    assert out_of_order["causes"] == ["m1v2 c1", "m1v2 c2"]
    assert out_of_order["history"] == 2

    assert out["newer_move_version"]["key"] == (2, 1)
    assert out["newer_move_version"]["causes"] == ["m2v1 c1", "m2v1 c2"]

    newer_move = out["newer_move"]
    assert newer_move["move_id"] != first["move_id"]
    assert newer_move["key"] == (1, 1)
    assert newer_move["method"] == "decomposition"
    assert newer_move["causes"] == ["m1v1 c1", "m1v1 c2"]

    ignored = out["older_move_ignored"]
    assert ignored["move_id"] == newer_move["move_id"]
    assert ignored["key"] == (1, 1)
    assert ignored["causes"] == newer_move["causes"]
    assert ignored["history"] == newer_move["history"] + 1

    hidden = out["hidden"]
    assert hidden["status"] == "HIDDEN"
    assert hidden["key"] == (1, 2)
    assert hidden["causes"] == []
    assert hidden["sources"] == set()

    assert out["unknown_status"] == hidden

    mismatched = out["mismatched"]
    assert mismatched["causes"] == ["valid"]
    assert mismatched["sources"] == {(2, DOC_C)}
