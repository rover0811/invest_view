"""document_judgement/v3 오프라인 실험: 자유 문장 event 대신 구조화된 사건 필드와 대상 관련성으로 트리거를 줄인다.

입력: 리플레이 내보내기 JSON, document_judgement/v3 프롬프트.
캐시: /tmp/sigana/<prompt>_<model>.jsonl (프롬프트 버전과 모델별, 문서 id 기준). --prompt-version으로 프롬프트를 고른다(기본 v3).
실행:
    AI_SIGNAL_LLM_API_KEY=$(security find-generic-password -s cgv-gemini-api-key -w) \
        services/ai_signal/.venv/bin/python experiments/signal_trigger/judgement_v3.py [--model M] [--compare M2]
모델은 --model, 환경 변수 V3_MODEL, 기본값 gemini-3.5-flash-lite 순서로 정한다.
--routes "gemini:M1,openrouter:M2"(또는 V3_ROUTES)를 주면 라우팅 계층으로 판정한다. QUOTA_EXHAUSTED나
차단된 경로는 다음 경로로 넘어가고, 결과는 실제로 판정한 모델의 캐시에 adapter 필드와 함께 쓴다.
분석은 경로 순서대로 각 모델 캐시를 합쳐서 한다. 키는 GEMINI_API_KEY, OPENROUTER_API_KEY 환경 변수로 준다.
RATE_LIMIT가 연속 MAX_CONSECUTIVE_RATE_LIMIT회를 넘으면 판정을 멈추고 종료 코드 3을 돌려준다.
API 키가 없으면 캐시만으로 분석한다. 요약은 /tmp/sigana/v3_summary.md에 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_signal.llm import CompletionRequest, LlmError, LlmErrorKind
from ai_signal.llm.adapters.openai_compatible import json_schema_text
from ai_signal.llm.adapters.openrouter import OpenRouterAdapter, OpenRouterSettings
from ai_signal.llm.container import build_llm
from ai_signal.llm.routing import Llm, Route, parse_routes
ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "frontend/public/replay/000660-2026-10-14.json"
ENV_FILE = Path("/tmp/replay-state/llm.env")
STATE_DIRECTION = {"POSITIVE": 1, "NEGATIVE": -1, "NEUTRAL": 0}
PROMPT_DIR = ROOT / "services/ai_signal/src/ai_signal/prompts/document_judgement"
PROMPT_VERSION = "v3"
PROMPT = PROMPT_DIR / f"{PROMPT_VERSION}.md"
CACHE_DIR = Path("/tmp/sigana")
MODEL = os.environ.get("V3_MODEL", "gemini-3.5-flash-lite")
CALL_LOG = CACHE_DIR / f"{PROMPT_VERSION}_calls.log"
SUMMARY = CACHE_DIR / f"{PROMPT_VERSION}_summary.md"
MAX_CONSECUTIVE_RATE_LIMIT = 20
ROUTES: tuple[Route, ...] = ()


def cache_path(model: str) -> Path:
    return CACHE_DIR / f"{PROMPT_VERSION}_{model.replace('/', '_')}.jsonl"


class RateLimitExhausted(RuntimeError):
    pass


_consecutive_rate_limit = 0

CANDIDATES = [{"ticker": "000660", "name": "SK하이닉스", "sectors": ["반도체"]}]
ALIASES = {
    "SK하이닉스": ["하이닉스", "하닉", "닉스", "SK hynix", "000660"],
    "삼성전자": ["삼전", "삼성", "Samsung Electronics", "005930"],
    "마이크론": ["Micron", "마이크론테크놀로지"],
    "창신메모리(CXMT)": ["CXMT", "창신메모리", "창신메모리테크놀로지", "ChangXin Memory"],
    "창장메모리(YMTC)": ["YMTC", "창장메모리", "양쯔메모리", "Yangtze Memory"],
    "한미반도체": ["Hanmi Semiconductor"],
    "ASML": ["에이에스엠엘"],
    "어플라이드머티어리얼즈": ["Applied Materials", "AMAT"],
    "도쿄일렉트론": ["Tokyo Electron", "TEL"],
    "솔리다임": ["Solidigm"],
}
RELATIONS = {
    "competitors": ["삼성전자", "마이크론", "창신메모리(CXMT)"],
    "suppliers": ["한미반도체", "ASML", "어플라이드머티어리얼즈", "도쿄일렉트론"],
    "subsidiaries": ["솔리다임"],
}
CONCURRENCY = 1
MAX_ATTEMPTS = 8
PROB_MIN = 0.7
COOLDOWN = timedelta(minutes=10)
TRIGGER_RELEVANCE = {"DIRECT", "COMPETITOR"}
EXCLUDED_EVENT_TYPES = {"SCHEDULE", "OTHER"}

EventType = Literal[
    "CONTRACT", "EARNINGS", "INVESTMENT", "TECH_ANNOUNCEMENT", "PRODUCT", "PERSONNEL",
    "POLICY", "REGULATION", "LEGAL", "CAPITAL", "SCHEDULE", "OTHER",
]


class V3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal[-1, 0, 1]
    scope: Literal["MARKET", "SECTOR", "SYMBOL"]
    fail_reason: Literal["LISTING_ONLY", "AD", "NO_EVIDENCE"] | None
    probability: float = Field(ge=0, le=1)
    info_type: Literal["NEW_FACT", "PRICE_RECAP", "OPINION", "FLOW", "OTHER"]
    event_subject: str
    event_type: EventType
    event_object: str
    target_relevance: Literal["DIRECT", "COMPETITOR", "SUPPLY_CHAIN", "SECTOR", "UNRELATED"]


@dataclass
class Doc:
    id: str
    title: str
    publisher: str
    published_at: datetime
    v3: V3 | None = None
    event: int | None = None
    source: str | None = None


def load_env() -> None:
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_export() -> tuple[dict[str, Any], list[Doc]]:
    data = json.loads(EXPORT.read_text())
    docs = [
        Doc(d["id"], d["title"], d["publisher"], datetime.fromisoformat(d["published_at"]))
        for d in data["documents"]
    ]
    docs.sort(key=lambda d: d.published_at)
    return data, docs


def read_cache_rows(model: str) -> dict[str, dict[str, Any]]:
    path = cache_path(model)
    rows: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            rows[row["id"]] = row
    return rows


def merged_cache() -> dict[str, tuple[V3, str]]:
    """경로 순서대로 모델 캐시를 합친다. 값은 (판정, adapter:model)."""
    out: dict[str, tuple[V3, str]] = {}
    for route in ROUTES or (Route("openrouter", MODEL),):
        for doc_id, row in read_cache_rows(route.model).items():
            if doc_id not in out:
                adapter = row.get("adapter", route.adapter_name if ROUTES else "legacy")
                out[doc_id] = (V3.model_validate(row["output"]), f"{adapter}:{route.model}")
    return out


def read_cache(model: str) -> dict[str, V3]:
    out: dict[str, V3] = {}
    path = cache_path(model)
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            out[row["id"]] = V3.model_validate(row["output"])
    return out


def messages(doc: Doc, prompt: str) -> list[dict[str, str]]:
    user = {
        "title": doc.title,
        "publisher": doc.publisher,
        "published_at": doc.published_at.isoformat(),
        "candidates": CANDIDATES,
        "aliases": ALIASES,
        "relations": RELATIONS,
    }
    system = f"{prompt}\nThe JSON object must match this JSON schema:\n{json_schema_text(V3)}\n"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def log_call(doc_id: str, status: str, where: str | None = None) -> None:
    with CALL_LOG.open("a") as f:
        f.write(f"{datetime.now().isoformat()}\t{doc_id}\t{status}\t{where or MODEL}\n")


def write_cache(model: str, row: dict[str, Any]) -> None:
    with cache_path(model).open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def judge_routed(llm: Llm, doc: Doc, prompt: str, lock: asyncio.Lock) -> None:
    """경로를 순서대로 시도한다. RATE_LIMIT는 같은 경로에서 기다렸다가 다시 시도한다."""
    global _consecutive_rate_limit
    for route in ROUTES:
        if llm.health.blocked(route) is not None:
            continue
        port = llm.port(route)
        for attempt in range(MAX_ATTEMPTS):
            try:
                result = await port.complete(llm.request(route, messages(doc, prompt)), V3)
            except LlmError as exc:
                log_call(doc.id, exc.kind.value, str(route))
                llm.health.record_failure(route, exc)
                if exc.kind == LlmErrorKind.QUOTA_EXHAUSTED or llm.health.blocked(route) is not None:
                    print(f"route {route} {exc.kind.value}; falling through", flush=True)
                    break
                if exc.kind == LlmErrorKind.RATE_LIMIT:
                    _consecutive_rate_limit += 1
                    if _consecutive_rate_limit > MAX_CONSECUTIVE_RATE_LIMIT:
                        raise RateLimitExhausted(str(route)) from exc
                    wait = exc.retry_after or min(120.0, 5.0 * 2**attempt)
                else:
                    wait = 2.0 * (attempt + 1)
                await asyncio.sleep(wait + random.random())
                continue
            llm.health.record_success(route)
            log_call(doc.id, "OK", str(route))
            _consecutive_rate_limit = 0
            doc.v3, doc.source = result.output, str(route)
            async with lock:
                write_cache(route.model, {
                    "id": doc.id, "adapter": route.adapter_name, "model": result.model_version,
                    "output": result.output.model_dump(),
                })
            return
    print(f"gave up {doc.id} {doc.title[:40]}", flush=True)


async def judge_one(client: OpenRouterAdapter, doc: Doc, prompt: str, lock: asyncio.Lock) -> None:
    global _consecutive_rate_limit
    model = MODEL
    for attempt in range(MAX_ATTEMPTS):
        try:
            result = await client.complete(
                CompletionRequest(model=model, messages=messages(doc, prompt)), V3
            )
        except LlmError as exc:
            log_call(doc.id, exc.kind.value)
            if exc.kind == LlmErrorKind.RATE_LIMIT:
                _consecutive_rate_limit += 1
                if _consecutive_rate_limit > MAX_CONSECUTIVE_RATE_LIMIT:
                    raise RateLimitExhausted(MODEL) from exc
                wait = exc.retry_after or min(120.0, 5.0 * 2**attempt)
            else:
                wait = 2.0 * (attempt + 1)
            await asyncio.sleep(wait + random.random())
            continue
        log_call(doc.id, "OK")
        _consecutive_rate_limit = 0
        doc.v3 = result.output
        async with lock:
            with cache_path(MODEL).open("a") as f:
                row = {"id": doc.id, "model": result.model_version, "output": result.output.model_dump()}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return
    print(f"gave up {doc.id} {doc.title[:40]}")


async def judge_all(docs: list[Doc]) -> None:
    cached = merged_cache()
    for d in docs:
        if d.id in cached:
            d.v3, d.source = cached[d.id]
    todo = [d for d in docs if d.v3 is None]
    if not todo:
        return
    if ROUTES:
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
            print(f"no API key; {len(todo)} documents stay unjudged")
            return
        os.environ["AI_SIGNAL_LLM_ROUTES_DOCUMENT_JUDGEMENT"] = ",".join(map(str, ROUTES))
        llm = build_llm()
        prompt = PROMPT.read_text()
        lock = asyncio.Lock()
        try:
            for d in todo:
                await judge_routed(llm, d, prompt, lock)
                print(f"judged {sum(x.v3 is not None for x in docs)}/{len(docs)} via {d.source}", flush=True)
        finally:
            for port in llm.adapters.values():
                close = getattr(port, "aclose", None)
                if close is not None:
                    await close()
        return
    if not os.environ.get("AI_SIGNAL_LLM_API_KEY"):
        print(f"no API key; {len(todo)} documents stay unjudged")
        return
    client = OpenRouterAdapter(OpenRouterSettings(max_concurrency=CONCURRENCY))
    prompt = PROMPT.read_text()
    lock = asyncio.Lock()
    queue: asyncio.Queue[Doc] = asyncio.Queue()
    for d in todo:
        queue.put_nowait(d)

    async def worker() -> None:
        while not queue.empty():
            d = queue.get_nowait()
            await judge_one(client, d, prompt, lock)
            print(f"judged {sum(x.v3 is not None for x in docs)}/{len(docs)}", flush=True)

    try:
        await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))
    finally:
        await client.aclose()


@dataclass
class Event:
    id: int
    key: tuple[str, ...]
    objects: list[str] = field(default_factory=list)
    docs: list[Doc] = field(default_factory=list)


def identity(v: V3) -> tuple[str, ...]:
    """사건 식별자: (event_subject, event_type). FLOW는 direction도 포함한다. event_object는 표시에만 쓴다."""
    key: tuple[str, ...] = (v.event_subject.strip(), v.event_type)
    if v.info_type == "FLOW":
        key += (str(v.direction),)
    return key


def group_events(docs: list[Doc]) -> list[Event]:
    events: list[Event] = []
    by_key: dict[tuple[str, ...], Event] = {}
    for d in docs:
        v = d.v3
        if v is None:
            continue
        key = identity(v)
        e = by_key.get(key)
        if e is None:
            e = by_key[key] = Event(len(events), key)
            events.append(e)
        if v.event_object not in e.objects:
            e.objects.append(v.event_object)
        e.docs.append(d)
        d.event = e.id
    return events


@dataclass
class Trigger:
    at: datetime
    reason: str
    state: str
    docs: list[Doc] = field(default_factory=list)


def eligible(d: Doc, direction: int) -> bool:
    v = d.v3
    return (
        v is not None
        and direction != 0
        and v.info_type == "NEW_FACT"
        and v.target_relevance in TRIGGER_RELEVANCE
        and v.event_type not in EXCLUDED_EVENT_TYPES
        and v.probability >= PROB_MIN
        and v.direction == direction
        and d.event is not None
    )


def simulate(data: dict[str, Any], docs: list[Doc]) -> list[Trigger]:
    states = [(datetime.fromisoformat(s["t"]), s["state"]) for s in data["states"]]
    day_end = max(d.published_at for d in docs) + timedelta(minutes=1)
    triggers: list[Trigger] = []
    for i, (start, state) in enumerate(states):
        end = states[i + 1][0] if i + 1 < len(states) else day_end
        direction = STATE_DIRECTION[state]
        prior = [d for d in docs if d.published_at < start and eligible(d, direction)]
        reflected = {d.event for d in prior}
        triggers.append(Trigger(start, "PRICE_STATE_CHANGE", state, prior))
        last = start
        pending: list[Doc] = []
        for d in docs:
            if not (start <= d.published_at < end):
                continue
            if pending and last + COOLDOWN <= d.published_at:
                triggers.append(Trigger(last + COOLDOWN, "NEW_DOCUMENT", state, pending))
                last, pending = last + COOLDOWN, []
            if not eligible(d, direction) or d.event in reflected:
                continue
            reflected.add(d.event)
            if d.published_at - last >= COOLDOWN:
                triggers.append(Trigger(d.published_at, "NEW_DOCUMENT", state, [d]))
                last = d.published_at
            else:
                pending.append(d)
        if pending and last + COOLDOWN < end:
            triggers.append(Trigger(last + COOLDOWN, "NEW_DOCUMENT", state, pending))
    return triggers


def md(text: str) -> str:
    return text.replace("|", "\\|")


def summary(docs: list[Doc], events: list[Event], triggers: list[Trigger]) -> str:
    judged = [d for d in docs if d.v3]
    sources = Counter(d.source or "unknown" for d in judged)
    out = [f"model {MODEL} routes {','.join(map(str, ROUTES)) or '-'} judged {len(judged)}/{len(docs)}",
           "sources: " + ", ".join(f"{k} {n}" for k, n in sources.most_common()), ""]
    for name in ("info_type", "target_relevance", "event_type"):
        c = Counter(str(getattr(d.v3, name)) for d in judged if d.v3)
        out.append(f"{name}: " + ", ".join(f"{k} {n}" for k, n in c.most_common()))
    out += ["", "## multi-doc events"]
    for e in sorted(events, key=lambda e: -len(e.docs)):
        if len(e.docs) > 1:
            out.append(f"- E{e.id} {' / '.join(e.key)} / {' ; '.join(e.objects)} ({len(e.docs)})")
    out += ["", "## keyword docs"]
    for d in judged:
        if d.v3 and re.search(r"SEDEX|반도체대전|기조연설|CXMT|창신|YMTC|창장", d.title):
            v = d.v3
            out.append(f"- {d.published_at:%H:%M} E{d.event} {md(d.title[:60])} | {v.info_type} "
                       f"{v.event_subject}/{v.event_type}/{v.event_object} {v.target_relevance}")
    out += ["", "## triggers"]
    for t in triggers:
        titles = "; ".join(f"E{d.event} {md(d.title[:40])}" for d in t.docs) if t.reason == "NEW_DOCUMENT" else f"{len(t.docs)} prior"
        out.append(f"- {t.at:%H:%M} {t.reason} {t.state}: {titles}")
    out += ["", "## NEW_FACT + DIRECT/COMPETITOR"]
    for d in judged:
        v = d.v3
        if v and v.info_type == "NEW_FACT" and v.target_relevance in TRIGGER_RELEVANCE:
            out.append(f"- {d.published_at:%H:%M} {md(d.title[:70])} | {v.target_relevance} {v.event_type} "
                       f"{v.event_subject}/{v.event_object} d{v.direction} p{v.probability:.2f}")
    return "\n".join(out) + "\n"


AGREEMENT_FIELDS = ("info_type", "target_relevance", "event_subject", "event_type", "direction")


def agreement(docs: list[Doc], other_model: str) -> str:
    other = read_cache(other_model)
    pairs = [(d.v3, other[d.id]) for d in docs if d.v3 and d.id in other]
    out = [f"## agreement {MODEL} vs {other_model} on {len(pairs)} docs"]
    for name in AGREEMENT_FIELDS:
        same = sum(str(getattr(a, name)).strip() == str(getattr(b, name)).strip() for a, b in pairs)
        out.append(f"- {name}: {same}/{len(pairs)} ({same / max(1, len(pairs)):.0%})")
    labels = ["NEW_FACT", "PRICE_RECAP", "OPINION", "FLOW", "OTHER"]
    conf = Counter((b.info_type, a.info_type) for a, b in pairs)
    out += ["", f"info_type confusion (rows {other_model}, cols {MODEL})",
            "| | " + " | ".join(labels) + " |", "|" + "---|" * (len(labels) + 1)]
    for r in labels:
        out.append(f"| {r} | " + " | ".join(str(conf[(r, c)]) for c in labels) + " |")
    return "\n".join(out) + "\n"


def main() -> tuple[dict[str, Any], list[Doc], str | None]:
    global MODEL, ROUTES, PROMPT_VERSION, PROMPT, CALL_LOG, SUMMARY
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--compare", default=None)
    parser.add_argument("--routes", default=os.environ.get("V3_ROUTES", ""))
    parser.add_argument("--no-call", action="store_true")
    parser.add_argument("--prompt-version", default=PROMPT_VERSION)
    args = parser.parse_args()
    PROMPT_VERSION = args.prompt_version
    PROMPT = PROMPT_DIR / f"{PROMPT_VERSION}.md"
    CALL_LOG = CACHE_DIR / f"{PROMPT_VERSION}_calls.log"
    SUMMARY = CACHE_DIR / f"{PROMPT_VERSION}_summary.md"
    MODEL = args.model
    if args.routes:
        ROUTES = parse_routes(args.routes)
        MODEL = ROUTES[0].model
    else:
        load_env()
    if args.no_call:
        for key in ("AI_SIGNAL_LLM_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
            os.environ.pop(key, None)
    data, docs = load_export()
    try:
        asyncio.run(judge_all(docs))
    except RateLimitExhausted:
        print(f"RATE_LIMIT exceeded {MAX_CONSECUTIVE_RATE_LIMIT} in a row for {MODEL}; cache kept")
        sys.exit(3)
    return data, docs, args.compare


if __name__ == "__main__":
    data, docs, compare = main()
    events = group_events(docs)
    triggers = simulate(data, docs)
    text = summary(docs, events, triggers)
    if compare:
        text += "\n" + agreement(docs, compare)
    SUMMARY.write_text(text)
    calls = Counter(line.split("\t")[2] for line in CALL_LOG.read_text().splitlines()) if CALL_LOG.exists() else Counter()
    print(f"events {len(events)} NEW_DOCUMENT {sum(t.reason == 'NEW_DOCUMENT' for t in triggers)} calls {dict(calls)}")
