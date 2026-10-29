from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from ai_signal.consumers import (
    ConsumedMessage,
    ConsumerLoop,
    ConsumerSpec,
    HandlerContext,
)
from ai_signal.consumers.registry import CONSUMERS
from ai_signal.llm import LlmErrorKind
from ai_signal.runners import runner_names
from ai_signal.signals.candidates import CANDIDATE_SOURCES, CandidateSet
from ai_signal.signals.generation import (
    CurrentCause,
    CurrentSignal,
    GenerationRequest,
)
from ai_signal.signals.move import candidate_range
from ai_signal.signals.store import update_value
from tests.fakes import (
    FakeConsumer,
    FakeDeserializer,
    FakeSerializer,
    FakeSessionFactory,
    fake_message,
)
from tests.signal_fakes import (
    BASE_MOVE,
    NOW,
    WINDOW_END,
    candidates,
    cause,
    document,
    fake_generator,
    output_json,
)


def _generate(replies: list[str | int], found: CandidateSet):
    generator, llm = fake_generator(replies)
    generation = asyncio.run(generator.generate(GenerationRequest(BASE_MOVE, found)))
    return generation, llm


def test_no_candidates_returns_no_candidate_without_llm_call() -> None:
    generation, llm = _generate([output_json()], CandidateSet())
    assert generation.decision.status == "NO_CANDIDATE"
    assert generation.decided_by is None
    assert llm.calls == 0


def test_primary_model_result_is_shown_with_cited_sources() -> None:
    reply = output_json([cause(2, ["D2"], "보조 원인"), cause(1, ["D1", "D2"])])
    generation, llm = _generate([reply], candidates("D1", "D2"))
    decision = generation.decision
    assert decision.status == "SHOWN"
    assert decision.headline_label == "HBM 공급 확대"
    assert [c.rank for c in decision.causes] == [1, 2]
    assert [s.ref for s in decision.causes[0].sources] == ["D1", "D2"]
    assert generation.decided_by == "llm"
    assert generation.model_version == "nvidia/nemotron-3-super-120b-a12b:free"
    assert generation.prompt_version == "signal_generation/v3"
    assert llm.calls == 1


def test_uncited_answer_is_a_schema_failure_and_secondary_answers() -> None:
    bad = output_json([cause(1, ["D9"])])
    generation, llm = _generate([bad, output_json()], candidates("D1"))
    assert generation.decision.status == "SHOWN"
    assert generation.model_version == "nvidia/nemotron-3.5-super-vl:free"
    assert generation.last_error == LlmErrorKind.SCHEMA
    assert llm.calls == 2


@pytest.mark.parametrize(
    ("label", "explanation"),
    [
        ("HBM 공급 확대", "엔비디아향 HBM 공급이 늘어난다는 소식에 매수세가 몰렸어요"),
        ("HBM 공급 확대", "엔비디아향 HBM 공급이 늘어난다는 소식에 매수세가 몰렸다."),
        (
            "HBM 공급 확대로",
            "엔비디아향 HBM 공급이 늘어난다는 소식에 매수세가 몰렸어요.",
        ),
        ("반도체 업황 회복에도 ", "업황이 회복되는데도 외국인이 팔았어요."),
        ("실적 발표를", "실적 발표 뒤 차익 실현 매물이 나왔어요."),
    ],
)
def test_wording_violation_is_a_schema_failure_and_secondary_answers(
    label: str, explanation: str
) -> None:
    bad = output_json(
        [cause(1, ["D1"]), cause(2, ["D1"], label=label, explanation=explanation)]
    )
    generation, llm = _generate([bad, output_json()], candidates("D1"))
    assert generation.decision.status == "SHOWN"
    assert generation.decided_by == "llm"
    assert generation.model_version == "nvidia/nemotron-3.5-super-vl:free"
    assert generation.last_error == LlmErrorKind.SCHEMA
    assert [a["error"] for a in generation.llm_attempts] == ["SCHEMA", None]
    assert generation.llm_attempts[0]["raw_content"] == bad
    assert llm.calls == 2


def test_wording_violation_on_every_model_hides_the_signal() -> None:
    bad = output_json([cause(1, ["D1"], label="수급 개선으로")])
    generation, llm = _generate([bad, bad], candidates("D1"))
    assert generation.decision.status == "HIDDEN"
    assert generation.llm_failed
    assert [a["error"] for a in generation.llm_attempts] == ["SCHEMA", "SCHEMA"]
    assert llm.calls == 2


