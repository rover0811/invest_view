from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_signal.documents.judgement import DocumentJudgementOutput
from ai_signal.prompts import PromptNotFoundError, active_version, load_prompt


def test_default_document_judgement_prompt_is_v4() -> None:
    assert active_version("document_judgement", {}) == "v4"


def test_document_judgement_v3_prompt_names_every_output_field() -> None:
    prompt = load_prompt("document_judgement", "v3")
    assert prompt.prompt_version == "document_judgement/v3"
    for field in DocumentJudgementOutput.model_fields:
        assert f"`{field}`" in prompt.text


def test_default_signal_generation_prompt_is_v2() -> None:
    assert active_version("signal_generation", {}) == "v2"
    prompt = load_prompt("signal_generation", environ={})
    assert prompt.version == "v2"
    assert prompt.prompt_version == "signal_generation/v2"


def test_unknown_purpose_falls_back_to_v1() -> None:
    assert active_version("unknown_purpose", {}) == "v1"
    assert (
        active_version("unknown_purpose", {"AI_SIGNAL_PROMPT_UNKNOWN_PURPOSE": " "})
        == "v1"
    )


def test_environment_overrides_per_purpose_default() -> None:
    environ = {"AI_SIGNAL_PROMPT_SIGNAL_GENERATION": "v1"}
    assert active_version("signal_generation", environ) == "v1"
    assert load_prompt("signal_generation", environ=environ).prompt_version == (
        "signal_generation/v1"
    )
    assert active_version("document_judgement", environ) == "v4"


def test_version_comes_from_environment() -> None:
    environ = {"AI_SIGNAL_PROMPT_DOCUMENT_JUDGEMENT": "v9"}
    assert active_version("document_judgement", environ) == "v9"
    with pytest.raises(PromptNotFoundError):
        _ = load_prompt("document_judgement", environ=environ)


@pytest.mark.parametrize("version", ["../registry", "latest", "v1.md"])
def test_rejects_non_version_names(version: str) -> None:
    with pytest.raises(PromptNotFoundError):
        _ = load_prompt("document_judgement", version=version)


def test_output_schema_is_strict_and_revalidates() -> None:
    schema = DocumentJudgementOutput.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "direction",
        "scope",
        "fail_reason",
        "probability",
        "info_type",
        "event_subject",
        "event_type",
        "event_object",
        "target_relevance",
    }
    parsed = DocumentJudgementOutput.model_validate_json(
        '{"direction": -1, "scope": "SECTOR", "fail_reason": null, "probability": 0.8,'
        ' "info_type": "PRICE_RECAP", "event_subject": "KOSPI", "event_type": "OTHER",'
        ' "event_object": "", "target_relevance": "SECTOR"}'
    )
    assert parsed.is_signal_candidate
    with pytest.raises(ValidationError):
        _ = DocumentJudgementOutput.model_validate_json(
            '{"direction": 2, "scope": "SECTOR", "fail_reason": null, "probability": 0.8}'
        )
    with pytest.raises(ValidationError):
        _ = DocumentJudgementOutput.model_validate_json(
            '{"direction": 1, "scope": "SECTOR", "fail_reason": null, "probability": 0.8,'
            ' "info_type": "RUMOR", "event_subject": "x", "event_type": "OTHER",'
            ' "event_object": "", "target_relevance": "SECTOR"}'
        )
