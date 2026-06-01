# stream-detection-java

> Apache Flink (Java) streaming pipeline that consumes Korean stock ticks from Kafka,
> applies 3 real-time detection rules (PRICE_ALERT, VI_IMMINENT, TRADING_HALT),
> and publishes alerts back to Kafka for downstream services.
>
> Part of the `invest_view` portfolio project — see the [project README](../../README.md) for the broader architecture.

## Overview

The `stream-detection-java` service is a high-performance streaming application built on Apache Flink. It consumes `StockTick` events from the `stock-ticks` Kafka topic, processes them through a series of real-time detection rules, and emits `StockAlert` events to the `stock-alerts` topic.

The service implements three core detection rules:
- **PRICE_ALERT**: Detects significant price movements (≥3% spread) within a 5-minute sliding window (1-minute slide), keyed by symbol.
- **VI_IMMINENT**: A per-tick check that fires when the current price is within 1% of the Static VI (Volatility Interruption) trigger price.
- **TRADING_HALT**: A stateful detector that monitors the `trading_halted` status and fires when it transitions from "N" to "Y".

This Java implementation serves as a robust alternative to the Python PyFlink reference (`services/stream_detection/`). By using the Java DataStream API and Avro `SpecificRecord` serialization, we sidestep the `BigDecimal` serialization issues (FLINK-11030) encountered in the Python implementation.

The service uses a deterministic UUID v5 generation strategy (SHA-1, DNS namespace) for `alert_event_id`. This ensures that identical alerts generated for the same (symbol, alert_type, key) triple produce the same ID across different runs or even different language implementations, enabling effective deduplication in the downstream `alert_service` database.

## Architecture

The following diagram illustrates the data flow through the system:

```
KIS Open API (WebSocket realtime price feed)
        │
        ▼
┌──────────────────┐
│  kis_ingestion   │  (Python, services/kis_ingestion/)
│  raw tick parser │
└────────┬─────────┘
         │
         ▼ (Avro StockTick, schema_id=5)
┌────────────────────────────────────┐
│  Kafka topic: stock-ticks          │
│  Confluent Schema Registry         │
└────────┬───────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────┐
│  THIS SERVICE: stream-detection-java                     │
│  Flink 1.18.1 on Kubernetes (Flink Operator 1.14.0)      │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  StreamDetectionJob.main()                       │   │
│  │   ├─ KafkaSource<StockTick> via SpecificRecord   │   │
│  │   ├─ Watermark: received_at ISO8601, 10s slack   │   │
│  │   │                                              │   │
│  │   ├─→ filter(isEligible).keyBy(symbol)           │   │
│  │   │   .window(SlidingEvent 5min/1min)            │   │
│  │   │   .aggregate(PriceAlertAggregator) ──┐       │   │
│  │   │                                       ▼      │   │
│  │   ├─→ flatMap(VIImminentFlatMap) ────→ union ─┐  │   │
│  │   │                                            ▼  │   │
│  │   ├─→ keyBy(symbol).process(                      │   │
│  │   │     TradingHaltDetector w/ ValueState) ────┐  │   │
│  │   │                                            ▼  │   │
│  │   │                                       KafkaSink │   │
│  │   │                                            │   │
│  │   └─ checkpoint: EXACTLY_ONCE / 60s / file:// /opt/flink/checkpoints
│  └──────────────────────────────────────────────────┘   │
└────────┬─────────────────────────────────────────────────┘
         │
         ▼ (Avro StockAlert, schema_id=…)
┌────────────────────────────────────┐
│  Kafka topic: stock-alerts         │
└────────┬───────────────────────────┘
         │
         ▼
┌──────────────────┐
│  alert_service   │  (Python, services/alert_service/)
│  Kafka consumer  │
│   → Postgres     │  (alert_service.alert_events, PK = alert_event_id)
└──────────────────┘
```

We use `SpecificRecord` (Avro code generation) instead of `GenericRecord` to ensure type safety and avoid runtime casting issues with logical types like `decimal`. This pivot was critical for handling the high-precision price data required for financial calculations.

## Prerequisites

- **Docker**: For running the kind cluster and the core infrastructure (Kafka, Schema Registry, Postgres).
- **kind** ≥ 0.20: Kubernetes-in-Docker for local Flink deployment.
- **kubectl**: Command-line tool for interacting with the Kubernetes cluster.
- **Helm** v3.x: For installing the Flink Kubernetes Operator.
- **Maven** 3.9+: For building the Java project.
- **Java 17**: JDK 17 is required and enforced by the `maven-enforcer-plugin`.
  - macOS: `export JAVA_HOME="$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home"`
- **Python 3.11 + uv**: For running synthetic test producers and downstream services.

Quick verification:
```bash
kubectl get kafka,deploy/schema-registry,statefulset/postgres -A   # infra Ready in the cluster
java -version 2>&1 | head -1   # should show 17
mvn -v | head -1               # should show 3.9+
kind version                    # should show 0.20+
helm version --short            # should show v3+
```

## Setup

