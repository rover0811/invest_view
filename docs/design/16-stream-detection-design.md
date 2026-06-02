---
aliases:
- 16 Stream Detection Design
tags:
- design
- stream-detection
- flink
created: 2026-06-01
---

# 16 Stream Detection Design

이 문서는 두드림 v1의 **Flink 실시간 감지 레이어 상세 설계**다. 상위 의사결정은 [[11-design-freeze-discussion-pack]]를 따르며, stream-detection 경계는 이 문서를 source of truth로 본다.

## Freeze Sync Extract

- **Java DataStream API**: PyFlink의 의존성 관리 복잡성(fat JAR 부재 등)을 해결하기 위해 Java로 피벗하여 구현한다.
- **UUID5 Idempotency**: SHA-1 기반 UUID v5를 사용하여 체크포인트 복구 시에도 동일한 `alert_event_id`를 생성, DB 레벨에서 자동 멱등성을 보장한다.
- **FLINK-11030 Workaround**: Avro GenericRecord의 Decimal 역직렬화 버그를 우회하기 위해 SpecificRecord 코드 생성 방식을 채택한다.
- **Strict Schema Management**: `auto.register.schemas`를 금지하고, `AvroSchemaGuard`를 통해 기동 시 Schema Registry 등록 여부를 강제 검증한다.
- **Event Time via received_at**: `trade_time`의 날짜 정보 부재 문제를 해결하기 위해 수집 레이어에서 찍은 `received_at`(ISO8601 UTC)을 워터마크 기준으로 사용한다.

## 0. Scope Boundary

### In Scope

- **Real-time Alert Detection**: 가격 변동성(Price Alert), VI 근접(VI Imminent), 거래 정지 상태 전이(Trading Halt) 감지
- **Technical Pattern Detection**: 골든/데드크로스(MA5/MA20), RSI(14), MACD(12/26/9) 기반 패턴을 closed 5분봉 기준으로 감지하고 `stock-patterns` 토픽으로 발행
- **Stateful Processing**: 5분 슬라이딩 윈도우, 상태 기반 전이, bar-close 기반 패턴 상태 처리
- **Idempotent ID Generation**: Python과 호환되는 UUID5 기반 멱등 키 생성
- **Schema Validation**: 기동 시 SR 연동 및 스키마 정합성 체크

### Out of Scope

- **Savepoint Management**: 자동화된 세이브포인트 스케줄링/오케스트레이션 (v1은 last-state 업그레이드 + PVC 기반 체크포인트/HA로 수동 복구 지원)
- **Dynamic Rule Injection**: 런타임 규칙 변경 (현재는 환경 변수 기반 정적 설정)
- **Parallelism Scaling**: v1은 병렬도 1로 고정하여 운영 복잡도 최소화

### Boundary Statement

Stream Detection의 책임은 **Kafka `stock-ticks`를 소비하여 3종의 알림 룰(`stock-alerts`)과 3종의 기술적 패턴 룰(`stock-patterns`)을 실시간으로 감지하고, 멱등적인 UUID와 함께 각 토픽으로 발행하는 것**까지다.

## 1. Pipeline Architecture

### Job Structure

`com.invest_view.stream_detection.StreamDetectionJob` 클래스가 엔트리포인트이며, 다음과 같은 파이프라인을 구성한다.

```mermaid
flowchart LR
    Source[(Kafka: stock-ticks)] --> WM[TickWatermarkStrategy]
    WM --> Split{Parallel Branches}
    
    subgraph Alert Rules
        Split --> PA[PriceAlert Window]
        Split --> VI[VI Imminent FlatMap]
        Split --> TH[TradingHalt Process]
    end

    subgraph Pattern Rules
        Split --> CR[MA Cross Detector]
        Split --> RSI[RSI Detector]
        Split --> MACD[MACD Detector]
    end

    PA --> AlertUnion[Alert Union]
    VI --> AlertUnion
    TH --> AlertUnion
    AlertUnion --> AlertSink[(Kafka: stock-alerts)]

    CR --> PatternUnion[Pattern Union]
    RSI --> PatternUnion
    MACD --> PatternUnion
    PatternUnion --> PatternSink[(Kafka: stock-patterns)]
```

