from __future__ import annotations

import asyncio
import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai_signal.consumers import ConsumedMessage, HandlerContext
from ai_signal.db import create_session_factory
from ai_signal.documents.judgement import DocumentJudge
from ai_signal.documents.news import NewsRecord
from ai_signal.documents.processor import DocumentProcessor
from ai_signal.outbox import OutboxWriter
from alembic import command
from tests.document_fakes import judgement, news_message, scripted_judge
from tests.fakes import FakeSerializer

PostgresContainer = importlib.import_module(
    "testcontainers.community.postgres"
).PostgresContainer

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "bronze.news_title_raw",
    "silver.document",
    "silver.document_kis_news",
    "silver.document_revision",
    "silver.document_entity",
    "silver.document_judgement",
    "outbox.events",
)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with (
        pytest.MonkeyPatch.context() as patch,
        PostgresContainer("postgres:16-alpine") as container,
    ):
        url = (
            container.get_connection_url()
            .replace("postgresql+psycopg2", "postgresql+asyncpg")
            .replace("postgresql://", "postgresql+asyncpg://")
        )
        patch.setenv("AI_SIGNAL_DATABASE_URL", url)
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(SERVICE_ROOT / "alembic"))
        command.upgrade(config, "head")
        yield url


@pytest.fixture
def clean_url(database_url: str) -> str:
    async def truncate() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        async with engine.begin() as connection:
            for table in reversed(TABLES):
                _ = await connection.execute(text(f"DELETE FROM {table}"))
            _ = await connection.execute(text("DELETE FROM gold.move"))
        await engine.dispose()

    asyncio.run(truncate())
    _set_move(database_url, "POSITIVE")
    return database_url


def _set_move(url: str, price_state: str | None) -> None:
    """Latest move state of 000660; ``None`` removes the move."""

    async def run() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as connection:
            _ = await connection.execute(text("DELETE FROM gold.move"))
            if price_state is not None:
                _ = await connection.execute(
                    text(
                        """
                        INSERT INTO gold.move (
                            move_id, move_type, symbol_entity_id, window_start,
                            window_end, return_pct, price_state, beta_method,
                            signal_status, version
                        )
                        SELECT gen_random_uuid(), 'DAILY', entity_id,
                               now() - interval '1 day', now(), 3.1, :state,
                               'OLS_4L', 'HIDDEN', 1
                        FROM reference.entity_symbol WHERE ticker = '000660'
                        """
                    ),
                    {"state": price_state},
                )
        await engine.dispose()

    asyncio.run(run())


def _process(url: str, judge: DocumentJudge, *messages: ConsumedMessage) -> None:
    async def run() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        factory = create_session_factory(engine)
        processor = DocumentProcessor(judge_factory=lambda: judge)
        for message in messages:
            async with factory() as session, session.begin():
                context = HandlerContext(
                    session=session, outbox=OutboxWriter(session, FakeSerializer())
                )
                await processor(message, context)
        await engine.dispose()

    asyncio.run(run())


def _rows(url: str, sql: str) -> list[tuple[Any, ...]]:
    async def run() -> list[tuple[Any, ...]]:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as connection:
            result = await connection.execute(text(sql))
            rows = [tuple(row) for row in result.all()]
        await engine.dispose()
        return rows

    return asyncio.run(run())


def _counts(url: str) -> dict[str, int]:
    return {
        table: int(_rows(url, f"SELECT count(*) FROM {table}")[0][0])
        for table in TABLES
    }


def test_enabled_symbol_is_judged_and_requests_a_signal(clean_url: str) -> None:
    judge, step = scripted_judge([judgement(direction=1, scope="SYMBOL")])
    message = news_message()
    _process(clean_url, judge, message, message)

    assert _counts(clean_url) == {table: 1 for table in TABLES}
    record = NewsRecord.from_message(message)
    assert _rows(
        clean_url,
        "SELECT direction, scope, fail_reason, decided_by, model_version, "
        "prompt_version, probability::text, version FROM silver.document_judgement",
    ) == [
        (1, "SYMBOL", None, "llm", "fake/model", "document_judgement/v1", "0.8765", 1)
    ]
    assert _rows(
        clean_url,
        "SELECT d.document_id, d.source, d.version, d.publisher, d.published_precision, "
        "k.raw_id, e.link_method, e.link_confidence::text "
        "FROM silver.document d JOIN silver.document_kis_news k USING (document_id) "
        "JOIN silver.document_entity e USING (document_id)",
    ) == [
        (
            record.document_id,
            "KIS_NEWS",
            1,
            "연합뉴스",
            "SECOND",
            record.raw_id,
            "source_tag",
            "1.0000",
        )
    ]
    assert _rows(clean_url, "SELECT aggregatetype, aggregateid FROM outbox.events") == [
        ("signal-requests", "000660")
    ]
    payload = _rows(
        clean_url, "SELECT convert_from(payload, 'UTF8') FROM outbox.events"
    )[0][0]
    assert payload.startswith("signal-requests:[('event_time', datetime.datetime(")
    assert payload.endswith("('reason', 'NEW_DOCUMENT'), ('symbol', '000660')]")
    (request,) = step.requests
    assert request.title == message.value["title"]
    assert [(c.ticker, c.name, c.sectors) for c in request.candidates] == [
        ("000660", "SK하이닉스", ("전기·전자",))
    ]


