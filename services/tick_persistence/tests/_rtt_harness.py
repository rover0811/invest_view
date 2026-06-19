from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator

import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session


@dataclass
class DbRoundtripCounter:
    sync_engine: Any
    statements: int = 0
    inserts: int = 0
    updates: int = 0
    selects: int = 0
    others: int = 0
    begins: int = 0
    commits: int = 0
    rollbacks: int = 0
    _verbs: dict[str, int] = field(default_factory=dict, repr=False)

    def _before_cursor_execute(
        self,
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        self.statements += 1
        head = statement.lstrip()[:6].upper()
        if head.startswith("INSERT"):
            self.inserts += 1
        elif head.startswith("UPDATE"):
            self.updates += 1
        elif head.startswith("SELECT"):
            self.selects += 1
        else:
            self.others += 1
        verb = head.split(None, 1)[0] if head.strip() else "?"
        self._verbs[verb] = self._verbs.get(verb, 0) + 1

    def _after_begin(self, _session: Session, _transaction: Any, _connection: Any) -> None:
        self.begins += 1

    def _after_commit(self, _session: Session) -> None:
        self.commits += 1

    def _after_rollback(self, _session: Session) -> None:
        self.rollbacks += 1

    def __enter__(self) -> DbRoundtripCounter:
        event.listen(self.sync_engine, "before_cursor_execute", self._before_cursor_execute)
        event.listen(Session, "after_begin", self._after_begin)
        event.listen(Session, "after_commit", self._after_commit)
        event.listen(Session, "after_rollback", self._after_rollback)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self.sync_engine, "before_cursor_execute", self._before_cursor_execute)
        event.remove(Session, "after_begin", self._after_begin)
        event.remove(Session, "after_commit", self._after_commit)
        event.remove(Session, "after_rollback", self._after_rollback)


@contextmanager
def count_db_roundtrips(engine: AsyncEngine) -> Generator[DbRoundtripCounter, None, None]:
    counter = DbRoundtripCounter(sync_engine=engine.sync_engine)
    with counter:
        yield counter


@pytest_asyncio.fixture
async def rtt_counter(db_session_factory: async_sessionmaker[AsyncSession]) -> Any:
    return count_db_roundtrips


async def test_rtt_harness_counts_n_selects(migrated_url: str) -> None:
    from tick_persistence.db.session import create_engine, create_session_factory

    engine = create_engine(migrated_url)
    session_factory = create_session_factory(engine)
    n = 5
    try:
        async with session_factory() as session:
            with count_db_roundtrips(engine) as ctr:
                for _ in range(n):
                    await session.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

    assert ctr.statements == n
    assert ctr.selects == n
    assert ctr.inserts == 0
    assert ctr.updates == 0


async def test_rtt_harness_classifies_insert_vs_select(migrated_url: str) -> None:
    from tick_persistence.db.session import create_engine, create_session_factory

    engine = create_engine(migrated_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            await session.execute(text("CREATE TEMP TABLE _rtt_test (v int) ON COMMIT DROP"))
            with count_db_roundtrips(engine) as ctr:
                await session.execute(text("SELECT 1"))
                await session.execute(text("SELECT 2"))
                await session.execute(text("INSERT INTO _rtt_test VALUES (1)"))
    finally:
        await engine.dispose()

    assert ctr.statements == 3
    assert ctr.selects == 2
    assert ctr.inserts == 1


async def test_rtt_harness_two_instances_are_independent(migrated_url: str) -> None:
    from tick_persistence.db.session import create_engine, create_session_factory

    engine = create_engine(migrated_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            with count_db_roundtrips(engine) as ctr1:
                for _ in range(3):
                    await session.execute(text("SELECT 1"))

        async with session_factory() as session:
            with count_db_roundtrips(engine) as ctr2:
                await session.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

    assert ctr1.statements == 3
    assert ctr2.statements == 1


async def test_rtt_harness_excludes_statements_outside_block(migrated_url: str) -> None:
    from tick_persistence.db.session import create_engine, create_session_factory

    engine = create_engine(migrated_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
            with count_db_roundtrips(engine) as ctr:
                await session.execute(text("SELECT 2"))
            await session.execute(text("SELECT 3"))
    finally:
        await engine.dispose()

    assert ctr.statements == 1
