"""Versioned prompt files (18.5).

``prompts/<purpose>/<version>.md`` is never edited after release; a change adds
a new version file. The active version comes from
``AI_SIGNAL_PROMPT_<PURPOSE>`` (for example ``AI_SIGNAL_PROMPT_DOCUMENT_JUDGEMENT=v2``)
and result tables record ``Prompt.prompt_version`` such as ``document_judgement/v1``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources

FALLBACK_VERSION = "v1"
DEFAULT_VERSIONS: Mapping[str, str] = {
    "signal_generation": "v2",
    "document_judgement": "v1",
}
_VERSION = re.compile(r"^v[0-9]+$")


class PromptNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class Prompt:
    purpose: str
    version: str
    text: str

    @property
    def prompt_version(self) -> str:
        return f"{self.purpose}/{self.version}"


def env_name(purpose: str) -> str:
    return f"AI_SIGNAL_PROMPT_{purpose.upper()}"


def default_version(purpose: str) -> str:
    return DEFAULT_VERSIONS.get(purpose, FALLBACK_VERSION)


def active_version(purpose: str, environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    default = default_version(purpose)
    return env.get(env_name(purpose), default).strip() or default


def load_prompt(
    purpose: str,
    version: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Prompt:
    resolved = version if version is not None else active_version(purpose, environ)
    if not _VERSION.match(resolved):
        raise PromptNotFoundError(f"invalid prompt version {resolved!r} for {purpose}")
    resource = resources.files("ai_signal.prompts").joinpath(purpose, f"{resolved}.md")
    if not resource.is_file():
        raise PromptNotFoundError(f"prompt {purpose}/{resolved} does not exist")
    return Prompt(
        purpose=purpose, version=resolved, text=resource.read_text(encoding="utf-8")
    )
