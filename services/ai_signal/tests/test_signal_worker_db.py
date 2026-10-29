from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai_signal.avro import AvroValueDeserializer, AvroValueSerializer
from ai_signal.consumers import ConsumedMessage, HandlerContext
from ai_signal.db import create_session_factory
from ai_signal.documents.judgement import DocumentJudgementOutput
from ai_signal.documents.processor import DocumentProcessor
from ai_signal.outbox import OutboxWriter
from ai_signal.signals import SignalWorker
from ai_signal.signals.generation import SignalGenerator
from alembic import command
from tests.document_fakes import judgement, news_value, scripted_judge
from tests.fakes import FakeSerializer, mock_registry
from tests.signal_fakes import (
    MOVE_ID,
    NOW,
    WINDOW_END,
    WINDOW_START,
    FakeLlm,
    cause,
    fake_generator,
    output_json,
    request_message,
)

PostgresContainer = importlib.import_module(
    "testcontainers.community.postgres"
).PostgresContainer

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "silver.document_judgement",
    "silver.document_entity",
    "silver.document_revision",
    "silver.document_kis_news",
    "silver.document",
    "bronze.news_title_raw",
    "gold.signal_cause_source",
    "gold.signal_cause",
    "gold.signal_retry",
    "gold.signal",
    "gold.move",
    "outbox.events",
)
IN_RANGE = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)
BEFORE_RANGE = datetime(2026, 10, 19, 0, 30, tzinfo=UTC)
REGISTRY = mock_registry("signal-updates")


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


def _execute(url: str, sql: str, params: dict[str, object] | None = None) -> None:
    async def run() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as connection:
            _ = await connection.execute(text(sql), params or {})
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


def _insert_move(url: str, version: int = 1) -> None:
    _execute(
        url,
        """
        INSERT INTO gold.move (
            move_id, move_type, symbol_entity_id, window_start, window_end,
            return_pct, price_state, contrib_global, contrib_market,
            contrib_sector, contrib_idio, beta_method, dominant_layer,
            signal_status, version
        )
        SELECT :id, 'DAILY', entity_id, :start, :end, 3.1, 'POSITIVE',
               0.4, 0.9, 0.3, 1.5, 'OLS_4L', 'IDIO', 'HIDDEN', :version
        FROM reference.entity_symbol WHERE ticker = '000660'
        ON CONFLICT (move_id) DO UPDATE SET version = EXCLUDED.version
        """,
        {"id": MOVE_ID, "start": WINDOW_START, "end": WINDOW_END, "version": version},
    )


def _news(doc_id: str, title: str, published_at: datetime) -> ConsumedMessage:
    return ConsumedMessage(
        topic="news-raw",
        partition=0,
        offset=1,
        key="000660",
        value=news_value(source_doc_id=doc_id, title=title, published_at=published_at),
        headers={},
        timestamp_ms=None,
    )


def _seed_documents(
    url: str,
    outputs: Sequence[DocumentJudgementOutput] | None = None,
    messages: Sequence[ConsumedMessage] | None = None,
) -> None:
    judge, _ = scripted_judge(
        outputs
        or [
            judgement(direction=1),
            judgement(direction=1),
            judgement(direction=-1),
            judgement(direction=1, fail_reason="LISTING_ONLY"),
        ]
    )
    messages = messages or (
        _news("1", "SK하이닉스 HBM 공급 확대", IN_RANGE),
        _news("2", "SK하이닉스 지난주 실적 발표", BEFORE_RANGE),
        _news("3", "SK하이닉스 소송 제기", IN_RANGE),
        _news("4", "외국인 순매수 상위 종목", IN_RANGE),
    )

    async def run() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        factory = create_session_factory(engine)
        processor = DocumentProcessor(judge_factory=lambda: judge)
        for message in messages:
            async with factory() as session, session.begin():
                await processor(
                    message,
                    HandlerContext(session, OutboxWriter(session, FakeSerializer())),
                )
        await engine.dispose()

    asyncio.run(run())


@pytest.fixture
def seeded_url(database_url: str) -> str:
    for table in TABLES:
        _execute(database_url, f"DELETE FROM {table}")
    _insert_move(database_url)
    return database_url


def _work(url: str, generator: SignalGenerator, *messages: ConsumedMessage) -> None:
    async def run() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        factory = create_session_factory(engine)
        worker = SignalWorker(generator_factory=lambda: generator, clock=lambda: NOW)
        serializer = AvroValueSerializer(REGISTRY)
        for message in messages:
            async with factory() as session, session.begin():
                await worker(
                    message, HandlerContext(session, OutboxWriter(session, serializer))
                )
        await engine.dispose()

    asyncio.run(run())


