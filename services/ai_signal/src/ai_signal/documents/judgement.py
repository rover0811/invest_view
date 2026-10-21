"""Document judgement (6.4): direction, scope and fail reason of one headline."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from ai_signal.llm import (
    FallbackOutcome,
    FallbackStep,
    Llm,
    LlmAttempt,
    ModelStep,
    route_step_names,
    run_fallback,
)
from ai_signal.prompts import Prompt, load_prompt

PURPOSE = "document_judgement"

Direction = Literal[-1, 0, 1]
Scope = Literal["MARKET", "SECTOR", "SYMBOL"]
FailReason = Literal["LISTING_ONLY", "AD", "NO_EVIDENCE"]
InfoType = Literal["NEW_FACT", "PRICE_RECAP", "OPINION", "FLOW", "OTHER"]
EventType = Literal[
    "CONTRACT",
    "EARNINGS",
    "INVESTMENT",
    "TECH_ANNOUNCEMENT",
    "PRODUCT",
    "PERSONNEL",
    "POLICY",
    "REGULATION",
    "LEGAL",
    "CAPITAL",
    "SCHEDULE",
    "OTHER",
]
TargetRelevance = Literal["DIRECT", "COMPETITOR", "SUPPLY_CHAIN", "SECTOR", "UNRELATED"]
INFO_TYPES: tuple[str, ...] = get_args(InfoType)
EVENT_TYPES: tuple[str, ...] = get_args(EventType)
TARGET_RELEVANCES: tuple[str, ...] = get_args(TargetRelevance)


class DocumentJudgementOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Direction
    scope: Scope
    fail_reason: FailReason | None
    probability: float = Field(ge=0, le=1)
    info_type: InfoType
    event_subject: str
    event_type: EventType
    event_object: str
    target_relevance: TargetRelevance

    @property
    def is_signal_candidate(self) -> bool:
        return self.fail_reason is None


@dataclass(frozen=True)
class Candidate:
    ticker: str
    name: str
    sectors: tuple[str, ...]


@dataclass(frozen=True)
class JudgementRequest:
    title: str
    publisher: str | None
    published_at: datetime
    candidates: tuple[Candidate, ...]

    def as_json(self) -> str:
        body = asdict(self)
        body["published_at"] = self.published_at.isoformat()
        return json.dumps(body, ensure_ascii=False)


@dataclass(frozen=True)
class Judgement:
    output: DocumentJudgementOutput
    decided_by: str
    model_version: str | None
    prompt_version: str
    attempts: tuple[LlmAttempt, ...] = ()

    @property
    def llm_attempts(self) -> list[dict[str, str | None]] | None:
        return [a.as_json() for a in self.attempts] if self.attempts else None


def judgement_messages(
    prompt: Prompt,
) -> Callable[[JudgementRequest], list[dict[str, str]]]:
    def build(request: JudgementRequest) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": request.as_json()},
        ]

    return build


def default_steps(
    llm: Llm, prompt: Prompt
) -> tuple[FallbackStep[JudgementRequest, DocumentJudgementOutput], ...]:
    messages = judgement_messages(prompt)
    routes = llm.routes_for(PURPOSE)
    return tuple(
        ModelStep(
            name=name,
            llm=llm,
            route=route,
            output_type=DocumentJudgementOutput,
            messages=messages,
        )
        for name, route in zip(route_step_names(routes), routes, strict=True)
    )


class DocumentJudge:
    """Runs the fallback steps; ``None`` means every step passed (not judged)."""

    def __init__(
        self,
        steps: Sequence[FallbackStep[JudgementRequest, DocumentJudgementOutput]],
        prompt: Prompt,
    ) -> None:
        self._steps = tuple(steps)
        self._prompt = prompt

    @classmethod
    def from_llm(cls, llm: Llm) -> DocumentJudge:
        prompt = load_prompt(PURPOSE)
        return cls(default_steps(llm, prompt), prompt)

    async def judge(self, request: JudgementRequest) -> Judgement | None:
        outcome: FallbackOutcome[DocumentJudgementOutput] = await run_fallback(
            self._steps, request
        )
        if outcome.result is None:
            return None
        return Judgement(
            output=outcome.result.value,
            decided_by=outcome.result.decided_by,
            model_version=outcome.result.model_version,
            prompt_version=self._prompt.prompt_version,
            attempts=tuple(outcome.failures.attempts),
        )
