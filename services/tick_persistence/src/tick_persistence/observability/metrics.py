"""Prometheus golden-signal metrics for tick_persistence.

Each ``TickMetrics`` owns its own ``CollectorRegistry`` so multiple instances
(e.g. across tests) never collide on the global registry. Consumer/handler
call-sites are guarded by ``if metrics is not None`` so wiring is additive.

Cardinality is bounded by design: ``partition`` labels span only the 3 Kafka
partitions and ``symbol`` labels only the ~40 subscribed stocks.
"""
from __future__ import annotations

import logging
import threading
from wsgiref.simple_server import WSGIServer

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    start_http_server,
)

logger = logging.getLogger(__name__)

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_BATCH_SIZE_BUCKETS = (1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0)


class TickMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry if registry is not None else CollectorRegistry()

        self.consumed_total = Counter(
            "tick_persistence_consumed_total",
            "Kafka messages consumed from the topic (per partition).",
            ["partition"],
            registry=self.registry,
        )
        self.inserted_total = Counter(
            "tick_persistence_inserted_total",
            "Ticks actually inserted into bronze.tick_history (per partition).",
            ["partition"],
            registry=self.registry,
        )
        self.conflict_total = Counter(
            "tick_persistence_conflict_total",
            "Valid ticks skipped as duplicate event_id conflicts (per partition).",
            ["partition"],
            registry=self.registry,
        )
        self.quarantined_total = Counter(
            "tick_persistence_quarantined_total",
            "Poison-pill ticks routed to bronze.tick_quarantine (per partition).",
            ["partition"],
            registry=self.registry,
        )
        self.skipped_total = Counter(
            "tick_persistence_skipped_total",
            "Messages skipped before the handler (deserialize fail/tombstone, per partition).",
            ["partition"],
            registry=self.registry,
        )
        self.empty_batches_total = Counter(
            "tick_persistence_empty_batches_total",
            "consume() calls that returned no actionable messages.",
            registry=self.registry,
        )
        self.rebalance_total = Counter(
            "tick_persistence_rebalance_total",
            "Consumer group rebalance callbacks observed.",
            ["event"],
            registry=self.registry,
        )

        self.batch_duration_seconds = Histogram(
            "tick_persistence_batch_duration_seconds",
            "Wall-clock duration of TickHandler.handle_batch.",
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.db_transaction_duration_seconds = Histogram(
            "tick_persistence_db_transaction_duration_seconds",
            "Duration of the per-batch DB transaction (bronze+silver+serving).",
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.commit_duration_seconds = Histogram(
            "tick_persistence_commit_duration_seconds",
            "Duration of synchronous Kafka offset commits.",
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.consume_batch_size = Histogram(
            "tick_persistence_consume_batch_size",
            "Number of messages returned by each consume() poll.",
            buckets=_BATCH_SIZE_BUCKETS,
            registry=self.registry,
        )

        self.handle_batch_failures_total = Counter(
            "tick_persistence_handle_batch_failures_total",
            "handle_batch invocations that raised (batch not committed).",
            registry=self.registry,
        )
        self.commit_failures_total = Counter(
            "tick_persistence_commit_failures_total",
            "Kafka offset commit attempts that raised.",
            registry=self.registry,
        )

        self.consumer_lag = Gauge(
            "tick_persistence_consumer_lag",
            "Consumer group lag (high watermark - position) per partition.",
            ["partition"],
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "tick_persistence_queue_depth",
            "Pending batches in the internal backpressure queue.",
            registry=self.registry,
        )
        self.committed_offset = Gauge(
            "tick_persistence_committed_offset",
            "Last successfully committed offset per partition.",
            ["partition"],
            registry=self.registry,
        )

        self.snapshot_staleness_seconds = Gauge(
            "tick_persistence_snapshot_staleness_seconds",
            "serving.symbol_snapshot staleness = now() - last_event_ts, per symbol.",
            ["symbol"],
            registry=self.registry,
        )

        self.reconciliation_imbalance = Gauge(
            "tick_persistence_reconciliation_imbalance",
            "consumed - (inserted + conflict + quarantine + skip) per partition; 0 == balanced.",
            ["partition"],
            registry=self.registry,
        )

        try:
            ProcessCollector(registry=self.registry)
            PlatformCollector(registry=self.registry)
        except Exception as exc:  # pragma: no cover
            logger.debug("RSS/CPU collectors register but no-op off Linux (no /proc): %s", exc)

    def set_consumer_lag(self, partition: int, lag: int) -> None:
        self.consumer_lag.labels(partition=str(partition)).set(lag)

    def set_committed_offset(self, partition: int, offset: int) -> None:
        self.committed_offset.labels(partition=str(partition)).set(offset)

    def set_queue_depth(self, depth: int) -> None:
        self.queue_depth.set(depth)

    def set_snapshot_staleness(self, symbol: str, staleness_seconds: float) -> None:
        self.snapshot_staleness_seconds.labels(symbol=symbol).set(staleness_seconds)

    def set_reconciliation_imbalance(self, partition: int, imbalance: int) -> None:
        self.reconciliation_imbalance.labels(partition=str(partition)).set(imbalance)


def start_metrics_server(
    metrics: TickMetrics, port: int, addr: str = "0.0.0.0"
) -> tuple[WSGIServer, threading.Thread]:
    """Start the exposition server on a daemon thread; caller stops via ``server.shutdown()``."""
    server, thread = start_http_server(port, addr=addr, registry=metrics.registry)
    logger.info("metrics server listening on %s:%s/metrics", addr, server.server_address[1])
    return server, thread
