"""Build the replay export JSON (contract v1, .sisyphus/replay-contract.md)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ai_signal.replay.bars import Bar

CONTRACT_VERSION = 1

_HANGUL_FIRST = 0xAC00
_HANGUL_LAST = 0xD7A3
_FINAL_CONSONANTS = 28
_FINAL_RIEUL = 8
_DIGIT_READINGS = "영일이삼사오육칠팔구"


def particle(label: str) -> str:
    """Same rule as alert_service.headline.direction_particle (doc 22 15.4)."""
    stripped = label.rstrip()
    if not stripped:
        return "로"
    last = stripped[-1]
    if last.isascii() and last.isdigit():
        last = _DIGIT_READINGS[int(last)]
    code = ord(last)
    if not _HANGUL_FIRST <= code <= _HANGUL_LAST:
        return "로"
    final = (code - _HANGUL_FIRST) % _FINAL_CONSONANTS
    return "로" if final in (0, _FINAL_RIEUL) else "으로"


@dataclass(frozen=True)
class StateRow:
    t: datetime
    state: str
    move_id: str
    move_version: int
    return_pct: float


@dataclass(frozen=True)
class DocumentRow:
    id: str
    title: str
    publisher: str
    published_at: datetime
    judged: bool
    direction: int | None = None
    scope: str | None = None
    fail_reason: str | None = None


@dataclass(frozen=True)
class CauseRow:
    rank: int
    label: str
    explanation: str
    scope: str
    source_document_ids: Sequence[str]


@dataclass(frozen=True)
class SignalRow:
    at: datetime
    move_version: int
    version: int
    status: str
    headline_label: str
    decided_by: str | None
    model: str | None
    prompt_version: str | None
    trigger: str
    causes: Sequence[CauseRow] = field(default_factory=tuple)
    decomposition: Mapping[str, object] | None = None


@dataclass(frozen=True)
class LogEntry:
    t: datetime
    kind: str
    text: str


_KST = ZoneInfo("Asia/Seoul")
_LAYERS = ("global", "market", "sector", "idio")


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError(f"naive datetime in export: {value!r}")
    return value.astimezone(_KST).isoformat(timespec="seconds")


def _decomposition_pct(value: Mapping[str, object] | None) -> dict[str, object] | None:
    """gold.move stores layer contributions as return fractions; the contract uses percent."""
    if value is None:
        return None
    out = dict(value)
    for layer in _LAYERS:
        raw = out.get(layer)
        if isinstance(raw, int | float):
            out[layer] = round(raw * 100, 4)
    return out


def build_export(
    *,
    symbol: str,
    name: str,
    day: date,
    prev_close: int,
    bars: Sequence[Bar],
    states: Sequence[StateRow],
    documents: Sequence[DocumentRow],
    signals: Sequence[SignalRow],
    log: Sequence[LogEntry],
    enter_pct: float = 1.0,
    exit_pct: float = 0.5,
) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "symbol": symbol,
        "name": name,
        "date": day.isoformat(),
        "prev_close": prev_close,
        "thresholds": {"enter_pct": enter_pct, "exit_pct": exit_pct},
        "bars": [{"t": _iso(b.t), "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume} for b in bars],
        "states": [
            {
                "t": _iso(s.t),
                "state": s.state,
                "move_id": s.move_id,
                "move_version": s.move_version,
                "return_pct": s.return_pct,
            }
            for s in states
        ],
        "documents": [
            {
                "id": d.id,
                "title": d.title,
                "publisher": d.publisher,
                "published_at": _iso(d.published_at),
                "judged": d.judged,
                "direction": d.direction,
                "scope": d.scope,
                "fail_reason": d.fail_reason,
            }
            for d in documents
        ],
        "signals": [
            {
                "at": _iso(s.at),
                "move_version": s.move_version,
                "version": s.version,
                "status": s.status,
                "headline_label": s.headline_label,
                "particle": particle(s.headline_label),
                "decided_by": s.decided_by,
                "model": s.model,
                "prompt_version": s.prompt_version,
                "trigger": s.trigger,
                "causes": [
                    {
                        "rank": c.rank,
                        "label": c.label,
                        "explanation": c.explanation,
                        "scope": c.scope,
                        "source_document_ids": list(c.source_document_ids),
                    }
                    for c in s.causes
                ],
                "decomposition": _decomposition_pct(s.decomposition),
            }
            for s in signals
        ],
        "log": [{"t": _iso(e.t), "kind": e.kind, "text": e.text} for e in sorted(log, key=lambda e: e.t)],
    }


def check_no_lookahead(export: Mapping[str, object]) -> None:
    """Raise ValueError when a signal cites a document published after ``at``."""
    documents = export["documents"]
    signals = export["signals"]
    if not isinstance(documents, list) or not isinstance(signals, list):
        raise ValueError("export documents and signals must be lists")
    published = {d["id"]: datetime.fromisoformat(d["published_at"]) for d in documents}
    for signal in signals:
        at = datetime.fromisoformat(signal["at"])
        for cause in signal["causes"]:
            for doc_id in cause["source_document_ids"]:
                if doc_id not in published:
                    raise ValueError(f"signal v{signal['version']} cites unknown document {doc_id}")
                if published[doc_id] > at:
                    raise ValueError(
                        f"lookahead: signal v{signal['version']} at {signal['at']} cites "
                        f"document {doc_id} published {published[doc_id].isoformat()}"
                    )