### Environment Configuration

- **State Backend**: `HashMapStateBackend`
- **Checkpointing**: 60s 주기, `EXACTLY_ONCE` 모드
- **Fault Tolerance**: `minPauseBetweenCheckpoints(30s)`, `checkpointTimeout(600s)`, `maxConcurrentCheckpoints(1)`
- **Cleanup**: `RETAIN_ON_CANCELLATION` (수동 삭제 전까지 체크포인트 유지)
- **Durable State**: 체크포인트/HA 메타데이터/세이브포인트를 PVC(`flink-checkpoint-storage`, 5Gi, `standard`, RWO)에 저장하여 파드 재기동·재배포 간 상태 보존
- **Parallelism**: 기본 1 (MVP 범위)

## 2. Detection Rules

| Rule Name | Alert Type | Logic | Trigger Values | Dedup Key | Severity |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `price_alert_5min_3pct` | `PRICE_ALERT` | 300s Window / 60s Slide. `(max-min)/min >= threshold(3%)` | `min_price`, `max_price`, `change_rate`, `threshold` | `observationStartMs` | `WARNING` |
| `vi_imminent_1pct` | `VI_IMMINENT` | Per-tick FlatMap. `\|price - vi_trigger\| / vi_trigger <= threshold(1%)` | `price`, `vi_trigger_price`, `distance_ratio`, `threshold` | `received_at` (ISO8601) | `WARNING` |
| `trading_halt_transition` | `TRADING_HALT` | KeyedProcessFunction. `prev=="N" AND current=="Y"` 전이만 발동 | `prev_state`, `new_state`, `transition_time` | `transitionTimeMs` | `CRITICAL` |

### MACD Warmup Suppression

`MacdDetector`는 `closedBarCount >= slowPeriod + signalPeriod` 조건을 만족하기 전까지 모든 MACD 신호를 억제한다. 기본값 기준으로 26 + 9 = **35개의 closed 5분봉**이 필요하다. 이는 EMA 초기화 구간의 스퓨리어스 crossover 신호를 방지하기 위한 guard이며, cold start 또는 명시적 state wipe 후 패턴 웜업 기간과 동일한 운영상 의미를 가진다.

### Common Eligibility Filter
- `trading_halted == "N"` (Trading Halt 룰 제외)
- `price > 0`
- `market` ∈ {`KRX`, `NXT`}

## 3. Serialization & Schema

### FLINK-11030 Workaround
Avro `GenericRecord` 사용 시 `decimal` logicalType이 `ByteBuffer`로 역직렬화되어 `AvroSerializer.copy()`에서 `ClassCastException`이 발생하는 버그를 해결하기 위해 다음 전략을 사용한다.
- **SpecificRecord**: `StockTick`, `StockAlert` 클래스를 Avro 스키마로부터 자동 생성
- **Decimal Conversion**: 생성된 클래스의 `MODEL$`에 `DecimalConversion`을 등록하고 `enableDecimalLogicalType=true` 설정

### Kafka Connectors
- **Source**: `stock-ticks` 토픽, `stream-detection-java` 그룹 ID, `latest` 오프셋부터 시작. `ConfluentRegistryAvroDeserializationSchema` 사용.
- **Sinks**: `stock-alerts` 및 `stock-patterns` 토픽, `EXACTLY_ONCE` 보장 (Kafka 트랜잭션, sink별 고유 `transactionalIdPrefix`, `transaction.timeout.ms=900000`). `auto.register.schemas=false` (사전 등록 필수).

## 4. Idempotent Deduplication

`AlertBuilders.java`에서 UUID v5를 생성하여 다운스트림(`alert_service`)에서의 멱등 처리를 지원한다.

