"""document_judgement/v2 오프라인 실험: 기사 성격(info_type)과 사건(event)으로 재생성 트리거를 줄인다.

입력: 리플레이 내보내기 JSON, document_judgement/v2 프롬프트.
캐시: /tmp/sigana/v2_judgements.jsonl (문서 id 기준), /tmp/sigana/v2_embeddings.json (event 문자열 기준).
실행:
    AI_SIGNAL_LLM_API_KEY=$(security find-generic-password -s cgv-gemini-api-key -w) \
        services/ai_signal/.venv/bin/python experiments/signal_trigger/judgement_v2.py
API 키가 없으면 캐시만으로 분석한다. REPORT.md의 수동 관찰 절(NOTES 표식 사이)은 다시 실행해도 보존된다.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ai_signal.llm import CompletionRequest, LlmError, LlmErrorKind
from ai_signal.llm.adapters.openai_compatible import json_schema_text
from ai_signal.llm.adapters.openrouter import OpenRouterAdapter, OpenRouterSettings
from ai_signal.llm.container import LlmSettings

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "frontend/public/replay/000660-2026-10-14.json"
PROMPT = ROOT / "services/ai_signal/src/ai_signal/prompts/document_judgement/v2.md"
REPORT = Path(__file__).resolve().parent / "REPORT.md"
ENV_FILE = Path("/tmp/replay-state/llm.env")
CACHE = Path("/tmp/sigana/v2_judgements.jsonl")
EMB_CACHE = Path("/tmp/sigana/v2_embeddings.json")
CALL_LOG = Path("/tmp/sigana/v2_calls.log")

EMBED_MODEL = "gemini-embedding-001"
EMBED_BATCH = 20
CANDIDATES = [{"ticker": "000660", "name": "SK하이닉스", "sectors": ["반도체"]}]
CONCURRENCY = 2
MAX_ATTEMPTS = 8
EVENT_THRESHOLD = 0.88
PROB_MIN = 0.7
COOLDOWN = timedelta(minutes=10)
NOTES_START = "<!-- NOTES:START -->"
NOTES_END = "<!-- NOTES:END -->"

InfoType = Literal["NEW_FACT", "PRICE_RECAP", "OPINION", "FLOW", "OTHER"]
STATE_DIRECTION = {"POSITIVE": 1, "NEGATIVE": -1, "NEUTRAL": 0}


class V2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal[-1, 0, 1]
    scope: Literal["MARKET", "SECTOR", "SYMBOL"]
    fail_reason: Literal["LISTING_ONLY", "AD", "NO_EVIDENCE"] | None
    probability: float = Field(ge=0, le=1)
    info_type: InfoType
    event: str


@dataclass
class Doc:
    id: str
    title: str
    publisher: str
    published_at: datetime
    v2: V2 | None = None
    cluster: int | None = None




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


def read_cache() -> dict[str, V2]:
    out: dict[str, V2] = {}
    if CACHE.exists():
        for line in CACHE.read_text().splitlines():
            row = json.loads(line)
            out[row["id"]] = V2.model_validate(row["output"])
    return out




def messages(doc: Doc, prompt: str) -> list[dict[str, str]]:
    user = {
        "title": doc.title,
        "publisher": doc.publisher,
        "published_at": doc.published_at.isoformat(),
        "candidates": CANDIDATES,
    }
    system = f"{prompt}\nThe JSON object must match this JSON schema:\n{json_schema_text(V2)}\n"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def log_call(doc_id: str, status: str) -> None:
    with CALL_LOG.open("a") as f:
        f.write(f"{datetime.now().isoformat()}\t{doc_id}\t{status}\n")


async def judge_one(client: OpenRouterAdapter, doc: Doc, prompt: str, lock: asyncio.Lock) -> None:
    model = LlmSettings().secondary_model
    for attempt in range(MAX_ATTEMPTS):
        try:
            result = await client.complete(
                CompletionRequest(model=model, messages=messages(doc, prompt)), V2
            )
        except LlmError as exc:
            log_call(doc.id, exc.kind.value)
            if exc.kind == LlmErrorKind.RATE_LIMIT:
                wait = exc.retry_after or min(120.0, 5.0 * 2**attempt)
            else:
                wait = 2.0 * (attempt + 1)
            await asyncio.sleep(wait + random.random())
            continue
        log_call(doc.id, "OK")
        doc.v2 = result.output
        async with lock:
            with CACHE.open("a") as f:
                row = {"id": doc.id, "model": result.model_version, "output": result.output.model_dump()}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return
    print(f"gave up {doc.id} {doc.title[:40]}")


async def judge_all(docs: list[Doc]) -> None:
    cached = read_cache()
    for d in docs:
        d.v2 = cached.get(d.id)
    todo = [d for d in docs if d.v2 is None]
    if not todo:
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
            print(f"judged {sum(x.v2 is not None for x in docs)}/{len(docs)}", flush=True)

    try:
        await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))
    finally:
        await client.aclose()




def embed_events(events: list[str]) -> dict[str, list[float]]:
    cache: dict[str, list[float]] = json.loads(EMB_CACHE.read_text()) if EMB_CACHE.exists() else {}
    missing = sorted({e for e in events if e not in cache})
    key = os.environ.get("AI_SIGNAL_LLM_API_KEY")
    if missing and key:
        base = os.environ["AI_SIGNAL_LLM_BASE_URL"]
        with httpx.Client(base_url=base, timeout=60) as http:
            for i in range(0, len(missing), EMBED_BATCH):
                batch = missing[i : i + EMBED_BATCH]
                for text, vector in zip(batch, embed_batch(http, key, batch), strict=True):
                    cache[text] = vector
                EMB_CACHE.write_text(json.dumps(cache))
    return cache


def embed_batch(http: httpx.Client, key: str, batch: list[str]) -> list[list[float]]:
    for attempt in range(MAX_ATTEMPTS * 2):
        resp = http.post(
            "/embeddings",
            json={"model": EMBED_MODEL, "input": batch},
            headers={"Authorization": f"Bearer {key}"},
        )
        log_call(f"embeddings[{len(batch)}]", "OK" if resp.status_code < 400 else f"EMBED_{resp.status_code}")
        if resp.status_code == 429:
            time.sleep(min(90.0, 10.0 * (attempt + 1)))
            continue
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]
    raise RuntimeError("embedding rate limit did not clear")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def closest_pairs(vectors: dict[str, list[float]], low: float, high: float) -> list[tuple[float, str, str]]:
    keys = sorted(vectors)
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            s = cosine(vectors[a], vectors[b])
            if low <= s < high:
                pairs.append((round(s, 3), a, b))
    return sorted(pairs, reverse=True)


@dataclass
class EventCluster:
    id: int
    events: list[str] = field(default_factory=list)
    docs: list[Doc] = field(default_factory=list)

    @property
    def name(self) -> str:
        return Counter(d.v2.event for d in self.docs if d.v2).most_common(1)[0][0]


def cluster_events(docs: list[Doc], vectors: dict[str, list[float]], threshold: float) -> list[EventCluster]:
    clusters: list[EventCluster] = []
    for d in docs:
        if d.v2 is None or d.v2.event not in vectors:
            continue
        v = vectors[d.v2.event]
        best, best_sim = None, 0.0
        for c in clusters:
            s = max(cosine(v, vectors[e]) for e in c.events)
            if s > best_sim:
                best, best_sim = c, s
        if best is not None and best_sim >= threshold:
            target = best
        else:
            target = EventCluster(len(clusters))
            clusters.append(target)
        if d.v2.event not in target.events:
            target.events.append(d.v2.event)
        target.docs.append(d)
        d.cluster = target.id
    return clusters




@dataclass
class Trigger:
    at: datetime
    reason: str
    state: str
    docs: list[Doc] = field(default_factory=list)


def eligible(d: Doc, direction: int, types: set[str]) -> bool:
    v = d.v2
    return (
        v is not None
        and direction != 0
        and v.info_type in types
        and v.fail_reason is None
        and v.probability >= PROB_MIN
        and v.direction == direction
        and d.cluster is not None
    )


def simulate(data: dict[str, Any], docs: list[Doc], types: set[str]) -> list[Trigger]:
    states = [(datetime.fromisoformat(s["t"]), s["state"]) for s in data["states"]]
    day_end = max(d.published_at for d in docs) + timedelta(minutes=1)
    triggers: list[Trigger] = []
    for i, (start, state) in enumerate(states):
        end = states[i + 1][0] if i + 1 < len(states) else day_end
        direction = STATE_DIRECTION[state]
        prior = [d for d in docs if d.published_at < start and eligible(d, direction, types)]
        reflected = {d.cluster for d in prior}
        triggers.append(Trigger(start, "PRICE_STATE_CHANGE", state, prior))
        last = start
        pending: list[Doc] = []
        for d in docs:
            if not (start <= d.published_at < end):
                continue
            if pending and last + COOLDOWN <= d.published_at:
                triggers.append(Trigger(last + COOLDOWN, "NEW_DOCUMENT", state, pending))
                last, pending = last + COOLDOWN, []
            if not eligible(d, direction, types) or d.cluster in reflected:
                continue
            reflected.add(d.cluster)
            if d.published_at - last >= COOLDOWN:
                triggers.append(Trigger(d.published_at, "NEW_DOCUMENT", state, [d]))
                last = d.published_at
            else:
                pending.append(d)
        if pending and last + COOLDOWN < end:
            triggers.append(Trigger(last + COOLDOWN, "NEW_DOCUMENT", state, pending))
    return triggers




def md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def sample_titles(docs: list[Doc], n: int, seed: int) -> list[Doc]:
    rng = random.Random(seed)
    judged = [d for d in docs if d.v2]
    return sorted(rng.sample(judged, min(n, len(judged))), key=lambda d: d.published_at)


def trigger_rows(triggers: list[Trigger], clusters: list[EventCluster]) -> list[str]:
    rows = ["| 시각 | 사유 | 상태 | 사건 | info_type | 대표 제목 |", "| --- | --- | --- | --- | --- | --- |"]
    for t in triggers:
        if t.reason == "PRICE_STATE_CHANGE":
            names = ", ".join(sorted({clusters[d.cluster].name for d in t.docs if d.cluster is not None})) or "없음"
            rows.append(f"| {t.at:%H:%M} | 상태 변경 | {t.state} | 반영 사건: {md_escape(names)} | | |")
            continue
        for d in t.docs:
            assert d.v2 is not None and d.cluster is not None
            rows.append(
                f"| {t.at:%H:%M} | 새 기사 | {t.state} | {md_escape(clusters[d.cluster].name)} | "
                f"{d.v2.info_type} | {md_escape(d.title)} ({d.published_at:%H:%M}) |"
            )
    return rows


def write_report(
    data: dict[str, Any],
    docs: list[Doc],
    clusters: list[EventCluster],
    triggers: list[Trigger],
    triggers_flow: list[Trigger],
    pairs: list[tuple[float, str, str]],
    calls: Counter[str],
) -> str:
    judged = [d for d in docs if d.v2]
    counts = Counter(d.v2.info_type for d in judged if d.v2)
    lines = [
        "# 기사 성격 분류로 시그널 재생성 트리거 줄이기 (SK하이닉스 2026-10-14 리플레이)",
        "",
        "이 문서는 `experiments/signal_trigger/judgement_v2.py`가 생성합니다. 수동 관찰 절은 다시 실행해도 유지됩니다.",
        "",
        "## 실험 설정",
        "",
        f"- 기사 {len(docs)}건 가운데 {len(judged)}건을 document_judgement/v2 프롬프트와 `{os.environ.get('AI_SIGNAL_LLM_SECONDARY_MODEL', '')}` 모델로 판정했습니다.",
        f"- 사건 이름(`event`)은 `{EMBED_MODEL}`로 임베딩하고, 코사인 유사도 {EVENT_THRESHOLD} 이상이면 같은 사건으로 묶었습니다(단일 연결 탐욕 군집).",
        f"- 트리거 조건: info_type이 NEW_FACT이고, fail_reason이 null이고, probability가 {PROB_MIN} 이상이고, direction이 게시 시점의 가격 상태와 같고, 그 사건이 현재 상태의 시그널에 아직 반영되지 않았으며, 같은 상태의 마지막 생성 뒤로 10분이 지났을 때만 재생성합니다.",
        "- 10분이 지나지 않았을 때 들어온 새 사건은 버리지 않고 모아 두었다가, 마지막 생성에서 10분이 되는 시각에 한 번에 반영합니다.",
        "- 상태 변경(09:02 POSITIVE, 14:11 NEUTRAL, 14:37 NEGATIVE)은 언제나 생성합니다. NEUTRAL 상태에서는 방향이 일치할 수 없으므로 새 기사로 재생성하지 않습니다.",
        f"- LLM 호출 기록: {dict(calls)}",
        "",
        "## info_type 분포",
        "",
        "| info_type | 건수 |",
        "| --- | --- |",
        *[f"| {k} | {counts.get(k, 0)} |" for k in ["NEW_FACT", "PRICE_RECAP", "OPINION", "FLOW", "OTHER"]],
        "",
        "## 유형별 예시 제목 (유형마다 5건)",
        "",
    ]
    for kind in ["NEW_FACT", "PRICE_RECAP", "OPINION", "FLOW", "OTHER"]:
        lines.append(f"### {kind}")
        lines.append("")
        picked = [d for d in judged if d.v2 and d.v2.info_type == kind][:: max(1, counts.get(kind, 1) // 5)][:5]
        lines += [f"- {d.published_at:%m-%d %H:%M} {d.title} (event: {d.v2.event})" for d in picked if d.v2]
        lines.append("")
    lines += [
        "## 사건 군집 (크기 상위 15개)",
        "",
        f"전체 군집 수는 {len(clusters)}개입니다.",
        "",
        "| 크기 | 대표 사건 | 주요 info_type | 첫 게시 | 묶인 사건 이름 예시 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in sorted(clusters, key=lambda c: len(c.docs), reverse=True)[:15]:
        kinds = Counter(d.v2.info_type for d in c.docs if d.v2).most_common(1)[0][0]
        variants = "; ".join(c.events[:4])
        lines.append(f"| {len(c.docs)} | {md_escape(c.name)} | {kinds} | {c.docs[0].published_at:%m-%d %H:%M} | {md_escape(variants)} |")
    lines += [
        "",
        "## 유사도 임계값 근처의 사건 쌍",
        "",
        "임계값을 고르려고 0.80에서 0.92 사이의 쌍을 확인했습니다(상위 25개).",
        "",
        "| 유사도 | 사건 A | 사건 B |",
        "| --- | --- | --- |",
        *[f"| {s} | {md_escape(a)} | {md_escape(b)} |" for s, a, b in pairs[:25]],
        "",
        "## 트리거 시뮬레이션",
        "",
    ]
    actual = [s for s in data["signals"] if s["trigger"] == "NEW_DOCUMENT"]
    sim_new = [t for t in triggers if t.reason == "NEW_DOCUMENT"]
    sim_flow = [t for t in triggers_flow if t.reason == "NEW_DOCUMENT"]
    lines += [
        f"- 실제 리플레이: 새 기사로 인한 재생성 {len(actual)}회, 상태 변경 생성 3회입니다.",
        f"- NEW_FACT만 허용: 새 기사로 인한 재생성 {len(sim_new)}회입니다.",
        f"- NEW_FACT와 FLOW 허용: 새 기사로 인한 재생성 {len(sim_flow)}회입니다.",
        "",
        "### NEW_FACT만 허용한 경우",
        "",
        *trigger_rows(triggers, clusters),
        "",
        "### NEW_FACT와 FLOW를 허용한 경우",
        "",
        *trigger_rows(triggers_flow, clusters),
        "",
        "### 실제 NEW_DOCUMENT 재생성 (55회)",
        "",
        "| 시각 | 헤드라인 문구 |",
        "| --- | --- |",
        *[f"| {s['at'][11:16]} | {md_escape(s['headline_label'] or '')} |" for s in actual],
        "",
        "## 판정 점검용 표본 20건",
        "",
        "| 게시 | 제목 | info_type | dir | p | fail | event |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d in sample_titles(docs, 20, seed=14):
        v = d.v2
        assert v is not None
        lines.append(
            f"| {d.published_at:%m-%d %H:%M} | {md_escape(d.title)} | {v.info_type} | {v.direction} | {v.probability} | {v.fail_reason or ''} | {md_escape(v.event)} |"
        )
    old = REPORT.read_text() if REPORT.exists() else ""
    if NOTES_START in old and NOTES_END in old:
        notes = old[old.index(NOTES_START) : old.index(NOTES_END) + len(NOTES_END)]
    else:
        notes = f"{NOTES_START}\n## 수동 관찰과 권장값\n\n(작성 전)\n{NOTES_END}"
    text = "\n".join(lines) + "\n\n" + notes + "\n"
    REPORT.write_text(text)
    return text


def call_counts() -> Counter[str]:
    if not CALL_LOG.exists():
        return Counter()
    return Counter(line.split("\t")[2] for line in CALL_LOG.read_text().splitlines() if line.count("\t") == 2)


def main() -> tuple[dict[str, Any], list[Doc]]:
    load_env()
    data, docs = load_export()
    asyncio.run(judge_all(docs))
    return data, docs


if __name__ == "__main__":
    data, docs = main()
    results = {d.id: d.v2 for d in docs}
    events = [d.v2.event for d in docs if d.v2]
    vectors = embed_events(events)
    pairs = closest_pairs(vectors, 0.80, 0.92)
    clusters = cluster_events(docs, vectors, EVENT_THRESHOLD)
    triggers = simulate(data, docs, {"NEW_FACT"})
    triggers_flow = simulate(data, docs, {"NEW_FACT", "FLOW"})
    calls = call_counts()
    report = write_report(data, docs, clusters, triggers, triggers_flow, pairs, calls)
    print(Counter(v.info_type for v in results.values() if v))
    print(f"clusters {len(clusters)} sim NEW_FACT {sum(t.reason == 'NEW_DOCUMENT' for t in triggers)} "
          f"with FLOW {sum(t.reason == 'NEW_DOCUMENT' for t in triggers_flow)} calls {dict(calls)}")
