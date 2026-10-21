from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from ai_signal.consumers import ConsumedMessage
from ai_signal.llm import Llm, Route
from ai_signal.llm.adapters.fake import FakeAdapter
from ai_signal.llm.container import LlmSettings
from ai_signal.prompts import load_prompt
from ai_signal.signals.candidates import CandidateSet, SignalCandidate
from ai_signal.signals.generation import SignalGenerator, default_steps
from ai_signal.signals.move import Contributions, OpenMove

MOVE_ID = UUID("00000000-0000-5000-8000-000000000001")
WINDOW_START = datetime(2026, 10, 19, 6, 30, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 20, 1, 5, tzinfo=UTC)
NOW = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)


def cause(
    rank: int = 1,
    refs: Sequence[str] = ("D1",),
    label: str = "HBM 공급 확대",
    explanation: str = "엔비디아향 HBM 공급이 늘어난다는 소식에 매수세가 몰렸어요.",
) -> dict[str, object]:
    return {
        "rank": rank,
        "label": label,
        "explanation": explanation,
        "scope": "SYMBOL",
        "document_ids": list(refs),
        "event_ids": [],
    }


def output_json(
    causes: Sequence[dict[str, object]] | None = None, headline: int = 1
) -> str:
    return json.dumps(
        {
            "headline_cause_rank": headline,
            "causes": list(causes) if causes is not None else [cause()],
            "probability": 0.7,
        },
        ensure_ascii=False,
    )


FakeLlm = FakeAdapter


def fake_llm(adapter: FakeAdapter) -> Llm:
    routes = tuple(
        Route("fake", route.model) for route in LlmSettings().default_routes()
    )
    return Llm(
        adapters={"fake": adapter},
        routes={"signal_generation": routes, "document_judgement": routes},
    )


def fake_generator(replies: Sequence[str | int]) -> tuple[SignalGenerator, FakeLlm]:
    llm = FakeAdapter(list(replies))
    prompt = load_prompt("signal_generation", environ={})
    return SignalGenerator(default_steps(fake_llm(llm), prompt), prompt), llm


BASE_MOVE = OpenMove(
    move_id=MOVE_ID,
    move_type="DAILY",
    symbol="000660",
    symbol_name="SK하이닉스",
    symbol_entity_id=UUID("00000000-0000-5000-8000-000000000002"),
    signal_enabled=True,
    window_start=WINDOW_START,
    window_end=WINDOW_END,
    return_pct=Decimal("3.1"),
    price_state="POSITIVE",
    contributions=Contributions(
        Decimal("0.4"), Decimal("0.9"), Decimal("0.3"), Decimal("1.5"), "IDIO"
    ),
    version=1,
)


def document(ref: str = "D1", published_at: datetime = WINDOW_END) -> SignalCandidate:
    return SignalCandidate(
        ref=ref,
        source_type="DOCUMENT",
        source_id=UUID(int=int(ref[1:])),
        source_version=1,
        title=f"SK하이닉스 HBM 공급 확대 {ref}",
        publisher="뉴스",
        published_at=published_at,
        scope="SYMBOL",
        timing="LEADING",
    )


def candidates(*refs: str) -> CandidateSet:
    return CandidateSet(documents=tuple(document(ref) for ref in refs))


def request_message(
    symbol: str = "000660",
    offset: int = 1,
    reason: str = "NEW_DOCUMENT",
    event_time: datetime = NOW,
) -> ConsumedMessage:
    return ConsumedMessage(
        topic="signal-requests",
        partition=0,
        offset=offset,
        key=symbol,
        value={"symbol": symbol, "reason": reason, "event_time": event_time},
        headers={},
        timestamp_ms=None,
    )
