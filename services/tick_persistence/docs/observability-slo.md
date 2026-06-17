# tick_persistence Observability & SLO

This service is the systemic fix for the incident where consumer lag reached
~2.28M and the pod OOM-looped for ~5 hours **with nobody noticing**. The root
cause was not throughput — it was the *absence of observability*. This document
defines the golden-signal metrics, the freshness/lag SLIs/SLOs, the error
budget, and the completeness reconciliation invariant.

> Scope boundary: this task exposes a scrape-able `/metrics` endpoint and
> **defines** the alert rules (`docs/alerts.yaml`). The Prometheus / Alertmanager
> / Slack collection + routing pipeline is built by the separate
> `observability-prometheus-slack.md` plan, which consumes these metrics and
> these rule definitions.

## Metrics endpoint

- Prometheus text exposition over HTTP at `GET /metrics`.
- Port is a config knob: `TICK_PERSISTENCE_METRICS_PORT` (default `9090`),
  bind address `TICK_PERSISTENCE_METRICS_ADDR` (default `0.0.0.0`), toggle
  `TICK_PERSISTENCE_METRICS_ENABLED` (default `true`).
- Served on a background daemon thread (`prometheus_client.start_http_server`)
  so the asyncio consume/dispatch loop is never blocked.

## Golden signals (Four Golden Signals — Google SRE)

### Traffic
| Metric | Type | Labels |
|---|---|---|
| `tick_persistence_consumed_total` | counter | partition |
| `tick_persistence_inserted_total` | counter | partition |
| `tick_persistence_conflict_total` | counter | partition |
| `tick_persistence_quarantined_total` | counter | partition |
| `tick_persistence_skipped_total` | counter | partition |
| `tick_persistence_empty_batches_total` | counter | — |

### Latency
| Metric | Type |
|---|---|
| `tick_persistence_batch_duration_seconds` | histogram |
| `tick_persistence_db_transaction_duration_seconds` | histogram |
| `tick_persistence_commit_duration_seconds` | histogram |
| `tick_persistence_consume_batch_size` | histogram |

### Errors
| Metric | Type |
|---|---|
| `tick_persistence_handle_batch_failures_total` | counter |
| `tick_persistence_commit_failures_total` | counter |
| `tick_persistence_quarantined_total` (poison-pill) | counter |

### Saturation
| Metric | Type | Labels |
|---|---|---|
| `tick_persistence_consumer_lag` | gauge | partition |
| `tick_persistence_queue_depth` | gauge | — |
| `tick_persistence_committed_offset` | gauge | partition |
| `tick_persistence_rebalance_total` | counter | event |
| `process_resident_memory_bytes` (RSS) | gauge | — |
| `process_cpu_seconds_total` (CPU) | counter | — |

Cardinality is bounded: `partition` spans the 3 Kafka partitions; `symbol`
(freshness) spans the ~40 subscribed stocks. No unbounded labels are emitted.

## Freshness SLI

`tick_persistence_snapshot_staleness_seconds{symbol}` =
`now() - serving.symbol_snapshot.last_event_ts`, refreshed by a background task
every `TICK_PERSISTENCE_FRESHNESS_REFRESH_INTERVAL_SECONDS` (default `5s`). This
is the user-facing signal: how stale is the price the front-end serves.

## SLI / SLO

Baselines are the **post-fix steady state** (not the incident). Ingest peak is
~58 msg/s; batch processing capacity is thousands/s, so steady-state lag is 0
and staleness is dominated by the batch poll timeout (≤ 1s, Task 3).

| SLI | SLO (steady state) | Alert threshold |
|---|---|---|
| **Freshness** | intraday p99 snapshot staleness **< 5s** | warning at **3s**, critical at **5s for 3m** |
| **Consumer lag** | **≈ 0** intraday | alert when `sum(lag) > 1,000 for 3m` |
| **Throughput** | inserted/s ≥ ingress/s; lag trend = 0 | `ThroughputBelowIngress` for 10m |
| **Availability** | monthly **99.5%** (pod Ready + scrape up) | `up == 0 for 2m` |
| **Error rate** | batch failure < 0.1%, quarantine < 1% | poison-pill / batch-failure spikes |

The 5s freshness SLO presupposes timeout-dominated batching (size 500 **OR**
time ≤ 1s, whichever first). Filling 500 by size alone at peak takes ~8.6s,
which would breach 5s — hence the ≤ 1s `poll_timeout` is the SLO precondition.

## Error budget

- Freshness SLO = intraday p99 staleness < 5s. Availability 99.5% ⇒ ~3.6h/month
  budget. Budget burn raises the priority of follow-up tuning / scale-out.
- Alert thresholds are static (appropriate for a single-node homelab);
  multi-window burn-rate alerting is deferred until baselines mature.

## Market-hours gating (alert layer)

Ticks only flow on weekdays 09:00–15:30 KST. Freshness/throughput alerts are
gated on an **upstream activity signal** (new Kafka offsets on `stock-ticks`),
not on a hardcoded clock and not on downstream bronze inserts (a downstream
gate would self-silence during the very incident it must catch). **Consumer lag
is never gated** — an off-hours backlog is still data loss risk. Gating lives in
`docs/alerts.yaml` and in the `observability-prometheus-slack.md` rule layer.

## Completeness reconciliation

Per Kafka partition the ledger enforces the zero-loss balance invariant:

```
consumed == inserted + conflict + quarantine + skip
```

Every consumed message lands in exactly one downstream category:

- **inserted** — new bronze row (RETURNING count)
- **conflict** — duplicate `event_id` (valid-but-not-inserted = `valid - inserted`)
- **quarantine** — poison-pill routed to `bronze.tick_quarantine`
- **skip** — deserialize failure / tombstone (never reaches the handler)

The consumer records `consumed`/`skip`; the handler records
`inserted`/`conflict`/`quarantine` after its transaction commits. A non-zero
`tick_persistence_reconciliation_imbalance{partition}` means a committed offset
was not accounted for — i.e. silent loss — and is logged at WARNING and alerted
on (`ReconciliationImbalance`).
