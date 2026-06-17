from __future__ import annotations

from tick_persistence.aggregation.ohlc import FiveMinuteAggregator
from tick_persistence.config import TickPersistenceConfig
from tick_persistence.db.session import create_engine, create_session_factory
from tick_persistence.handler import TickHandler
from tick_persistence.kafka.consumer import TickConsumer
from tick_persistence.observability import FreshnessMonitor, ReconciliationLedger, TickMetrics
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.quarantine import QuarantineRepository
from tick_persistence.repository.snapshot import SnapshotRepository
from tick_persistence.repository.tick_history import TickHistoryRepository


class Container:
    def __init__(self, config: TickPersistenceConfig) -> None:
        self.config = config
        self.engine = create_engine(config.database_url)
        self.session_factory = create_session_factory(self.engine)

        self.metrics = TickMetrics()
        self.ledger = ReconciliationLedger(self.metrics)

        self.tick_history_repo = TickHistoryRepository()
        self.snapshot_repo = SnapshotRepository()
        self.metrics_repo = Metrics5mRepository()
        self.quarantine_repo = QuarantineRepository()
        self.aggregator = FiveMinuteAggregator()

        self.handler = TickHandler(
            session_factory=self.session_factory,
            tick_history_repo=self.tick_history_repo,
            snapshot_repo=self.snapshot_repo,
            metrics_repo=self.metrics_repo,
            aggregator=self.aggregator,
            quarantine_repo=self.quarantine_repo,
            metrics=self.metrics,
            ledger=self.ledger,
        )
        self.consumer = TickConsumer(
            config=config,
            on_message=self.handler.handle_batch,
            on_revoke=self.handler.clear_state,
            metrics=self.metrics,
            ledger=self.ledger,
        )
        self.freshness_monitor = FreshnessMonitor(
            self.session_factory,
            self.metrics,
            interval_seconds=config.freshness_refresh_interval_seconds,
        )
