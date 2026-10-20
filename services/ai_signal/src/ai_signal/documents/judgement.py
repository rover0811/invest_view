"""Document judgement (6.4): direction, scope and fail reason of one headline."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_signal.llm import (
    FallbackOutcome,
    FallbackStep,
    LlmAttempt,
    ModelSpec,
    ModelStep,
    OpenRouterClient,
    primary_spec,
    run_fallback,
    secondary_spec,
)
from ai_signal.llm.client import json_schema_text
from ai_signal.prompts import Prompt, load_prompt

PURPOSE = "document_judgement"

Direction = Literal[-1, 0, 1]
Scope = Literal["MARKET", "SECTOR", "SYMBOL"]
FailReason = Literal["LISTING_ONLY", "AD", "NO_EVIDENCE"]


class DocumentJudgementOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Direction
    scope: Scope
    fail_reason: FailReason | None
    probability: float = Field(ge=0, le=1)

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
) -> Callable[[JudgementRequest, ModelSpec], list[dict[str, str]]]:
    def build(request: JudgementRequest, spec: ModelSpec) -> list[dict[str, str]]:
        system = prompt.text
        if spec.response_mode == "json_object":
            schema = json_schema_text(DocumentJudgementOutput)
            system = (
                f"{system}\nThe JSON object must match this JSON schema:\n{schema}\n"
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": request.as_json()},
        ]

    return build


def default_steps(
    client: OpenRouterClient, prompt: Prompt
) -> tuple[FallbackStep[JudgementRequest, DocumentJudgementOutput], ...]:
    messages = judgement_messages(prompt)
    return (
        ModelStep(
            name="primary",
            client=client,
            spec=primary_spec(client.settings),
            output_type=DocumentJudgementOutput,
            messages=messages,
        ),
        ModelStep(
            name="secondary",
            client=client,
            spec=secondary_spec(client.settings),
            output_type=DocumentJudgementOutput,
            messages=messages,
        ),
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
    def from_client(cls, client: OpenRouterClient) -> DocumentJudge:
        prompt = load_prompt(PURPOSE)
        return cls(default_steps(client, prompt), prompt)

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