- **Algorithm**: SHA-1 기반 Name-based UUID (v5)
- **Namespace**: `6ba7b810-9dad-11d1-80b4-00c04fd430c8` (DNS Namespace)
- **Format**: `uuid5(NS, "{symbol}|{alert_type}|{key}")`
- **Cross-language Identity**: Python의 `uuid.uuid5()`와 바이트 단위로 동일하게 설계되어, 언어 간 전환 시에도 동일한 ID 생성을 보장한다.

## 5. Watermark Strategy

`TickWatermarkStrategy.java`는 이벤트 타임 처리를 위해 다음과 같이 동작한다.

- **Timestamp Extraction**: `StockTick.received_at` (ISO8601 UTC) 필드를 파싱하여 Epoch Milli로 변환.
- **Out-of-orderness**: 10초 허용 (`forBoundedOutOfOrderness(Duration.ofSeconds(10))`).
- **Idleness**: 1분 설정 (`withIdleness(Duration.ofMinutes(1))`). 병렬도 1 환경에서 특정 종목의 데이터 공백이 전체 워터마크 전진을 방해하는 것을 방지한다.

## 6. Deployment & Operations

### Infrastructure
- **Flink Version**: 1.18.1
- **Flink Operator**: 1.14.0
- **Image**: `stream-detection-java:rules3`
- **Upgrade Mode**: `last-state` (Kubernetes HA + PVC 기반 체크포인트로 상태 보존 복구; 세이브포인트 자동 스케줄링은 범위 외)

### Resource Allocation
- **JobManager/TaskManager**: 각 1024m Memory, 1 CPU
- **Parallelism**: 1 (TaskSlots 1)

## Resolved design decisions

| # | 질문 | 결정 |
| :--- | :--- | :--- |
| D1 | 구현 언어 선택 | PyFlink의 의존성 지옥을 피하기 위해 Java DataStream API 채택 |
| D2 | 이벤트 타임 기준 | `trade_time`은 날짜가 없어 부적합하므로 `received_at` 사용 |
| D3 | 스키마 등록 방식 | `auto.register.schemas`를 금지하고 `make schemas`로 사전 등록 강제 |
| D4 | 멱등성 보장 | UUID5 기반 `alert_event_id` 생성으로 DB 레벨 멱등성 확보 |
| D5 | 체크포인트 저장소 | 운영 복구를 위해 PVC(`flink-checkpoint-storage`) 기반 체크포인트/HA/세이브포인트 + `last-state` 업그레이드 채택 (PR #19 리뷰 반영) |
| D6 | 병렬도 설정 | 운영 복잡도 최소화를 위해 병렬도 1로 고정 |
| D7 | 스키마 검증 | `AvroSchemaGuard`를 통해 기동 시 SR 등록 여부 즉시 검증 |

## Remaining open questions

- **Savepoint Automation**: 현재 수동 세이브포인트/`last-state` 복구를 자동 스케줄링·오케스트레이션으로 확장할 시점
- **Scaling Strategy**: 병렬도 확장 시 KeyGroup 할당 및 워터마크 정체 해소 방안
- **Cross-node Storage**: 단일 노드 kind의 RWO PVC를 멀티노드/클라우드(S3 등 공유 스토리지) 환경으로 확장할 시점

## v1 implementation scope

- **Current**: 3종 알림 룰(Price, VI, Halt) + 3종 패턴 룰(Cross, RSI, MACD), UUID5 멱등 키, Java 기반 안정적 파이프라인
- **Next Scoped**: 세이브포인트 자동 스케줄링, 공유 스토리지(S3) 기반 체크포인트, 병렬도 확장
- **Out of Scope**: 런타임 동적 규칙 변경, 미국 시장 데이터 처리

## Related Notes

- [[11-design-freeze-discussion-pack]]
- [[12-kis-realtime-ingress-design]]
- [[14-alert-serving-design]]
- [[event-driven-stock-pipeline]]
