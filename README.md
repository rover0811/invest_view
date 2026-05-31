# invest_view

한국 주식 실시간 시세를 이벤트 기반으로 처리해 알림과 분석 컨텍스트를 제공하는 데이터 엔지니어링 포트폴리오 프로젝트입니다.

KIS Open API 기반 실시간 수집, Kafka/Flink 기반 이벤트 처리, FastAPI/PostgreSQL 기반 서빙 경로를 중심으로 구성되어 있습니다.

## 현재 v1 범위

- **실시간 소스**: KIS Open API only
- **대상 범위**: 서비스 단위 구독 풀 기준 최대 41종목
- **백본 구조**: KIS WebSocket -> Kafka -> Flink -> Kafka -> FastAPI -> PostgreSQL
- **저장소**: 단일 PostgreSQL (`bronze / silver / gold / serving / integration`)
- **적재 방식**: `stock-ticks`, `stock-patterns`는 custom persistence consumer로 DB 적재
- **CDC**: PostgreSQL outbox -> Debezium Source -> `enrichment-events`

## v1 범위에서 제외

- paper trading
- 미국 시장 어댑터
- BigQuery / Elasticsearch serving
- agent / frontend의 direct Kafka access

## 아키텍처 스냅샷

![invest_view container architecture](docs/diagrams/rendered/11-d2-container-view.png)

## 문서

- [문서 인덱스](docs/README.md)
- [설계 확정 문서](docs/design/11-design-freeze-discussion-pack.md)
- [KIS 실시간 수집 설계](docs/design/12-kis-realtime-ingress-design.md)
- [이벤트 기반 파이프라인 설계 근거](docs/design/event-driven-stock-pipeline.md)

## 운영 (Operations)

### 전체 서비스 기동
모든 인프라(Kafka, Postgres 등)와 서비스(Ingestion, Alert 등)를 한 번에 실행합니다.
```bash
docker compose -f docker-compose.dev.yml up -d
```
실행되는 파이프라인: `KIS -> Kafka -> Flink(kind) -> Kafka -> alert_service -> Postgres`

### Flink 브릿지 복구 (중요)
Flink 작업은 `kind` 클러스터(`invest-flink-control-plane`)에서 실행되며, Docker Compose 네트워크(`invest_view_default`)에 브릿지로 연결되어 있습니다.
`docker compose down` 명령을 실행하면 이 브릿지 연결이 끊어집니다. 연결을 복구하려면 다음 스크립트를 다시 실행하십시오:
```bash
bash services/stream_detection_java/scripts/setup-kind.sh
```
이 스크립트는 멱등성이 보장되므로 안전하게 다시 실행할 수 있습니다.

### 실시간 데이터 검증
평일 장중(09:00 KST 이후) 실시간 데이터의 E2E 흐름 검증 방법은 OP-1 절차를 참조하십시오.

### OP-1: 평일 실시간 E2E 검증 (운영자 수동)

**[OPERATOR]** 이 절차는 실제 시장 데이터가 존재하는 평일 장중(09:00 KST 이후)에 운영자가 수동으로 수행합니다. 본 프로젝트의 자동화된 검증(AC-1~AC-7)은 이미 통과되었으며, 이 단계는 실제 KIS API와의 연동을 최종 확인하는 단계입니다. **Plan 완료를 차단하지 않는 비차단(non-blocking) 절차입니다.**

1. **사전 조건**
   - Docker Compose 스택 기동: `docker compose -f docker-compose.dev.yml up -d`
   - Flink 브릿지 연결 확인: `bash services/stream_detection_java/scripts/setup-kind.sh` (필요 시)

2. **실시간 Tick 수신 확인**
   `kis_ingestion` 서비스의 로그를 통해 실제 시장 데이터가 유입되는지 확인합니다.
   ```bash
   docker compose -f docker-compose.dev.yml logs -f kis_ingestion
   ```
   로그에 `Tick received` 또는 Kafka 발행 관련 로그가 실시간으로 흐르는지 확인합니다.

3. **Flink 처리 상태 확인**
   `kind` 클러스터에서 Flink Job이 정상 실행 중인지 확인합니다.
   ```bash
   kubectl get flinkdeployment
   ```
   `stream-detection` Job의 상태가 `RUNNING`이어야 합니다.

4. **DB Alert 생성 확인 (핵심 검증)**
   실제 시장 데이터에 의해 알림이 생성되어 DB에 적재되었는지 확인합니다.
   ```sql
   SELECT rule_name, symbol, count(*)
   FROM alert_service.alert_events
   WHERE rule_name IN ('price_alert_5min_3pct', 'vi_imminent_1pct', 'trading_halt_transition')
     AND received_at > now() - interval '1 hour'
   GROUP BY rule_name, symbol;
   ```
   *참고: `rule_name='echo'`는 진단용이므로 검증 대상에서 제외합니다.*

5. **성공 기준**
   위 SQL 쿼리 결과, 3개 규칙 중 최소 하나 이상에서 1건 이상의 데이터가 조회되어야 합니다.