def _updates(url: str) -> list[dict[str, Any]]:
    deserializer = AvroValueDeserializer(REGISTRY)
    rows = _rows(
        url,
        "SELECT payload FROM outbox.events WHERE aggregatetype = 'signal-updates' "
        "ORDER BY created_at",
    )
    return [deserializer.deserialize("signal-updates", bytes(row[0])) for row in rows]


def _signals(url: str) -> list[tuple[Any, ...]]:
    return _rows(
        url,
        "SELECT version, status, move_version, decided_by, model_version, "
        "prompt_version, headline_cause_rank FROM gold.signal ORDER BY version",
    )


def _attempts(url: str) -> list[Any]:
    return [
        row[0]
        for row in _rows(url, "SELECT llm_attempts FROM gold.signal ORDER BY version")
    ]


def _user_input(llm: FakeLlm) -> dict[str, Any]:
    messages = llm.requests[0]["messages"]
    assert isinstance(messages, list)
    return json.loads(messages[1]["content"])


def test_shown_signal_uses_only_same_direction_documents_in_range(
    seeded_url: str,
) -> None:
    _seed_documents(seeded_url)
    generator, llm = fake_generator([output_json([cause(1, ["D1"])])])
    _work(seeded_url, generator, request_message())

    assert llm.calls == 1
    documents = _user_input(llm)["documents"]
    assert [d["title"] for d in documents] == ["SK하이닉스 HBM 공급 확대"]
    assert _user_input(llm)["decomposition"]["dominant_layer"] == "IDIO"
    assert _user_input(llm)["events"] == "none"
    assert _signals(seeded_url) == [
        (
            1,
            "SHOWN",
            1,
            "llm",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "signal_generation/v3",
            1,
        )
    ]
    assert _rows(
        seeded_url,
        "SELECT rank, label, scope, timing, lag_seconds FROM gold.signal_cause",
    ) == [(1, "HBM 공급 확대", "SYMBOL", "LEADING", 0)]
    assert _rows(
        seeded_url,
        "SELECT s.source_type, d.title FROM gold.signal_cause_source s "
        "JOIN silver.document d ON d.document_id = s.source_id",
    ) == [("DOCUMENT", "SK하이닉스 HBM 공급 확대")]
    assert _rows(seeded_url, "SELECT signal_status FROM gold.move") == [("SHOWN",)]
    assert _attempts(seeded_url) == [
        [
            {
                "step": "primary",
                "adapter": "fake",
                "model": "nvidia/nemotron-3-super-120b-a12b:free",
                "error": None,
                "raw_content": output_json([cause(1, ["D1"])]),
                "cached": False,
            }
        ]
    ]

    (update,) = _updates(seeded_url)
    assert update["symbol"] == "000660"
    assert update["move_id"] == str(MOVE_ID)
    assert (update["version"], update["move_version"]) == (1, 1)
    assert update["move_window_end"] == WINDOW_END
    assert update["status"] == "SHOWN"
    assert update["headline_label"] == "HBM 공급 확대"
    assert update["generation_method"] == "llm"
    assert update["symbol_name"] == "SK하이닉스"
    (document_id,) = _rows(
        seeded_url,
        "SELECT source_id::text FROM gold.signal_cause_source "
        "WHERE source_type = 'DOCUMENT' AND rank = 1",
    )[0]
    assert update["causes"] == [
        {
            "rank": 1,
            "explanation": "엔비디아향 HBM 공급이 늘어난다는 소식에 매수세가 몰렸어요.",
            "source_count": 1,
            "source_document_ids": [document_id],
        }
    ]


def test_redelivered_request_is_a_no_op(seeded_url: str) -> None:
    _seed_documents(seeded_url)
    generator, llm = fake_generator([output_json(), output_json()])
    message = request_message()
    _work(seeded_url, generator, message, message)

    assert llm.calls == 1
    assert [row[0] for row in _signals(seeded_url)] == [1]
    assert len(_updates(seeded_url)) == 1


def test_no_candidate_without_llm_then_new_move_version_increments(
    seeded_url: str,
) -> None:
    generator, llm = fake_generator([])
    _work(seeded_url, generator, request_message())
    _insert_move(seeded_url, version=2)
    _work(seeded_url, generator, request_message(offset=2))

    assert llm.calls == 0
    assert [row[:4] for row in _signals(seeded_url)] == [
        (1, "NO_CANDIDATE", 1, None),
        (2, "NO_CANDIDATE", 2, None),
    ]
    updates = _updates(seeded_url)
    assert [(u["version"], u["move_version"], u["status"]) for u in updates] == [
        (1, 1, "NO_CANDIDATE"),
        (2, 2, "NO_CANDIDATE"),
    ]
    assert all(u["generation_method"] is None for u in updates)
    assert _rows(seeded_url, "SELECT count(*) FROM gold.signal_cause") == [(0,)]
    assert _attempts(seeded_url) == [None, None]


