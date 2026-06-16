# 20. Data Contracts — price staleness fix foundation

이 문서는 stale-price bug(#29)와 TEXT time-column bug(#30)를 “수직 데이터 흐름 추적”이 아니라 **수평 data contract 부재**로 정리한다. T0(invariants)와 T1(idempotency key)의 입력은 아래 네 계약이다.

원칙: Kafka offset은 lineage/position이고, 체결의 business identity가 아니다. `ON CONFLICT`는 올바른 identity가 있을 때만 멱등성을 준다.

---

## 1. Identity contract — “같은 체결”을 무엇으로 식별하는가

### 현재 상태 (current)

- `stock-ticks` Avro에는 Kafka lineage가 아닌 tick payload 필드만 있고, 체결번호/sequence/execution id 필드는 없다. 스키마는 `source_tr_id`, `market`, `received_at`, `symbol`, `trade_time`, `price` 등을 정의하지만 실행번호는 정의하지 않는다 (`schemas/stock-ticks.avsc:6`, `schemas/stock-ticks.avsc:8`, `schemas/stock-ticks.avsc:9`, `schemas/stock-ticks.avsc:10`, `schemas/stock-ticks.avsc:54`).
- KIS parser의 normalized model도 `symbol`, `trade_time`, `price`, `trade_volume`, `business_date` 등 46개 wire fields만 가진다 (`services/kis_ingestion/src/kis_ingestion/tick_parser.py:13`, `services/kis_ingestion/src/kis_ingestion/tick_parser.py:14`, `services/kis_ingestion/src/kis_ingestion/tick_parser.py:15`, `services/kis_ingestion/src/kis_ingestion/tick_parser.py:26`, `services/kis_ingestion/src/kis_ingestion/tick_parser.py:47`).
- KIS source field order도 `symbol`, `trade_time`, `price`로 시작하고 46개 필드를 선언하지만 execution-number/sequence 필드는 없다 (`services/kis_ingestion/src/kis_ingestion/models/tick.py:57`, `services/kis_ingestion/src/kis_ingestion/models/tick.py:58`, `services/kis_ingestion/src/kis_ingestion/models/tick.py:59`, `services/kis_ingestion/src/kis_ingestion/models/tick.py:70`, `services/kis_ingestion/src/kis_ingestion/models/tick.py:72`).
- Bronze 저장소는 business identity가 아니라 Kafka 위치로 dedupe key를 만든다: `dedupe_key = f"{message.topic}:{message.partition}:{message.offset}"` (`services/tick_persistence/src/tick_persistence/repository/tick_history.py:24`). 이 값은 `tick_dedupe_key`에 저장된다 (`services/tick_persistence/src/tick_persistence/repository/tick_history.py:30`).
- DB 모델은 `tick_dedupe_key`를 유니크로 둔다 (`services/tick_persistence/src/tick_persistence/db/models.py:35`) 그리고 Kafka 위치 컬럼을 별도로 저장한다 (`services/tick_persistence/src/tick_persistence/db/models.py:44`, `services/tick_persistence/src/tick_persistence/db/models.py:45`, `services/tick_persistence/src/tick_persistence/db/models.py:46`).

### 있어야 할 상태 (target)

- “같은 체결”의 identity를 Kafka 위치와 분리해 명시해야 한다.
- KIS 현재 payload에는 execution-number가 없으므로 T1은 **없는 필드를 있다고 가정하면 안 된다**. 가능한 선택지는 다음 중 하나로 설계 결정을 내려야 한다.
  - 공급자 원천에 실제 체결번호가 있으면 schema에 `source_execution_id` 같은 canonical identity를 추가한다.
  - 없으면 `source + market + symbol + business_date + trade_time + price + trade_volume + cumulative_volume + trade_type` 같은 deterministic surrogate를 정의하되, 충돌 가능성과 “동일 초 복수 체결” 한계를 contract에 적는다.
- `kafka_topic/partition/offset`은 audit lineage로만 유지하고, `tick_dedupe_key`는 logical execution identity에서 만들어야 한다.

### DDIA mapping

- Ch11 Streams: event identity와 log position을 구분해야 replay/derived state가 안정된다.
- Ch4 Encoding: Avro schema에 identity 필드와 의미가 명시되지 않으면 downstream은 임의 추론을 하게 된다.
- Ch8 Distributed Trouble: 재시도/중복/재전송 상황에서 네트워크 위치(offset)는 business event identity가 될 수 없다.

---

## 2. Time contract — event-time / processing-time / persistence-time 분리

### 현재 상태 (current)

- Avro schema에서 `received_at`은 string이다 (`schemas/stock-ticks.avsc:8`).
- Avro schema에서 `trade_time`도 string이다 (`schemas/stock-ticks.avsc:10`).
- Avro schema에서 `business_date`도 string이다 (`schemas/stock-ticks.avsc:42`).
- Bronze migration은 `received_at`을 `sa.Text()`로 만든다 (`services/tick_persistence/alembic/versions/0001_initial.py:63`).
- Bronze migration은 `trade_time`을 `sa.Text()`로 만든다 (`services/tick_persistence/alembic/versions/0001_initial.py:65`).
- Bronze migration은 `business_date`를 `sa.Text()`로 만든다 (`services/tick_persistence/alembic/versions/0001_initial.py:74`).
- Bronze migration에서 진짜 TIMESTAMPTZ는 `persisted_at`뿐이다 (`services/tick_persistence/alembic/versions/0001_initial.py:79`). ORM도 `received_at`, `trade_time`, `business_date`는 `Text`이고 (`services/tick_persistence/src/tick_persistence/db/models.py:86`, `services/tick_persistence/src/tick_persistence/db/models.py:88`, `services/tick_persistence/src/tick_persistence/db/models.py:97`), `persisted_at`만 timezone-aware timestamp다 (`services/tick_persistence/src/tick_persistence/db/models.py:103`, `services/tick_persistence/src/tick_persistence/db/models.py:104`).
- Parser는 `received_at`을 meta string으로 받고 (`services/kis_ingestion/src/kis_ingestion/tick_parser.py:66`, `services/kis_ingestion/src/kis_ingestion/tick_parser.py:68`, `services/kis_ingestion/src/kis_ingestion/tick_parser.py:86`), `trade_time`과 `business_date`를 string field로 둔다 (`services/kis_ingestion/src/kis_ingestion/tick_parser.py:15`, `services/kis_ingestion/src/kis_ingestion/tick_parser.py:47`).

### 있어야 할 상태 (target)

- 세 시간을 계약으로 분리한다.
  - **event-time**: 실제 체결 시각. KIS 현재 입력은 `business_date(YYYYMMDD)` + `trade_time(HHMMSS)`이므로 한국시장 timezone/session rule로 canonical timestamp를 만들어야 한다.
  - **processing-time**: ingestion app이 받은 시각. `received_at`은 ISO string이 아니라 timestamp logical type 또는 DB TIMESTAMPTZ로 정규화해야 한다.
  - **persistence-time**: DB에 쓴 시각. `persisted_at`은 audit용이지 최신 체결 판단 기준이 아니다.
- Silver/serving의 “최신 가격”은 `persisted_at` LWW가 아니라 event-time + deterministic tie-breaker에 따라 결정해야 한다.

### DDIA mapping

- Ch11 Streams: event time과 processing time을 분리하지 않으면 windowing/watermark/replay 결과가 바뀐다.
- Ch5 Replication/LWW: “가장 나중에 저장된 row가 최신 가격”이라는 LWW는 clock/order 오류와 지연 이벤트에 취약하다.
- Ch4 Encoding: time logical type 없이 string으로 두면 정렬, timezone, evolution contract가 사라진다.

---

## 3. Idempotency contract — at-least-once delivery에서 중복을 어떻게 제거하는가

### 현재 상태 (current)

- Consumer는 수동 commit이다: `enable.auto.commit`이 `False`이고 (`services/tick_persistence/src/tick_persistence/kafka/consumer.py:73`), handler 성공 후 offset commit을 한다 (`services/tick_persistence/src/tick_persistence/kafka/consumer.py:185`, `services/tick_persistence/src/tick_persistence/kafka/consumer.py:186`). 실패 시 commit하지 않고 재시도한다 (`services/tick_persistence/src/tick_persistence/kafka/consumer.py:187`, `services/tick_persistence/src/tick_persistence/kafka/consumer.py:189`). 즉 delivery는 at-least-once다.
- 중복 제거는 `topic:partition:offset` 기반 `tick_dedupe_key`에 의존한다 (`services/tick_persistence/src/tick_persistence/repository/tick_history.py:24`, `services/tick_persistence/src/tick_persistence/repository/tick_history.py:30`).
- `ON CONFLICT DO NOTHING`은 그 offset-derived key에만 적용된다 (`services/tick_persistence/src/tick_persistence/repository/tick_history.py:32`, `services/tick_persistence/src/tick_persistence/repository/tick_history.py:35`).
- DB unique constraint도 `tick_dedupe_key` 하나뿐이다 (`services/tick_persistence/src/tick_persistence/db/models.py:35`; migration: `services/tick_persistence/alembic/versions/0001_initial.py:80`).
- KIS producer는 Kafka producer idempotence를 켠다 (`services/kis_ingestion/src/kis_ingestion/producer.py:114`, `services/kis_ingestion/src/kis_ingestion/producer.py:116`, `services/kis_ingestion/src/kis_ingestion/producer.py:117`), 하지만 payload key는 symbol이고 (`services/kis_ingestion/src/kis_ingestion/producer.py:127`, `services/kis_ingestion/src/kis_ingestion/producer.py:129`), logical tick identity를 만들지 않는다.

### 있어야 할 상태 (target)

- Consumer idempotency key는 **same business event → same key**여야 한다. 같은 tick이 다른 offset으로 republish되어도 같은 key가 나와야 한다.
- `ON CONFLICT DO NOTHING`은 logical execution key에 걸려야 한다. `upsert` 자체가 idempotent가 아니라, conflict target이 idempotent해야 한다.
- Kafka producer idempotence는 broker 세션 내 duplicate produce 방지 설정으로 취급하고, end-to-end exactly-once 또는 DB idempotency의 근거로 쓰지 않는다.

### DDIA mapping

- Ch7 Transactions: “upsert를 쓰면 멱등”은 fallacy다. 고유키가 business operation identity일 때만 retry-safe하다.
- Ch11 Streams: at-least-once consumer는 sink idempotency 또는 transactions가 있어야 재처리 안전하다.
- Ch8 Distributed Trouble: producer retry, consumer crash, network timeout은 모두 중복을 만든다.

---

## 4. Reprocessing contract — replay/restart 때 무엇이 보장되는가

### 현재 상태 (current)

- Tick persistence config는 `auto.offset.reset=earliest`를 기본값으로 둔다 (`services/tick_persistence/src/tick_persistence/config.py:16`, `services/tick_persistence/src/tick_persistence/config.py:17`, `services/tick_persistence/src/tick_persistence/config.py:18`). Consumer 생성 시 그 값을 그대로 Kafka config에 넣는다 (`services/tick_persistence/src/tick_persistence/kafka/consumer.py:68`, `services/tick_persistence/src/tick_persistence/kafka/consumer.py:72`).
- Handler 성공 후 commit하고 (`services/tick_persistence/src/tick_persistence/kafka/consumer.py:185`, `services/tick_persistence/src/tick_persistence/kafka/consumer.py:186`), 실패하면 “NOT committing”으로 다음 세션 재시도를 의도한다 (`services/tick_persistence/src/tick_persistence/kafka/consumer.py:187`, `services/tick_persistence/src/tick_persistence/kafka/consumer.py:189`).
- 그러나 replay에서 같은 logical tick이 새 Kafka offsets로 재발행되면 현재 dedupe key가 달라진다 (`services/tick_persistence/src/tick_persistence/repository/tick_history.py:24`). 따라서 replay/repub은 중복 row를 만들 수 있다.
- KIS producer idempotence는 `enable.idempotence=True` 설정일 뿐 (`services/kis_ingestion/src/kis_ingestion/producer.py:114`, `services/kis_ingestion/src/kis_ingestion/producer.py:117`), producer가 발행하는 headers도 session/sequence lineage다 (`services/kis_ingestion/src/kis_ingestion/producer.py:131`, `services/kis_ingestion/src/kis_ingestion/producer.py:132`, `services/kis_ingestion/src/kis_ingestion/producer.py:133`). 이 값들은 DB identity로 보존되지 않는다.
- Flink derived sinks는 Kafka sink `EXACTLY_ONCE`를 사용한다 (`services/stream_detection_java/src/main/java/com/invest_view/stream_detection/sink/AlertKafkaSink.java:36`, `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/sink/AlertKafkaSink.java:37`; `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/sink/PatternKafkaSink.java:33`, `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/sink/PatternKafkaSink.java:34`). 이는 Flink→Kafka 구간 보장이지, KIS→Bronze DB replay contract는 아니다.

### 있어야 할 상태 (target)

- Replay/restart 계약을 세 단계로 분리한다.
  1. **Kafka position replay**: earliest/reset/group 변경으로 같은 log record를 다시 읽을 수 있다.
  2. **Logical replay**: 같은 source tick이 새 topic/partition/offset으로 들어와도 DB 결과가 동일해야 한다.
  3. **Derived replay**: silver/serving 재계산은 event-time order와 deterministic tie-breaker로 동일 결과를 만들어야 한다.
- Bronze는 logical idempotency key로 재처리 안전성을 보장하고, Kafka offset은 어느 log record에서 왔는지 추적하는 lineage metadata로만 둔다.

### DDIA mapping

- Ch11 Streams: replay 가능한 log와 deterministic consumer state가 분리되어야 한다.
- Ch5 Replication/LWW: 재처리 중 late event가 persisted_at 최신값을 덮는 구조는 stale/latest 판단을 깨뜨린다.
- Ch8 Distributed Trouble: restart, rebalance, producer session loss는 정상 상황이며 contract로 흡수해야 한다.

---

## Misconceptions to reject

### (a) “offset = identity”가 아니다

- 현재 코드는 `topic:partition:offset`을 dedupe key로 만든다 (`services/tick_persistence/src/tick_persistence/repository/tick_history.py:24`).
- 같은 code path가 Kafka metadata를 별도 컬럼에도 저장한다 (`services/tick_persistence/src/tick_persistence/repository/tick_history.py:27`, `services/tick_persistence/src/tick_persistence/repository/tick_history.py:28`, `services/tick_persistence/src/tick_persistence/repository/tick_history.py:29`). 이 값들은 lineage다.
- 같은 체결이 republish되어 offset이 바뀌면 key도 바뀐다. 따라서 offset은 business identity가 아니다.

### (b) “upsert = idempotence”가 아니다

- Tick repository는 `on_conflict_do_nothing(index_elements=["tick_dedupe_key"])`를 쓴다 (`services/tick_persistence/src/tick_persistence/repository/tick_history.py:35`).
- 그러나 그 conflict target은 offset-derived key다 (`services/tick_persistence/src/tick_persistence/repository/tick_history.py:24`). 그래서 같은 business tick이 다른 offset으로 오면 conflict가 발생하지 않는다.
- 올바른 예시는 alert/pattern처럼 event id를 conflict target으로 삼는 구조다 (`services/alert_service/src/alert_service/repository/alert_events.py:23`; `services/event_pattern_persistence/src/event_pattern_persistence/repository/pattern_events.py:60`).

### (c) “producer.idempotence = exactly-once”가 아니다

- KIS producer는 `acks=all`과 `enable.idempotence=True`를 설정한다 (`services/kis_ingestion/src/kis_ingestion/producer.py:116`, `services/kis_ingestion/src/kis_ingestion/producer.py:117`).
- 하지만 DB consumer는 at-least-once다: 성공 후 commit (`services/tick_persistence/src/tick_persistence/kafka/consumer.py:185`, `services/tick_persistence/src/tick_persistence/kafka/consumer.py:186`), 실패 시 미커밋 재시도 (`services/tick_persistence/src/tick_persistence/kafka/consumer.py:187`, `services/tick_persistence/src/tick_persistence/kafka/consumer.py:189`).
- Producer idempotence는 KIS producer session의 broker append 중복을 줄이는 설정이지, DB sink의 logical duplicate 제거 또는 replay determinism을 보장하지 않는다.

---

## Correct examples already in the repo

### pattern_events: deterministic UUIDv5 identity + idempotent sink

- Pattern schema 문서가 `pattern_event_id`를 “deterministic UUIDv5”로 선언한다 (`schemas/stock-patterns.avsc:8`, `schemas/stock-patterns.avsc:10`).
- Java builder는 DNS namespace UUIDv5 generator를 만들고 (`services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/PatternBuilders.java:16`, `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/PatternBuilders.java:17`), `symbol|patternType|windowKey`로 deterministic id를 만든다 (`services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/PatternBuilders.java:26`, `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/PatternBuilders.java:27`, `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/PatternBuilders.java:28`).
- Builder는 window start/end를 key로 사용해 `pattern_event_id`를 세팅한다 (`services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/PatternBuilders.java:39`, `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/PatternBuilders.java:41`).
- Persistence는 `pattern_event_id`를 primary key로 둔다 (`services/event_pattern_persistence/src/event_pattern_persistence/db/models.py:27`) and duplicate `pattern_event_id`를 no-op으로 처리한다 (`services/event_pattern_persistence/src/event_pattern_persistence/repository/pattern_events.py:54`, `services/event_pattern_persistence/src/event_pattern_persistence/repository/pattern_events.py:60`).
- Test도 UUIDv5 예시를 고정한다 (`services/event_pattern_persistence/tests/test_avro_roundtrip.py:42`) and duplicate insert가 한 row만 남는지 검증한다 (`services/event_pattern_persistence/tests/test_repository.py:54`, `services/event_pattern_persistence/tests/test_repository.py:59`, `services/event_pattern_persistence/tests/test_repository.py:60`, `services/event_pattern_persistence/tests/test_repository.py:67`).

### alert_events: deterministic alert id + replay-safe fanout

- Alert schema는 `alert_event_id`를 event UUID로 둔다 (`schemas/stock-alerts.avsc:8`, `schemas/stock-alerts.avsc:10`).
- Java alert builder도 DNS namespace UUIDv5 generator를 만들고 (`services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/AlertBuilders.java:19`, `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/AlertBuilders.java:20`), `symbol|alertType|key`로 id를 만든다 (`services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/AlertBuilders.java:25`, `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/AlertBuilders.java:26`, `services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/AlertBuilders.java:27`).
- Rule builders use deterministic keys: price window start (`services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/AlertBuilders.java:61`), VI `receivedAt` (`services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/AlertBuilders.java:96`), trading halt transition time (`services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/AlertBuilders.java:124`).
- Alert DB uses `alert_event_id` as primary key (`services/alert_service/src/alert_service/db/models.py:74`) and repository conflict target (`services/alert_service/src/alert_service/repository/alert_events.py:17`, `services/alert_service/src/alert_service/repository/alert_events.py:23`, `services/alert_service/src/alert_service/repository/alert_events.py:24`).
- Pusher resumes fanout if the alert row already exists (`services/alert_service/src/alert_service/ws/pusher.py:83`, `services/alert_service/src/alert_service/ws/pusher.py:84`, `services/alert_service/src/alert_service/ws/pusher.py:85`), and notification fanout is idempotent on `(user_id, alert_event_id)` (`services/alert_service/src/alert_service/repository/notifications.py:25`, `services/alert_service/src/alert_service/repository/notifications.py:27`, `services/alert_service/src/alert_service/repository/notifications.py:47`, `services/alert_service/src/alert_service/repository/notifications.py:48`).

---

## T0/T1 implications

- T0 invariants must assert: no serving “latest” computation may use `persisted_at` as event freshness; all derived latest state must use event-time contract.
- T1 idempotency key must replace offset-derived `tick_dedupe_key` with a logical tick identity. If KIS lacks source execution-number, T1 must explicitly choose and test a deterministic surrogate from available fields, including collision behavior.
- Schema changes should encode time and identity contracts before further vertical tracing/debugging.
