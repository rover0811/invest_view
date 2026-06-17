# pyright: reportPrivateUsage=false, reportArgumentType=false
from __future__ import annotations

import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
import yaml

from tick_persistence.aggregation.ohlc import FiveMinuteAggregator
from tick_persistence.db.models import SymbolSnapshot
from tick_persistence.handler import TickHandler
from tick_persistence.kafka.consumer import TickConsumer, TickMessage
from tick_persistence.observability import (
    FreshnessMonitor,
    ReconciliationLedger,
    TickMetrics,
    start_metrics_server,
)
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.quarantine import QuarantineRepository
from tick_persistence.repository.snapshot import SnapshotRepository
from tick_persistence.repository.tick_history import TickHistoryRepository

_ROOT = Path(__file__).resolve().parents[1]
_ALERTS = _ROOT / "docs" / "alerts.yaml"
_SLO_DOC = _ROOT / "docs" / "observability-slo.md"

_EXPECTED_METRIC_NAMES = (
    "tick_persistence_consumed_total",
    "tick_persistence_inserted_total",
    "tick_persistence_conflict_total",
    "tick_persistence_quarantined_total",
    "tick_persistence_skipped_total",
    "tick_persistence_batch_duration_seconds",
    "tick_persistence_db_transaction_duration_seconds",
    "tick_persistence_commit_duration_seconds",
    "tick_persistence_handle_batch_failures_total",
    "tick_persistence_commit_failures_total",
    "tick_persistence_consumer_lag",
    "tick_persistence_queue_depth",
    "tick_persistence_committed_offset",
    "tick_persistence_rebalance_total",
    "tick_persistence_snapshot_staleness_seconds",
    "tick_persistence_reconciliation_imbalance",
)


def _tick_value(*, symbol: str = "005930", price: int = 70000, trade_time: str, cumulative_volume: int) -> dict[str, Any]:
    return {
        "source_tr_id": "H0STCNT0",
        "market": "KRX",
        "received_at": "2026-06-01T00:00:01+00:00",
        "symbol": symbol,
        "business_date": "20260601",
        "trade_time": trade_time,
        "price": price,
        "trade_type": "2",
        "trade_volume": 1,
        "vwap": Decimal(str(price)),
        "change": price - 70000,
        "change_rate": Decimal("1.23"),
        "change_sign": "2",
        "cumulative_volume": cumulative_volume,
        "trade_strength": Decimal("105.50"),
        "vi_trigger_price": 71000,
        "trading_halted": "0",
    }


def _message(value: dict[str, Any], *, offset: int, partition: int = 0) -> TickMessage:
    return TickMessage(value=value, topic="stock-ticks", partition=partition, offset=offset, headers={})


def _mock_kafka_message(*, partition: int, offset: int) -> MagicMock:
    msg = MagicMock()
    msg.partition.return_value = partition
    msg.offset.return_value = offset
    msg.topic.return_value = "stock-ticks"
    return msg


def test_metrics_endpoint_exposes_golden_signals_over_http():
    metrics = TickMetrics()
    metrics.set_consumer_lag(0, 5)
    metrics.set_snapshot_staleness("005930", 1.2)
    metrics.set_queue_depth(0)
    metrics.set_committed_offset(0, 42)
    metrics.set_reconciliation_imbalance(0, 0)
    metrics.batch_duration_seconds.observe(0.05)
    metrics.db_transaction_duration_seconds.observe(0.02)
    metrics.commit_duration_seconds.observe(0.01)
    metrics.inserted_total.labels(partition="0").inc(3)
    metrics.quarantined_total.labels(partition="0").inc(1)
    metrics.consumed_total.labels(partition="0").inc(4)
    metrics.skipped_total.labels(partition="0").inc(1)
    metrics.conflict_total.labels(partition="0").inc(0)
    metrics.handle_batch_failures_total.inc(0)
    metrics.commit_failures_total.inc(0)
    metrics.rebalance_total.labels(event="assign").inc()

    server, _thread = start_metrics_server(metrics, 0, "127.0.0.1")
    try:
        port = server.server_address[1]
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
    finally:
        server.shutdown()

    for name in _EXPECTED_METRIC_NAMES:
        assert name in body, f"missing metric {name} in /metrics output"
    assert 'tick_persistence_consumer_lag{partition="0"} 5.0' in body
    assert 'tick_persistence_snapshot_staleness_seconds{symbol="005930"}' in body


def test_consumer_records_consumed_and_skip_into_ledger():
    with (
        patch("tick_persistence.kafka.consumer.Consumer"),
        patch("tick_persistence.kafka.consumer.SchemaRegistryClient"),
        patch("tick_persistence.kafka.consumer.AvroDeserializer"),
        patch("tick_persistence.kafka.consumer.Path") as mock_path,
    ):
        mock_path.return_value.read_text.return_value = '{"type":"record","name":"X","fields":[]}'
        config = MagicMock()
        config.kafka_topic = "stock-ticks"
        config.poll_timeout = 0.01
        config.batch_size = 500
        config.max_poll_interval_ms = 300_000
        metrics = TickMetrics()
        ledger = ReconciliationLedger(metrics)
        consumer = TickConsumer(config, MagicMock(), metrics=metrics, ledger=ledger)

    valid_tm = _message(_tick_value(trade_time="090000", cumulative_volume=1), offset=10)
    batch = [
        (_mock_kafka_message(partition=0, offset=10), valid_tm),
        (_mock_kafka_message(partition=0, offset=11), None),
        (_mock_kafka_message(partition=1, offset=5), valid_tm),
    ]
    consumer._record_reconciliation(batch)

    assert ledger.snapshot()[0]["consumed"] == 2
    assert ledger.snapshot()[0]["skip"] == 1
    assert ledger.snapshot()[1]["consumed"] == 1
    assert ledger.snapshot()[1]["skip"] == 0