def test_llm_failure_hides_and_registers_retry(seeded_url: str) -> None:
    _seed_documents(seeded_url)
    generator, llm = fake_generator([429, 503])
    _work(seeded_url, generator, request_message(reason=STATE_CHANGE))

    assert llm.calls == 2
    assert [row[:4] for row in _signals(seeded_url)] == [(1, "HIDDEN", 1, None)]
    assert _attempts(seeded_url) == [
        [
            {
                "step": "primary",
                "adapter": "fake",
                "model": "nvidia/nemotron-3-super-120b-a12b:free",
                "error": "RATE_LIMIT",
                "raw_content": None,
                "cached": False,
            },
            {
                "step": "secondary",
                "adapter": "fake",
                "model": "nvidia/nemotron-3.5-super-vl:free",
                "error": "UNAVAILABLE",
                "raw_content": None,
                "cached": False,
            },
        ]
    ]
    assert _rows(
        seeded_url,
        "SELECT target_version, attempts, last_error, next_attempt_at "
        "FROM gold.signal_retry",
    ) == [(1, 0, "UNAVAILABLE", NOW + timedelta(seconds=30))]
    (update,) = _updates(seeded_url)
    assert (update["status"], update["generation_method"]) == ("HIDDEN", None)

    _execute(seeded_url, "DELETE FROM gold.signal")
    retry_generator, _ = fake_generator([output_json()])
    _work(seeded_url, retry_generator, request_message(offset=2))
    assert _rows(seeded_url, "SELECT count(*) FROM gold.signal_retry") == [(0,)]


def test_rule_violating_answers_are_kept_on_hidden_signal(seeded_url: str) -> None:
    _seed_documents(seeded_url)
    uncited = output_json([cause(1, ["D9"])])
    generator, llm = fake_generator([uncited, "not json"])
    _work(seeded_url, generator, request_message(reason=STATE_CHANGE))

    assert llm.calls == 2
    assert [row[:2] for row in _signals(seeded_url)] == [(1, "HIDDEN")]
    assert _attempts(seeded_url) == [
        [
            {
                "step": "primary",
                "adapter": "fake",
                "model": "nvidia/nemotron-3-super-120b-a12b:free",
                "error": "SCHEMA",
                "raw_content": uncited,
                "cached": False,
            },
            {
                "step": "secondary",
                "adapter": "fake",
                "model": "nvidia/nemotron-3.5-super-vl:free",
                "error": "SCHEMA",
                "raw_content": "not json",
                "cached": False,
            },
        ]
    ]


def test_disabled_symbol_and_missing_move_are_skipped(seeded_url: str) -> None:
    generator, llm = fake_generator([output_json()])
    _execute(
        seeded_url,
        "UPDATE reference.entity_symbol SET signal_enabled = false WHERE ticker = '000660'",
    )
    try:
        _work(seeded_url, generator, request_message(), request_message("005930"))
    finally:
        _execute(
            seeded_url,
            "UPDATE reference.entity_symbol SET signal_enabled = true "
            "WHERE ticker = '000660'",
        )
    assert llm.calls == 0
    assert _signals(seeded_url) == []


STATE_CHANGE = "PRICE_STATE_CHANGE"


def _retry_targets(url: str) -> list[tuple[Any, ...]]:
    return _rows(url, "SELECT target_version FROM gold.signal_retry")


def _forget_inputs(url: str) -> None:
    """Make the next request see a changed input so only the rule under test decides."""
    _execute(url, "UPDATE gold.signal SET input_hash = NULL")


def test_new_document_failure_keeps_previous_shown_signal(seeded_url: str) -> None:
    _seed_documents(seeded_url)
    generator, _ = fake_generator([output_json()])
    _work(seeded_url, generator, request_message())
    _forget_inputs(seeded_url)

    failing, llm = fake_generator([429, 503])
    later = NOW + timedelta(minutes=30)
    _work(seeded_url, failing, request_message(offset=2, event_time=later))

    assert llm.calls == 2
    assert [row[:3] for row in _signals(seeded_url)] == [(1, "SHOWN", 1)]
    assert [u["status"] for u in _updates(seeded_url)] == ["SHOWN"]
    assert _retry_targets(seeded_url) == [(1,)]
    assert _rows(seeded_url, "SELECT signal_status FROM gold.move") == [("SHOWN",)]


def test_new_document_failure_without_previous_writes_nothing(
    seeded_url: str,
) -> None:
    _seed_documents(seeded_url)
    generator, _ = fake_generator([429, 503])
    _work(seeded_url, generator, request_message())

    assert _signals(seeded_url) == []
    assert _updates(seeded_url) == []
    assert _retry_targets(seeded_url) == [(1,)]


