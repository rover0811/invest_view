"""새 기사가 시그널을 다시 만들 만한 정보인지 오프라인으로 점수를 매긴다.

입력: 리플레이 내보내기 JSON(분봉, 기사, 시그널)과 기사별 판정 확률 TSV.
실행: python experiments/signal_trigger/analysis.py
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "frontend/public/replay/000660-2026-10-14.json"
JUDGEMENTS = Path("/tmp/sigana/judgements.tsv")

SIMILARITY = 0.3
REACTION_MINUTES = 10
COVERAGE_MINUTES = 60
BRACKET = re.compile(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】")


def normalize(title: str) -> str:
    return re.sub(r"[\s\W_]+", "", BRACKET.sub("", title))


def grams(title: str) -> set[str]:
    text = normalize(title)
    return {text[i : i + 3] for i in range(len(text) - 2)}


def similarity(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


@dataclass
class Doc:
    id: str
    title: str
    publisher: str
    published_at: datetime
    direction: int | None
    scope: str | None
    probability: float | None
    fail_reason: str | None
    grams: set[str] = field(default_factory=set)


@dataclass
class Cluster:
    docs: list[Doc] = field(default_factory=list)

    @property
    def first(self) -> Doc:
        return self.docs[0]


def load() -> tuple[dict, list[Doc]]:
    data = json.loads(EXPORT.read_text())
    probs: dict[str, tuple[float, str]] = {}
    for line in JUDGEMENTS.read_text().splitlines():
        doc_id, prob, _direction, _scope, fail = line.split("\t")
        probs[doc_id] = (float(prob), fail)
    docs = []
    for d in data["documents"]:
        prob, fail = probs.get(d["id"], (None, ""))
        docs.append(
            Doc(
                id=d["id"],
                title=d["title"],
                publisher=d["publisher"],
                published_at=datetime.fromisoformat(d["published_at"]),
                direction=d["direction"],
                scope=d["scope"],
                probability=prob,
                fail_reason=d["fail_reason"] or (fail or None),
                grams=grams(d["title"]),
            )
        )
    docs.sort(key=lambda d: d.published_at)
    return data, docs


def cluster_docs(docs: list[Doc]) -> list[Cluster]:
    clusters: list[Cluster] = []
    for doc in docs:
        best, best_sim = None, 0.0
        for c in clusters:
            s = max(similarity(doc.grams, m.grams) for m in c.docs)
            if s > best_sim:
                best, best_sim = c, s
        if best is not None and best_sim >= SIMILARITY:
            best.docs.append(doc)
        else:
            clusters.append(Cluster([doc]))
    return clusters


def minute_closes(bars: list[dict]) -> dict[datetime, tuple[float, float]]:
    return {datetime.fromisoformat(b["t"]): (b["c"], b["v"]) for b in bars}


def reaction(closes: dict[datetime, tuple[float, float]], at: datetime, sigma: float) -> tuple[float | None, float | None]:
    start = at.replace(second=0)
    if start not in closes:
        return None, None
    end = start + timedelta(minutes=REACTION_MINUTES)
    while end not in closes and end > start:
        end -= timedelta(minutes=1)
    if end == start:
        return None, None
    r = closes[end][0] / closes[start][0] - 1
    return r * 100, (r / sigma if sigma else None)


def ten_minute_sigma(closes: dict[datetime, tuple[float, float]]) -> float:
    times = sorted(closes)
    rets = [
        closes[t + timedelta(minutes=REACTION_MINUTES)][0] / closes[t][0] - 1
        for t in times
        if t + timedelta(minutes=REACTION_MINUTES) in closes
    ]
    return statistics.pstdev(rets)


def score_clusters(data: dict, clusters: list[Cluster]) -> list[dict]:
    closes = minute_closes(data["bars"])
    sigma = ten_minute_sigma(closes)
    rows = []
    for c in clusters:
        first = c.first
        window_end = first.published_at + timedelta(minutes=COVERAGE_MINUTES)
        publishers = {d.publisher for d in c.docs if d.published_at <= window_end}
        probs = [d.probability for d in c.docs if d.probability is not None]
        directions = [d.direction for d in c.docs if d.direction is not None]
        direction = max(set(directions), key=directions.count) if directions else None
        r10, z = reaction(closes, first.published_at, sigma)
        aligned = z is not None and direction not in (None, 0) and (z * direction) > 0
        rows.append(
            {
                "first": first.published_at,
                "size": len(c.docs),
                "publishers": len(publishers),
                "direction": direction,
                "max_prob": max(probs) if probs else None,
                "scope": first.scope,
                "failed": all(d.fail_reason for d in c.docs),
                "r10_pct": r10,
                "z": z,
                "aligned": aligned,
                "title": first.title,
            }
        )
    return rows


def importance(row: dict) -> float:
    if row["failed"] or row["direction"] in (None, 0):
        return 0.0
    coverage = min(row["publishers"], 5) / 5
    prob = row["max_prob"] or 0.0
    reaction_part = min(abs(row["z"]), 3) / 3 if row["aligned"] else 0.0
    return round(0.4 * reaction_part + 0.3 * coverage + 0.3 * prob, 3)


def main() -> None:
    data, docs = load()
    clusters = cluster_docs(docs)
    rows = score_clusters(data, clusters)
    for row in rows:
        row["importance"] = importance(row)
    ranked = sorted(rows, key=lambda r: r["importance"], reverse=True)
    print(f"docs {len(docs)} clusters {len(clusters)}")
    for r in ranked[:25]:
        z = "  -  " if r["z"] is None else f"{r['z']:+.1f}"
        print(
            f"{r['importance']:.2f} {r['first']:%m-%d %H:%M} n={r['size']:<2} pub={r['publishers']} "
            f"dir={r['direction']} p={r['max_prob']} z={z} {'ALIGN' if r['aligned'] else '     '} {r['title'][:46]}"
        )
    return ranked


if __name__ == "__main__":
    ranked = main()
    data, docs = load()
    clusters = cluster_docs(docs)