@pytest.mark.parametrize(
    "label",
    ["HBM 공급 확대", "4분기 실적 개선", " 외국인 순매수 ", "2분기 가이던스 상향"],
)
def test_wording_rules_accept_noun_phrase_labels(label: str) -> None:
    reply = output_json(
        [cause(1, ["D1"], label=label, explanation="  외국인이 사들였어요.  ")]
    )
    generation, llm = _generate([reply], candidates("D1"))
    assert generation.decision.status == "SHOWN"
    assert generation.model_version == "nvidia/nemotron-3-super-120b-a12b:free"
    assert generation.last_error is None
    assert llm.calls == 1


def test_every_model_failing_hides_the_signal() -> None:
    generation, llm = _generate([429, 503], candidates("D1"))
    assert generation.decision.status == "HIDDEN"
    assert generation.decided_by is None
    assert generation.llm_failed
    assert generation.last_error == LlmErrorKind.UNAVAILABLE
    assert llm.calls == 2


def test_prompt_input_writes_none_for_empty_slots() -> None:
    move = replace(BASE_MOVE, contributions=None)
    body = GenerationRequest(move, candidates("D1")).prompt_input()
    assert body["decomposition"] == "none"
    assert body["events"] == "none"
    documents = body["documents"]
    assert isinstance(documents, list)
    assert documents[0]["ref"] == "D1"
    full = GenerationRequest(BASE_MOVE, CandidateSet()).prompt_input()
    assert full["documents"] == "none"
    assert full["decomposition"] == {
        "global": 0.4,
        "market": 0.9,
        "sector": 0.3,
        "idio": 1.5,
        "dominant_layer": "IDIO",
    }


def test_input_hash_depends_on_move_version_and_prompt() -> None:
    request = GenerationRequest(BASE_MOVE, candidates("D1"))
    newer = GenerationRequest(replace(BASE_MOVE, version=2), candidates("D1"))
    assert request.input_hash("signal_generation/v1") == request.input_hash(
        "signal_generation/v1"
    )
    assert request.input_hash("signal_generation/v1") != newer.input_hash(
        "signal_generation/v1"
    )
    assert request.input_hash("signal_generation/v1") != request.input_hash(
        "signal_generation/v3"
    )


def test_daily_candidate_range_runs_to_last_session_end() -> None:
    window = candidate_range(BASE_MOVE)
    assert window.start == BASE_MOVE.window_start
    assert window.end == datetime(2026, 10, 20, 11, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="INTRADAY_SPIKE"):
        _ = candidate_range(replace(BASE_MOVE, move_type="INTRADAY_SPIKE"))


def test_update_value_for_hidden_has_no_generation_method() -> None:
    generation, _ = _generate([503, 503], candidates("D1"))
    value = update_value(BASE_MOVE, 3, generation, NOW)
    assert value["status"] == "HIDDEN"
    assert value["generation_method"] is None
    assert value["headline_label"] is None
    assert value["causes"] == []
    assert value["version"] == 3
    assert value["move_version"] == 1
    assert value["move_window_end"] == BASE_MOVE.window_end


def test_signal_worker_and_retry_runner_are_registered() -> None:
    assert ("signal_worker", "signal-requests") in [
        (spec.name, spec.topic) for spec in CONSUMERS
    ]
    assert {"signal_worker", "signal_retry"} <= set(runner_names())
    assert [source.source_type for source in CANDIDATE_SOURCES] == [
        "DOCUMENT",
        "EVENT",
    ]


