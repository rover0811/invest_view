from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai_signal.consumers import ConsumedMessage
from ai_signal.documents.judgement import (
    DocumentJudge,
    DocumentJudgementOutput,
    JudgementRequest,
)
from ai_signal.documents.news import news_idempotency_key
from ai_signal.llm import StepFailures, StepResult
from ai_signal.prompts import load_prompt

PUBLISHED_AT = datetime(2026, 10, 19, 0, 30, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 10, 19, 0, 31, tzinfo=UTC)


def news_value(
    *,
    symbol: str = "000660",
    source_doc_id: str = "2026101900001",
    title: str = "SK하이닉스, 3분기 영업이익 사상 최대",
    published_at: datetime = PUBLISHED_AT,
    observed_at: datetime = OBSERVED_AT,
) -> dict[str, object]:
    return {
        "raw_id": "ignored-by-processor",
        "idempotency_key": news_idempotency_key(symbol, source_doc_id, title),
        "symbol": symbol,
        "source_doc_id": source_doc_id,
        "title": title,
        "provider_code": "2",
        "category_code": "01",
        "published_at": published_at,
        "observed_at": observed_at,
        "received_at": observed_at,
        "source_payload": {
            "cntt_usiq_srno": source_doc_id,
            "hts_pbnt_titl_cntt": title,
            "dorg": "연합뉴스",
        },
    }


def news_message(
    *,
    symbol: str = "000660",
    source_doc_id: str = "2026101900001",
    title: str = "SK하이닉스, 3분기 영업이익 사상 최대",
    published_at: datetime = PUBLISHED_AT,
) -> ConsumedMessage:
    value = news_value(
        symbol=symbol,
        source_doc_id=source_doc_id,
        title=title,
        published_at=published_at,
    )
    return ConsumedMessage(
        topic="news-raw",
        partition=0,
        offset=1,
        key=str(value["symbol"]),
        value=value,
        headers={},
        timestamp_ms=None,
    )


_SUBJECTS = itertools.count(1)


@dataclass
class ScriptedStep:
    outputs: list[DocumentJudgementOutput | None]
    requests: list[JudgementRequest] = field(default_factory=list)
    name: str = "scripted"

    async def attempt(
        self, request: JudgementRequest, failures: StepFailures
    ) -> StepResult[DocumentJudgementOutput] | None:
        _ = failures
        self.requests.append(request)
        output = self.outputs.pop(0) if self.outputs else None
        if output is None:
            return None
        return StepResult(value=output, decided_by="llm", model_version="fake/model")


def judgement(
    direction: int = 1,
    scope: str = "SYMBOL",
    fail_reason: str | None = None,
    probability: float = 0.87654,
    info_type: str = "NEW_FACT",
    event_subject: str | None = None,
    event_type: str = "CONTRACT",
    event_object: str = "공급 계약",
    target_relevance: str = "DIRECT",
) -> DocumentJudgementOutput:
    """``event_subject`` defaults to a fresh value so identities never repeat."""
    return DocumentJudgementOutput.model_validate(
        {
            "direction": direction,
            "scope": scope,
            "fail_reason": fail_reason,
            "probability": probability,
            "info_type": info_type,
            "event_subject": event_subject or f"subject-{next(_SUBJECTS)}",
            "event_type": event_type,
            "event_object": event_object,
            "target_relevance": target_relevance,
        }
    )


def scripted_judge(
    outputs: Sequence[DocumentJudgementOutput | None],
) -> tuple[DocumentJudge, ScriptedStep]:
    step = ScriptedStep(list(outputs))
    return DocumentJudge([step], load_prompt("document_judgement", "v3")), step
