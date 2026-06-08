from __future__ import annotations

import inspect
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from alert_service.agent.tools.market import _snapshot_impl, get_symbol_snapshot


def test_get_symbol_snapshot_is_importable_decorated_tool_without_ticker_param() -> None:
    assert hasattr(get_symbol_snapshot, "tool_spec")
    assert get_symbol_snapshot.tool_spec["name"] == "get_symbol_snapshot"

    params = inspect.signature(get_symbol_snapshot).parameters
    assert list(params) == ["tool_context"]
    assert "ticker" not in params
    assert get_symbol_snapshot.tool_spec["inputSchema"]["json"]["properties"] == {}


@pytest.fixture(scope="function")
async def live_session_factory():
    url = os.getenv("ALERT_SERVICE_DATABASE_URL")
    if not url:
        pytest.skip("ALERT_SERVICE_DATABASE_URL is unset; skipping live market tool QA")

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on external DB reachability
        await engine.dispose()
        pytest.skip(f"live DB unreachable: {exc}")

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.qa
async def test_snapshot_impl_returns_snapshot_for_existing_ticker(live_session_factory) -> None:
    async with live_session_factory() as session:
        result = await session.execute(text("SELECT symbol FROM serving.symbol_snapshot LIMIT 1"))
        row = result.first()

    if row is None:
        pytest.skip("serving.symbol_snapshot has no rows; cannot assert populated snapshot")

    ticker = row[0]
    snapshot = await _snapshot_impl(live_session_factory, ticker)

    assert isinstance(snapshot, dict)
    assert snapshot["symbol"] == ticker
    assert isinstance(snapshot["last_price"], float)


@pytest.mark.qa
async def test_snapshot_impl_returns_empty_dict_for_missing_ticker(live_session_factory) -> None:
    snapshot = await _snapshot_impl(live_session_factory, "999999")

    assert snapshot == {}