def test_ledger_detects_imbalance_and_sets_gauge():
    metrics = TickMetrics()
    ledger = ReconciliationLedger(metrics)
    ledger.record_consumed(2, 10)
    ledger.record_inserted(2, 6)
    ledger.record_conflict(2, 2)
    ledger.record_quarantine(2, 1)

    imbalance = ledger.verify()

    assert imbalance[2] == 1
    gauge = metrics.registry.get_sample_value(
        "tick_persistence_reconciliation_imbalance", {"partition": "2"}
    )
    assert gauge == 1.0


def test_alerts_file_defines_required_slo_rules():
    data = yaml.safe_load(_ALERTS.read_text())
    rules = {rule["alert"]: rule for group in data["groups"] for rule in group["rules"]}

    required = (
        "ConsumerLagHigh",
        "SnapshotStale",
        "EndToEndFreshnessCritical",
        "TickPersistenceOOMKilled",
        "TickPersistenceRestartsHigh",
        "TickPersistenceDown",
        "TickPersistenceMetricsAbsent",
        "ReconciliationImbalance",
        "PoisonPillSpike",
        "BatchFailureSpike",
    )
    for alert in required:
        assert alert in rules, f"missing alert rule {alert}"
        rule = rules[alert]
        assert rule["expr"].strip()
        assert "runbook_url" in rule["annotations"]
        assert "action" in rule["annotations"]

    assert "1000" in rules["ConsumerLagHigh"]["expr"]
    assert rules["ConsumerLagHigh"]["for"] == "3m"
    assert "> 5" in rules["EndToEndFreshnessCritical"]["expr"]
    assert "> 3" in rules["SnapshotStale"]["expr"]
    assert "OOMKilled" in rules["TickPersistenceOOMKilled"]["expr"]
    assert "restarts_total" in rules["TickPersistenceRestartsHigh"]["expr"]
    assert "kafka_topic_partition_current_offset" in rules["EndToEndFreshnessCritical"]["expr"]
    assert "!= 0" in rules["ReconciliationImbalance"]["expr"]


def test_slo_doc_states_thresholds_and_reconciliation_invariant():
    text = _SLO_DOC.read_text()
    assert "/metrics" in text
    assert "9090" in text
    assert "5s" in text
    assert "3s" in text
    assert ("1,000" in text) or ("1000" in text)
    assert "error budget" in text.lower()
    assert "consumed == inserted + conflict + quarantine + skip" in text


@pytest.mark.qa
async def test_freshness_monitor_updates_staleness_gauge(db_session_factory):
    metrics = TickMetrics()
    monitor = FreshnessMonitor(db_session_factory, metrics, interval_seconds=0.01)

    stale_event_ts = datetime.now(timezone.utc) - timedelta(seconds=12)
    async with db_session_factory() as session, session.begin():
        await session.execute(
            sa.insert(SymbolSnapshot).values(symbol="005930", last_price=70000, last_event_ts=stale_event_ts)
        )

    staleness = await monitor.refresh_once()

    assert "005930" in staleness
    assert staleness["005930"] >= 11
    gauge = metrics.registry.get_sample_value(
        "tick_persistence_snapshot_staleness_seconds", {"symbol": "005930"}
    )
    assert gauge is not None and gauge >= 11


@pytest.mark.qa
async def test_reconciliation_balance_holds_across_consumer_and_handler(db_session_factory):
    metrics = TickMetrics()
    ledger = ReconciliationLedger(metrics)
    handler = TickHandler(
        session_factory=db_session_factory,
        tick_history_repo=TickHistoryRepository(),
        snapshot_repo=SnapshotRepository(),
        metrics_repo=Metrics5mRepository(),
        aggregator=FiveMinuteAggregator(),
        quarantine_repo=QuarantineRepository(),
        metrics=metrics,
        ledger=ledger,
    )

    new0 = _tick_value(trade_time="090000", cumulative_volume=1_000_001)
    new1 = _tick_value(trade_time="090001", cumulative_volume=1_000_002)
    new2 = _tick_value(trade_time="090002", cumulative_volume=1_000_003)
    bad = _tick_value(trade_time="090003", cumulative_volume=1_000_004)
    del bad["symbol"]

    def _consumer_records(tuples):
        for msg, tm in tuples:
            partition = msg.partition()
            ledger.record_consumed(partition, 1)
            if tm is None:
                ledger.record_skip(partition, 1)

    batch0 = [
        _message(new0, offset=0),
        _message(new1, offset=1),
        _message(new2, offset=2),
        _message(bad, offset=3),
    ]
    _consumer_records([(_mock_kafka_message(partition=0, offset=m.offset), m) for m in batch0])
    await handler.handle_batch(batch0)

    _consumer_records([(_mock_kafka_message(partition=0, offset=4), None)])

    dup = [_message(new0, offset=5), _message(new1, offset=6)]
    _consumer_records([(_mock_kafka_message(partition=0, offset=m.offset), m) for m in dup])
    await handler.handle_batch(dup)

    imbalance = ledger.verify()
    snapshot = ledger.snapshot()[0]

    assert imbalance[0] == 0
    assert snapshot["consumed"] == 7
    assert snapshot["inserted"] == 3
    assert snapshot["conflict"] == 2
    assert snapshot["quarantine"] == 1
    assert snapshot["skip"] == 1
    assert snapshot["consumed"] == (
        snapshot["inserted"] + snapshot["conflict"] + snapshot["quarantine"] + snapshot["skip"]
    )
