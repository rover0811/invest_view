"""Signal generation (12.2, 12.4): one LLM call per request, fallback as a step list."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_signal.llm import (
    FallbackStep,
    LlmAttempt,
    LlmErrorKind,
    ModelSpec,
    ModelStep,
    OpenRouterClient,
    StepFailures,
    StepResult,
    primary_spec,
    run_fallback,
    secondary_spec,
)
from ai_signal.llm.client import json_schema_text
from ai_signal.prompts import Prompt, load_prompt
from ai_signal.signals.candidates import CandidateSet, SignalCandidate
from ai_signal.signals.move import OpenMove

PURPOSE = "signal_generation"
NONE = "none"
SignalStatus = Literal["SHOWN", "HIDDEN", "NO_CANDIDATE"]
Scope = Literal["MARKET", "SECTOR", "SYMBOL"]


class SignalCauseOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(
        ge=1, le=3, description="원인 순위. 1이 가장 강한 원인이고 서로 겹치지 않습니다"
    )
    label: str = Field(
        min_length=1,
        description="원인을 나타내는 짧은 한국어 명사구. 끝에 조사를 붙이지 않고, 숫자와 종목 이름을 쓰지 않습니다",
    )
    explanation: str = Field(
        min_length=1,
        description="배경, 원인, 이 종목에 준 영향 순서의 해요체 한 문장. '요.'로 끝나고 등락률 숫자를 쓰지 않습니다",
    )
    scope: Scope = Field(description="원인의 범위")
    document_ids: list[str] = Field(description="이 원인의 근거 기사 ref 목록(예: D1)")
    event_ids: list[str] = Field(
        description="이 원인의 근거 이벤트 ref 목록. 없으면 빈 목록"
    )


class SignalGenerationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline_cause_rank: int = Field(
        ge=1, le=3, description="헤드라인에 보여 줄 원인의 rank. 보통 1입니다"
    )
    causes: list[SignalCauseOutput] = Field(
        min_length=1, max_length=3, description="원인 1개에서 3개"
    )
    probability: float = Field(
        ge=0, le=1, description="헤드라인 원인이 움직임을 설명한다고 보는 확신도"
    )


@dataclass(frozen=True)
class GenerationRequest:
    move: OpenMove
    candidates: CandidateSet

    def prompt_input(self) -> dict[str, object]:
        move = self.move
        layers = move.contributions
        decomposition: object = (
            {
                "global": _number(layers.global_),
                "market": _number(layers.market),
                "sector": _number(layers.sector),
                "idio": _number(layers.idio),
                "dominant_layer": layers.dominant_layer,
            }
            if layers is not None
            else NONE
        )
        return {
            "symbol": move.symbol,
            "name": move.symbol_name,
            "move": {
                "move_type": move.move_type,
                "window_start": move.window_start.isoformat(),
                "window_end": move.window_end.isoformat(),
                "return_pct": _number(move.return_pct),
                "price_state": move.price_state,
            },
            "decomposition": decomposition,
            "documents": _candidate_list(self.candidates.documents),
            "events": _candidate_list(self.candidates.events),
        }

    def as_json(self) -> str:
        return json.dumps(self.prompt_input(), ensure_ascii=False)

    def input_hash(self, prompt_version: str) -> str:
        body = {
            "move_id": str(self.move.move_id),
            "move_version": self.move.version,
            "prompt_version": prompt_version,
            "input": self.prompt_input(),
            "sources": [
                [c.ref, str(c.source_id), c.source_version]
                for c in (*self.candidates.documents, *self.candidates.events)
            ],
        }
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()


def _number(value: Decimal) -> float:
    return round(float(value), 6)


def _candidate_list(candidates: Sequence[SignalCandidate]) -> object:
    if not candidates:
        return NONE
    return [
        {
            "ref": c.ref,
            "title": c.title,
            "publisher": c.publisher,
            "published_at": c.published_at.isoformat(),
            "scope": c.scope,
            "timing": c.timing,
        }
        for c in candidates
    ]


@dataclass(frozen=True)
class GeneratedCause:
    rank: int
    label: str
    explanation: str
    scope: Scope
    sources: tuple[SignalCandidate, ...]


@dataclass(frozen=True)
class SignalDecision:
    status: SignalStatus
    headline_cause_rank: int | None = None
    causes: tuple[GeneratedCause, ...] = ()
    probability: float | None = None

    @property
    def headline_label(self) -> str | None:
        for cause in self.causes:
            if cause.rank == self.headline_cause_rank:
                return cause.label
        return None


HIDDEN = SignalDecision(status="HIDDEN")
NO_CANDIDATE = SignalDecision(status="NO_CANDIDATE")


def resolve_output(
    output: SignalGenerationOutput, candidates: CandidateSet
) -> SignalDecision | None:
    """Keep cited refs that are candidates (12.2); ``None`` if nothing usable remains."""
    by_ref = candidates.by_ref()
    causes: list[GeneratedCause] = []
    headline: int | None = None
    seen_ranks: set[int] = set()
    for cause in sorted(output.causes, key=lambda c: c.rank):
        refs = dict.fromkeys((*cause.document_ids, *cause.event_ids))
        sources = tuple(by_ref[ref] for ref in refs if ref in by_ref)
        if not sources or cause.rank in seen_ranks:
            continue
        seen_ranks.add(cause.rank)
        rank = len(causes) + 1
        if cause.rank == output.headline_cause_rank:
            headline = rank
        causes.append(
            GeneratedCause(rank, cause.label, cause.explanation, cause.scope, sources)
        )
    if not causes or headline is None:
        return None
    return SignalDecision(
        status="SHOWN",
        headline_cause_rank=headline,
        causes=tuple(causes),
        probability=output.probability,
    )


LABEL_PARTICLES = (
    "으로",
    "에서",
    "에도",
    "로",
    "에",
    "이",
    "가",
    "은",
    "는",
    "을",
    "를",
    "와",
    "과",
)
EXPLANATION_ENDING = "요."


def cause_wording_ok(cause: GeneratedCause) -> bool:
    """Wording rules the prompt states but the JSON schema cannot enforce (12.2)."""
    if not cause.explanation.strip().endswith(EXPLANATION_ENDING):
        return False
    return not cause.label.strip().endswith(LABEL_PARTICLES)


def wording_ok(decision: SignalDecision) -> bool:
    """One bad cause rejects the whole answer so a partial signal is never shown."""
    return all(cause_wording_ok(cause) for cause in decision.causes)


def signal_messages(
    prompt: Prompt,
) -> Callable[[GenerationRequest, ModelSpec], list[dict[str, str]]]:
    def build(request: GenerationRequest, spec: ModelSpec) -> list[dict[str, str]]:
        system = prompt.text
        if spec.response_mode == "json_object":
            schema = json_schema_text(SignalGenerationOutput)
            system = (
                f"{system}\nThe JSON object must match this JSON schema:\n{schema}\n"
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": request.as_json()},
        ]

    return build


@dataclass(frozen=True)
class CitingModelStep:
    """A ``ModelStep`` whose answer must cite candidates and follow the wording rules.

    Either violation is recorded as a SCHEMA failure so the next step answers.
    """

    model: ModelStep[GenerationRequest, SignalGenerationOutput]

    @property
    def name(self) -> str:
        return self.model.name

    async def attempt(
        self, request: GenerationRequest, failures: StepFailures
    ) -> StepResult[SignalDecision] | None:
        result = await self.model.attempt(request, failures)
        if result is None:
            return None
        decision = resolve_output(result.value, request.candidates)
        if decision is None or not wording_ok(decision):
            failures.reject_last_attempt(self.name, LlmErrorKind.SCHEMA)
            return None
        return StepResult(
            value=decision,
            decided_by=result.decided_by,
            model_version=result.model_version,
        )


@dataclass(frozen=True)
class HiddenStep:
    """Terminal step: hide the signal (12.4 step 5). Always returns a result."""

    name: str = "hidden"

    async def attempt(
        self, request: GenerationRequest, failures: StepFailures
    ) -> StepResult[SignalDecision]:
        _ = (request, failures)
        return StepResult(value=HIDDEN, decided_by=self.name)


def default_steps(
    client: OpenRouterClient, prompt: Prompt
) -> tuple[FallbackStep[GenerationRequest, SignalDecision], ...]:
    messages = signal_messages(prompt)

    def model(name: str, spec: ModelSpec) -> CitingModelStep:
        return CitingModelStep(
            ModelStep(
                name=name,
                client=client,
                spec=spec,
                output_type=SignalGenerationOutput,
                messages=messages,
            )
        )

    return (
        model("primary", primary_spec(client.settings)),
        model("secondary", secondary_spec(client.settings)),
        HiddenStep(),
    )


@dataclass(frozen=True)
class Generation:
    decision: SignalDecision
    decided_by: str | None
    model_version: str | None
    prompt_version: str
    last_error: LlmErrorKind | None
    attempts: tuple[LlmAttempt, ...] = ()

    @property
    def llm_attempts(self) -> list[dict[str, str | None]]:
        return [attempt.as_json() for attempt in self.attempts]

    @property
    def llm_failed(self) -> bool:
        return self.decision.status == "HIDDEN" and self.last_error is not None


class SignalGenerator:
    def __init__(
        self,
        steps: Sequence[FallbackStep[GenerationRequest, SignalDecision]],
        prompt: Prompt,
    ) -> None:
        self._steps = tuple(steps)
        self._prompt = prompt

    @property
    def prompt_version(self) -> str:
        return self._prompt.prompt_version

    @classmethod
    def from_client(cls, client: OpenRouterClient) -> SignalGenerator:
        prompt = load_prompt(PURPOSE)
        return cls(default_steps(client, prompt), prompt)

    async def generate(self, request: GenerationRequest) -> Generation:
        if request.candidates.is_empty:
            return Generation(NO_CANDIDATE, None, None, self.prompt_version, None)
        outcome = await run_fallback(self._steps, request)
        result = outcome.result
        last_error = outcome.failures.last_error
        attempts = tuple(outcome.failures.attempts)
        if result is None or result.value.status != "SHOWN":
            return Generation(
                HIDDEN, None, None, self.prompt_version, last_error, attempts
            )
        return Generation(
            decision=result.value,
            decided_by=result.decided_by,
            model_version=result.model_version,
            prompt_version=self.prompt_version,
            last_error=last_error,
            attempts=attempts,
        )
