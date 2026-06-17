"""Completeness reconciliation ledger.

Proves zero loss by enforcing, per Kafka partition, the balance invariant:

    consumed == inserted + conflict + quarantine + skip

where every consumed message lands in exactly one downstream category:
inserted (new bronze row), conflict (duplicate event_id), quarantine
(poison-pill) or skip (deserialize fail / tombstone). The consumer records
``consumed``/``skip``; the handler records ``inserted``/``conflict``/
``quarantine`` after its transaction commits. A non-zero imbalance means a
message was consumed (and its offset committed) without being accounted for —
i.e. silent loss — and is surfaced as a warning log plus a Prometheus gauge.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from tick_persistence.observability.metrics import TickMetrics

logger = logging.getLogger(__name__)

_ACCOUNTED = ("inserted", "conflict", "quarantine", "skip")


class ReconciliationLedger:
    def __init__(self, metrics: TickMetrics | None = None) -> None:
        self._metrics = metrics
        self._consumed: dict[int, int] = defaultdict(int)
        self._accounted: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def record_consumed(self, partition: int, count: int = 1) -> None:
        if count <= 0:
            return
        self._consumed[partition] += count
        if self._metrics is not None:
            self._metrics.consumed_total.labels(partition=str(partition)).inc(count)

    def record_inserted(self, partition: int, count: int = 1) -> None:
        self._record_category(partition, "inserted", count)
        if count > 0 and self._metrics is not None:
            self._metrics.inserted_total.labels(partition=str(partition)).inc(count)

    def record_conflict(self, partition: int, count: int = 1) -> None:
        self._record_category(partition, "conflict", count)
        if count > 0 and self._metrics is not None:
            self._metrics.conflict_total.labels(partition=str(partition)).inc(count)

    def record_quarantine(self, partition: int, count: int = 1) -> None:
        self._record_category(partition, "quarantine", count)
        if count > 0 and self._metrics is not None:
            self._metrics.quarantined_total.labels(partition=str(partition)).inc(count)

    def record_skip(self, partition: int, count: int = 1) -> None:
        self._record_category(partition, "skip", count)
        if count > 0 and self._metrics is not None:
            self._metrics.skipped_total.labels(partition=str(partition)).inc(count)

    def _record_category(self, partition: int, category: str, count: int) -> None:
        if count <= 0:
            return
        self._accounted[partition][category] += count

    def imbalance(self, partition: int) -> int:
        accounted = sum(self._accounted[partition].values())
        return self._consumed[partition] - accounted

    def partitions(self) -> set[int]:
        return set(self._consumed) | set(self._accounted)

    def verify(self) -> dict[int, int]:
        result: dict[int, int] = {}
        for partition in self.partitions():
            imbalance = self.imbalance(partition)
            result[partition] = imbalance
            if self._metrics is not None:
                self._metrics.set_reconciliation_imbalance(partition, imbalance)
            if imbalance != 0:
                logger.warning(
                    "reconciliation imbalance partition=%s imbalance=%s consumed=%s accounted=%s",
                    partition,
                    imbalance,
                    self._consumed[partition],
                    dict(self._accounted[partition]),
                )
        return result

    def snapshot(self) -> dict[int, dict[str, int]]:
        view: dict[int, dict[str, int]] = {}
        for partition in self.partitions():
            counts = {category: self._accounted[partition][category] for category in _ACCOUNTED}
            counts["consumed"] = self._consumed[partition]
            counts["imbalance"] = self.imbalance(partition)
            view[partition] = counts
        return view
