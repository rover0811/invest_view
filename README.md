# invest_view

한국 주식 실시간 시세를 이벤트 기반으로 처리해 알림과 분석 컨텍스트를 제공하는 데이터 엔지니어링 포트폴리오 프로젝트입니다.

## 프로젝트 소개

`invest_view`는 KIS Open API를 기반으로 한국 주식 실시간 데이터를 수집하고, Kafka와 Flink로 이벤트를 처리한 뒤, FastAPI와 PostgreSQL을 통해 알림과 분석용 조회 경로를 제공하는 프로젝트입니다. 주식 서비스로 읽히는 제품 구조를 가지되, 내부적으로는 이벤트 기반 파이프라인과 데이터 계약, 적재/서빙 분리 같은 데이터 엔지니어링 포인트를 드러내는 것을 목표로 합니다.

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

![invest_view container architecture](docs/diagrams/rendered/11-d2-container-view.svg)

## 문서

- [문서 인덱스](docs/README.md)
- [설계 확정 문서](docs/design/11-design-freeze-discussion-pack.md)
- [KIS 실시간 수집 설계](docs/design/12-kis-realtime-ingress-design.md)
- [이벤트 기반 파이프라인 설계 근거](docs/design/event-driven-stock-pipeline.md)

## README를 짧게 둔 이유

설계 확정 문서는 세부 요구사항, 스코프 분리, 로드맵, 논의 포인트까지 포함한 상세 문서라서 `docs/design/` 아래에 유지합니다. 이 root README는 GitHub에서 프로젝트를 처음 보는 사람이 구조를 빠르게 이해할 수 있도록 만든 진입점입니다.
