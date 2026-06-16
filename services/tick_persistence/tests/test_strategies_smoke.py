from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession

from tests.strategies import COMMON_HYPOTHESIS_SETTINGS, hypothesis_db_session, out_of_order_ticks


class RollbackExample(Exception):
    pass


def _event_key(tick: dict[str, Any]) -> tuple[str, str, int]:
    return (str(tick["business_date"]), str(tick["trade_time"]), int(tick["cumulative_volume"]))


@COMMON_HYPOTHESIS_SETTINGS
@given(ticks=out_of_order_ticks())
def test_out_of_order_ticks_generate_shuffled_strict_volume_sequences(ticks: list[dict[str, Any]]) -> None:
    assert ticks
    assert len({tick["symbol"] for tick in ticks}) == 1

    ordered_ticks = sorted(ticks, key=_event_key)
    volumes = [int(tick["cumulative_volume"]) for tick in ordered_ticks]
    assert volumes == sorted(volumes)
    assert len(set(volumes)) == len(volumes)
    assert [tick["trade_time"] for tick in ticks] != [tick["trade_time"] for tick in ordered_ticks]


async def _bronze_count(session: AsyncSession) -> int:
    count = await session.scalar(sa.text("SELECT count(*) FROM bronze.tick_history"))
    return int(count or 0)


@COMMON_HYPOTHESIS_SETTINGS
@given(dedupe_suffix=st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=1, max_size=32))
async def test_hypothesis_session_savepoint_rolls_back_between_examples(
    hypothesis_db_session: AsyncSession,
    dedupe_suffix: str,
) -> None:
    assert await _bronze_count(hypothesis_db_session) == 0

    try:
        async with hypothesis_db_session.begin_nested():
            await hypothesis_db_session.execute(
                sa.text(
                    """
                    INSERT INTO bronze.tick_history (tick_id, tick_dedupe_key, symbol)
                    VALUES (:tick_id, :tick_dedupe_key, :symbol)
                    """
                ),
                {
                    "tick_id": uuid.uuid4(),
                    "tick_dedupe_key": f"hypothesis:{dedupe_suffix}",
                    "symbol": "005930",
                },
            )
            assert await _bronze_count(hypothesis_db_session) == 1
            raise RollbackExample
    except RollbackExample:
        pass

    assert await _bronze_count(hypothesis_db_session) == 0