Infrastructure now runs entirely in the `kind` cluster — there is no Docker Compose and no
kind↔compose network bridge. Prepare the environment via the top-level `Makefile`:
```bash
kind create cluster --name invest-flink   # one-time (Makefile never creates the cluster)
make operators         # Strimzi operator
make flink-operator    # Flink Kubernetes Operator (installs cert-manager if needed)
make infra-up          # Strimzi Kafka cluster + topics + in-cluster Schema Registry + Postgres
make schemas           # register stock-ticks-value, stock-alerts-value
```
Pods reach Kafka/SR/Postgres over in-cluster DNS
(`invest-kafka-kafka-bootstrap.kafka.svc:9092`, `schema-registry:8081`, `postgres:5432`).

## Build

To build the service and package it into a fat JAR:
```bash
cd services/stream_detection_java
mvn clean package
```

The build process:
- Generates Avro `SpecificRecord` classes from `.avsc` files.
- Compiles the Java source code.
- Runs unit tests (expecting ≥60 passes).
- Packages everything into `target/stream-detection-java-1.0-SNAPSHOT.jar`.

The resulting JAR is a shaded fat JAR containing all dependencies, including the Flink Kafka connector, Confluent serdes, and the UUID generator.

## Deploy

Deploy the service to the kind cluster:
```bash
bash services/stream_detection_java/scripts/deploy.sh
```

The deployment script:
1. Rebuilds the JAR (skipping tests).
2. Builds a Docker image tagged `stream-detection-java:rules1`.
3. Loads the image into the kind cluster.
4. Applies the `k8s/flinkdeployment.yaml` manifest.
5. Waits for the job to reach the `RUNNING` state.
6. Sets up a port-forward for the Flink Web UI.

Access the **Flink Web UI** at: http://localhost:8083

### Stateless redeploy & pattern warmup

The Flink job uses `upgradeMode: stateless` with `emptyDir` checkpoints. On every redeploy, all keyed state resets. This includes the rolling state for pattern detectors (MA5/MA20 history, RSI 14-period window, MACD EMA12/26/signal).

After a stateless redeploy, the pattern rules require a warmup period before they emit events again:
- **Golden/Dead cross**: Needs 20 closed 5m bars (MA_LONG).
- **RSI**: Needs 15 closed 5m bars (RSI_PERIOD+1).
- **MACD**: Needs ~35 closed 5m bars (MACD_SLOW + signal).

**Operational Note**: Prefer redeploying out of market hours. Expect no pattern events for roughly 130 minutes of market data (~26 closed 5m bars) after a stateless redeploy. This is by design for v1; stateful upgrades via PVC/savepoints are deferred.

## Verify

To verify the end-to-end flow, inject synthetic ticks via the Makefile QA injector (runs an
in-cluster Job against the Strimzi bootstrap), then wait for window processing:

```bash
make inject-tick
sleep 25  # Wait for window processing and alert propagation
```

Verify the alerts landed in Postgres (requires `alert_service` running):
```bash
kubectl exec statefulset/postgres -- psql -U postgres -d invest_view -c \
  "SELECT rule_name, count(*) FROM alert_service.alert_events GROUP BY 1"
```

## Troubleshooting

### `ImagePullBackOff` / `ErrImageNeverPull`
The image is local to the kind cluster. Ensure you've run `kind load docker-image stream-detection-java:rules1 --name invest-flink`. You can verify the image presence with:
`docker exec invest-flink-control-plane crictl images | grep stream-detection`

### `Schema not found` / SR 404
Ensure schemas are registered. Run `make schemas` from the repo root. Verify with a temporary
port-forward: `kubectl port-forward svc/schema-registry 8081:8081` then `curl -s http://localhost:8081/subjects`.

### `Connection refused` to Kafka
Pods reach Kafka over in-cluster DNS at `invest-kafka-kafka-bootstrap.kafka.svc:9092` (no
Docker bridge). Verify the Strimzi cluster is Ready (`kubectl -n kafka get kafka`) and that the
FlinkDeployment env `KAFKA_BOOTSTRAP_SERVERS` points at that bootstrap.

### FlinkDeployment stuck in DEPLOYING
Check the operator logs and deployment description:
- `kubectl describe flinkdeployment stream-detection`
- `kubectl logs -l component=jobmanager --tail=200`
Common issues include resource constraints or image architecture mismatches (e.g., running x86 images on Apple Silicon).

### `ClassCastException: BigDecimal cannot be cast to ByteBuffer`
This indicates a fallback to `GenericRecord` or a mismatch in Avro configuration. Ensure `SpecificRecord` is used and the `avro-maven-plugin` has `enableDecimalLogicalType` set to `true`.

### `Expecting type to be a PojoTypeInfo`
Flink's POJO extractor requires public setters. Ensure `<createSetters>true</createSetters>` is present in the `avro-maven-plugin` configuration in `pom.xml`.

### Wrong Java version
The project requires JDK 17. If you see build errors related to Java version, set your `JAVA_HOME` correctly:
`export JAVA_HOME="$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home"`

## References

- **Plan 19**: `.sisyphus/plans/19-stream-detection-java.md`
- **PyFlink reference**: `services/stream_detection/`
- **alert_service**: `services/alert_service/`
- **kis_ingestion**: `services/kis_ingestion/`
- **Apache Flink 1.18 docs**: https://nightlies.apache.org/flink/flink-docs-release-1.18/
- **Flink Kubernetes Operator 1.14**: https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-release-1.14/
- **Confluent Avro Serializer**: https://docs.confluent.io/platform/current/schema-registry/serdes-develop/serdes-avro.html
- **FLINK-11030 (the bug we sidestep)**: https://issues.apache.org/jira/browse/FLINK-11030