def test_neutral_move_hides_on_state_change_and_ignores_documents(
    seeded_url: str,
) -> None:
    _seed_documents(seeded_url)
    _execute(seeded_url, "UPDATE gold.move SET price_state = 'NEUTRAL'")
    generator, llm = fake_generator([output_json()])
    _work(seeded_url, generator, request_message())
    assert _signals(seeded_url) == []

    _work(seeded_url, generator, request_message(offset=2, reason=STATE_CHANGE))
    _forget_inputs(seeded_url)
    _work(seeded_url, generator, request_message(offset=3))

    assert llm.calls == 0
    assert [row[:4] for row in _signals(seeded_url)] == [(1, "HIDDEN", 1, None)]
    (update,) = _updates(seeded_url)
    assert (update["status"], update["causes"]) == ("HIDDEN", [])
    assert _retry_targets(seeded_url) == []


def test_new_document_requests_coalesce_in_event_time(seeded_url: str) -> None:
    _seed_documents(seeded_url)
    generator, llm = fake_generator([output_json(), output_json(), output_json()])
    _work(seeded_url, generator, request_message())
    _forget_inputs(seeded_url)
    _work(
        seeded_url,
        generator,
        request_message(offset=2, event_time=NOW + timedelta(minutes=9, seconds=59)),
    )
    assert llm.calls == 1

    _work(
        seeded_url,
        generator,
        request_message(offset=3, event_time=NOW + timedelta(minutes=10)),
    )
    _forget_inputs(seeded_url)
    _work(
        seeded_url,
        generator,
        request_message(
            offset=4, reason=STATE_CHANGE, event_time=NOW + timedelta(minutes=11)
        ),
    )

    assert llm.calls == 3
    assert _rows(
        seeded_url, "SELECT version, as_of_event_time FROM gold.signal ORDER BY 1"
    ) == [(1, NOW), (2, NOW + timedelta(minutes=11))]
    # The minute-10 answer cites no document newer than v1, so the guard keeps v1.
    assert _rows(
        seeded_url,
        "SELECT move_version, event_time, decision, guard_applied, "
        "jsonb_array_length(llm_attempts) FROM gold.signal_decision",
    ) == [(1, NOW + timedelta(minutes=10), "KEEP", True, 1)]
    assert [u["version"] for u in _updates(seeded_url)] == [1, 2]


def test_candidates_are_bounded_by_request_event_time(seeded_url: str) -> None:
    _seed_documents(seeded_url)
    generator, llm = fake_generator([output_json()])
    early = IN_RANGE - timedelta(minutes=11)
    _work(seeded_url, generator, request_message(event_time=early))
    assert llm.calls == 0
    assert [row[:2] for row in _signals(seeded_url)] == [(1, "NO_CANDIDATE")]

    _work(seeded_url, generator, request_message(offset=2, event_time=IN_RANGE))
    assert llm.calls == 1
    assert [row[:2] for row in _signals(seeded_url)][-1] == (2, "SHOWN")


def test_price_recap_is_a_candidate_with_its_character(seeded_url: str) -> None:
    _seed_documents(
        seeded_url,
        [
            judgement(
                direction=1,
                scope="MARKET",
                info_type="PRICE_RECAP",
                event_subject="코스피",
                event_type="OTHER",
                event_object="지수 반등",
                target_relevance="SECTOR",
            )
        ],
        [_news("9", "코스피 반등에 반도체 강세", IN_RANGE)],
    )
    generator, llm = fake_generator([output_json([cause(1, ["D1"])])])
    _work(seeded_url, generator, request_message())

    documents = _user_input(llm)["documents"]
    assert [d["title"] for d in documents] == ["코스피 반등에 반도체 강세"]
    assert documents[0]["info_type"] == "PRICE_RECAP"
    assert documents[0]["target_relevance"] == "SECTOR"
    assert documents[0]["event"] == {
        "subject": "코스피",
        "type": "OTHER",
        "object": "지수 반등",
    }


def test_documents_judged_before_v4_stay_candidates(seeded_url: str) -> None:
    _seed_documents(seeded_url)
    _execute(
        seeded_url,
        "UPDATE silver.document_judgement SET info_type = NULL, event_subject = NULL, "
        "event_type = NULL, event_object = NULL, target_relevance = NULL",
    )
    generator, llm = fake_generator([output_json([cause(1, ["D1"])])])
    _work(seeded_url, generator, request_message())

    documents = _user_input(llm)["documents"]
    assert [d["title"] for d in documents] == ["SK하이닉스 HBM 공급 확대"]
    assert documents[0]["info_type"] is None
    assert documents[0]["event"] == {"subject": None, "type": None, "object": None}