def test_non_enabled_symbol_is_stored_without_judgement(clean_url: str) -> None:
    judge, step = scripted_judge([judgement()])
    _process(clean_url, judge, news_message(symbol="005380", title="현대차 신차 출시"))

    counts = _counts(clean_url)
    assert counts["silver.document_entity"] == 1
    assert counts["silver.document_judgement"] == 0
    assert counts["outbox.events"] == 0
    assert step.requests == []


def test_fail_reason_is_recorded_without_signal_request(clean_url: str) -> None:
    judge, _ = scripted_judge([judgement(direction=0, fail_reason="LISTING_ONLY")])
    _process(clean_url, judge, news_message(title="[특징주] 상승률 상위 종목"))

    assert _rows(clean_url, "SELECT fail_reason FROM silver.document_judgement") == [
        ("LISTING_ONLY",)
    ]
    assert _counts(clean_url)["outbox.events"] == 0


def test_every_step_passing_leaves_the_document_unjudged(clean_url: str) -> None:
    judge, step = scripted_judge([None, judgement()])
    message = news_message()
    _process(clean_url, judge, message)
    assert _counts(clean_url)["silver.document_judgement"] == 0

    _process(clean_url, judge, message)
    assert len(step.requests) == 2
    assert _counts(clean_url)["silver.document_judgement"] == 1


def test_title_change_adds_a_version_and_old_title_replay_is_ignored(
    clean_url: str,
) -> None:
    judge, step = scripted_judge(
        [judgement(direction=1), judgement(direction=-1, scope="SECTOR")]
    )
    first = news_message(title="삼성전자 HBM 공급 기대")
    second = news_message(title="삼성전자 HBM 공급 지연 우려")
    _process(clean_url, judge, first, second, first)

    assert _rows(
        clean_url,
        "SELECT version, title FROM silver.document_revision ORDER BY version",
    ) == [(1, "삼성전자 HBM 공급 기대"), (2, "삼성전자 HBM 공급 지연 우려")]
    assert _rows(clean_url, "SELECT version, title FROM silver.document") == [
        (2, "삼성전자 HBM 공급 지연 우려")
    ]
    assert _rows(
        clean_url,
        "SELECT version, direction, scope FROM silver.document_judgement ORDER BY version",
    ) == [(1, 1, "SYMBOL"), (2, -1, "SECTOR")]
    assert _rows(clean_url, "SELECT raw_id FROM silver.document_kis_news") == [
        (NewsRecord.from_message(second).raw_id,)
    ]
    counts = _counts(clean_url)
    assert counts["bronze.news_title_raw"] == 2
    assert counts["outbox.events"] == 1
    assert len(step.requests) == 2


def test_same_document_under_two_symbols_links_both(clean_url: str) -> None:
    judge, step = scripted_judge([judgement(scope="SECTOR")])
    _process(
        clean_url,
        judge,
        news_message(symbol="005380", title="반도체 업황 개선"),
        news_message(symbol="000660", title="반도체 업황 개선"),
    )
    counts = _counts(clean_url)
    assert counts["silver.document"] == 1
    assert counts["silver.document_entity"] == 2
    assert counts["silver.document_judgement"] == 1
    assert [c.ticker for c in step.requests[0].candidates] == ["000660"]
    assert _rows(clean_url, "SELECT aggregateid FROM outbox.events") == [("000660",)]


@pytest.mark.parametrize(
    ("price_state", "direction", "probability", "requested"),
    [
        ("POSITIVE", 1, 0.7, True),
        ("NEGATIVE", -1, 0.9, True),
        ("POSITIVE", 1, 0.69, False),
        ("POSITIVE", -1, 0.9, False),
        ("NEGATIVE", 1, 0.9, False),
        ("POSITIVE", 0, 0.9, False),
        ("NEUTRAL", 1, 0.9, False),
        (None, 1, 0.9, False),
    ],
)
def test_new_document_request_requires_probability_and_move_direction(
    clean_url: str,
    price_state: str | None,
    direction: int,
    probability: float,
    requested: bool,
) -> None:
    _set_move(clean_url, price_state)
    judge, _ = scripted_judge([judgement(direction=direction, probability=probability)])
    _process(clean_url, judge, news_message())

    counts = _counts(clean_url)
    assert counts["silver.document_judgement"] == 1
    assert counts["outbox.events"] == (1 if requested else 0)