@dataclass
class FakeProducer:
    sent: list[tuple[str, bytes | None, bytes | None, list[tuple[str, bytes]]]] = field(
        default_factory=list
    )

    def produce(
        self,
        topic: str,
        *,
        value: bytes | None = None,
        key: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> None:
        self.sent.append((topic, value, key, headers or []))

    def flush(self, timeout: float) -> int:
        _ = timeout
        return 0


def test_handler_failure_goes_to_dlq_with_error_headers_and_commits() -> None:
    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        _ = (message, context)
        raise RuntimeError("boom")

    spec = ConsumerSpec(
        "signal_worker", "signal-requests", handler, dlq_topic="signal-dlq"
    )
    consumer = FakeConsumer()
    producer = FakeProducer()
    loop = ConsumerLoop(
        spec,
        consumer=consumer,
        deserializer=FakeDeserializer(),
        serializer=FakeSerializer(),
        session_factory=FakeSessionFactory().as_factory(),
        max_attempts=2,
        retry_backoff_seconds=0,
        dlq_producer=producer,
    )
    message = fake_message(topic="signal-requests", headers=[("id", b"evt-1")])
    asyncio.run(loop.process(message))

    ((topic, value, key, headers),) = producer.sent
    assert (topic, value, key) == ("signal-dlq", b"payload", b"000660")
    named = dict(headers)
    assert named["id"] == b"evt-1"
    assert named["error.class"] == b"RuntimeError"
    assert named["error.message"] == b"boom"
    assert named["error.offset"] == b"7"
    assert len(consumer.commits) == 1


def test_dlq_topic_requires_a_producer() -> None:
    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        _ = (message, context)

    with pytest.raises(ValueError, match="dlq_producer"):
        _ = ConsumerLoop(
            ConsumerSpec("x", "t", handler, dlq_topic="dlq"),
            consumer=FakeConsumer(),
            deserializer=FakeDeserializer(),
            serializer=FakeSerializer(),
            session_factory=FakeSessionFactory().as_factory(),
        )


def test_prompt_lists_every_output_field() -> None:
    from ai_signal.prompts import load_prompt
    from ai_signal.signals.generation import SignalGenerationOutput

    schema = json.loads(json.dumps(SignalGenerationOutput.model_json_schema()))
    for version in ("v3",):
        text = load_prompt("signal_generation", version).text
        for name in schema["properties"]:
            assert f"`{name}`" in text


def _current(as_of: datetime = WINDOW_END) -> CurrentSignal:
    return CurrentSignal(
        version=1,
        headline_label="HBM 공급 확대",
        as_of_event_time=as_of,
        causes=(CurrentCause("HBM 공급 확대", "기존 설명이에요.", (UUID(int=1),)),),
    )


def _keep_request(found: CandidateSet) -> GenerationRequest:
    return GenerationRequest(
        BASE_MOVE, found, _current(), WINDOW_END + timedelta(hours=1)
    )


def _found_with_new() -> CandidateSet:
    return CandidateSet(
        documents=(
            document("D1"),
            document("D2", WINDOW_END + timedelta(minutes=30)),
            document("D3", WINDOW_END + timedelta(hours=2)),
        )
    )


def test_prompt_input_marks_new_candidates_and_current_signal() -> None:
    value = _keep_request(_found_with_new()).prompt_input()
    assert [d["is_new"] for d in value["documents"]] == [False, True, False]
    assert value["current_signal"] == {
        "headline_label": "HBM 공급 확대",
        "causes": [
            {
                "label": "HBM 공급 확대",
                "explanation": "기존 설명이에요.",
                "refs": ["D1"],
            }
        ],
    }
    assert (
        GenerationRequest(BASE_MOVE, _found_with_new()).prompt_input()["current_signal"]
        == "none"
    )


def test_input_hash_ignores_current_signal() -> None:
    found = _found_with_new()
    assert _keep_request(found).input_hash("v") == GenerationRequest(
        BASE_MOVE, found
    ).input_hash("v")


def test_keep_with_empty_causes_is_accepted() -> None:
    generator, llm = fake_generator(
        [output_json([], decision="KEEP", reason="같아요.")]
    )
    generation = asyncio.run(generator.generate(_keep_request(_found_with_new())))
    assert generation.decision.keep
    assert generation.decision.reason == "같아요."
    assert not generation.decision.guard_applied
    assert llm.calls == 1


def test_keep_without_current_signal_is_a_schema_failure() -> None:
    generator, llm = fake_generator([output_json([], decision="KEEP"), output_json()])
    generation = asyncio.run(
        generator.generate(GenerationRequest(BASE_MOVE, candidates("D1")))
    )
    assert generation.decision.status == "SHOWN"
    assert not generation.decision.keep
    assert generation.attempts[0].error is LlmErrorKind.SCHEMA
    assert llm.calls == 2


def test_effect_mismatch_is_a_schema_failure() -> None:
    mismatch = output_json([cause(label="경쟁사 칭찬 발언", effect="NEGATIVE")])
    generator, llm = fake_generator([mismatch, output_json()])
    generation = asyncio.run(
        generator.generate(GenerationRequest(BASE_MOVE, candidates("D1")))
    )
    assert generation.decision.status == "SHOWN"
    assert generation.attempts[0].error is LlmErrorKind.SCHEMA
    assert llm.calls == 2


def test_update_citing_no_new_candidate_becomes_keep() -> None:
    generator, _ = fake_generator([output_json([cause(refs=["D1"])])])
    generation = asyncio.run(generator.generate(_keep_request(_found_with_new())))
    assert generation.decision.keep
    assert generation.decision.guard_applied


def test_update_citing_a_new_candidate_is_shown() -> None:
    generator, _ = fake_generator([output_json([cause(refs=["D2"])])])
    generation = asyncio.run(generator.generate(_keep_request(_found_with_new())))
    assert not generation.decision.keep
    assert generation.decision.status == "SHOWN"
