---
aliases: 
tags: 
created: 2026-04-03 05:04:13
---
# Draft: Event-Driven Real-time Pipeline (KIS + Flink + PostgreSQL)

## Current Architecture (파악 완료)
- **Batch ETL**: Bronze/Silver/Gold medallion architecture
- **Data Sources**: Toss Securities API (주가, 재무, overview), 한경컨센서스 (리포트)
- **Storage**: BigQuery (Bronze/Silver/Gold) → Elasticsearch (Gold documents)
- **Orchestration**: Airflow DAG, asyncio tasks
- **Infrastructure**: zenrows proxy, dependency-injector DI, Loki logging
- **Existing libs**: pykrx, finance-datareader already in deps

> 현재 아키텍처는 위와 같지만, **이 문서의 목표 아키텍처는 BigQuery/Elasticsearch를 제거하고 Kafka + 단일 PostgreSQL로 재구성하는 것**이다.

## User's Proposed Change
- **FROM**: Toss API 중심 batch scraping → BigQuery / Elasticsearch serving
- **TO**: KIS WebSocket (41개 등록 제한) → Kafka `stock-ticks` → Flink sliding window + CEP → `stock-alerts` / `stock-patterns` + PostgreSQL archive/serving
- **Future-ready**: 현재는 한국시장(KIS) 기준으로 구현하되, 추후 미장(Finnhub 등) 도입을 위해 data source 추상화를 먼저 설계

## Requirements (confirmed from interview)
- [x] **Ingress Separation**: KIS (realtime) vs Toss/Hankyung (batch)로 수집 경계 분리
- [x] 가격 정보 소스는 KIS 중심으로 전환
- [x] Flink로 가격 집계 및 파생 이벤트 생성
- [x] 급락/급등 파생 이벤트 생성
- [x] **Near-realtime (초 단위)** 실시간성 요구
- [x] **Kafka + Flink 전부 새로 구축** (기존 인프라 없음)
- [x] 파생 이벤트 소비자: **확정** -- FastAPI alert-service가 stock-alerts + enrichment-events를 구독하여 DB 저장 + WebSocket push

## Updated Requirements (2nd interview round)
- [x] **실시간 소스**: 증권사 API 또는 Toss API를 깎아 쓸 예정 (yfinance 아님)
- [x] **단계적 접근**: Phase 0 → 1 → 2 → 3 순서로 점진적 전환
- [x] **목적**: 학습/실험용 (프로덕션 서비스 아님)
- [x] **학습 의미**: 이벤트 아키텍처, Flink 등을 경험하는 것도 목적에 포함
- [x] **시장 선택**: 한국시장 유지, 현재 범위는 41종목 집중
- [x] **다중 유저 가정**: API 구독 풀과 유저 관심종목은 별개 레이어로 설계
- [x] **스토리지 단순화**: 단일 PostgreSQL 사용 (Bronze/Silver/Gold + serving)
- [x] **미장 확장 가능성**: 현재 범위는 제외하되, 추후 도입 가능한 인터페이스를 먼저 설계

## Critical Concerns (from updated research)
1. **KRX 무료 bulk realtime 부재**: KIS WebSocket은 앱키당 1세션 / 총 41건 등록 제한, REST는 실전 20건/초 / 모의 2건/초라 전종목 실시간은 구조적으로 불가능
2. **pykrx / finance-datareader는 intraday 대안이 아님**: 둘 다 사실상 일봉/배치 성격이라 real-time ingress 대체 수단이 될 수 없음
3. **Flink를 쓰는 이유를 명확히 해야 함**: KIS가 이미 주는 전일대비율/VWAP/OHLC를 재계산하는 것이 아니라, 5분 슬라이딩 윈도우와 기술 패턴 CEP를 만드는 데 의미가 있음
4. **한국시장 특수성**: 09:00-15:30 KST, 상하한가 ±30%, 동시호가, VI, 거래정지 등 세션/마이크로스트럭처를 반영해야 함
5. **서비스는 다중 유저**: API의 41개 구독 슬롯은 단일 유저 개념이 아니라 서비스 전체 구독 풀로 해석해야 함
6. **확장성**: 추후 미장(Finnhub 등)을 붙일 수 있게 data source와 event schema는 시장 중립적으로 잡는 편이 맞음

## Research Findings (업데이트 완료)

### Flink vs Alternatives
- **PyFlink**: production-ready지만 JVM 위에서 돌아감, 운영 복잡도 HIGH
- **운영비**: self-managed $350-600/month (compute + storage)
- **Flink CEP**: sliding window 상태 관리 + 기술적 패턴 감지에 가장 강력 (state machine 기반)
- **대안들**: Kafka Streams (간단하지만 CEP 약함), Bytewax (Python-native지만 생태계 작음)
- **권장**: 단순 집계만이면 Kafka Streams도 가능하지만, **5분 등락 이벤트 + 기술 패턴 발행**을 하려면 Flink가 가장 설득력 있음

### Event-Driven Architecture
- **하이브리드 권장**: 실시간 가격은 Kafka backbone, 배치 enrichment는 PostgreSQL 중심으로 유지
- **Pure Lambda는 지양**: two truths 문제를 피하고, write path / read path를 의도적으로 분리
- **스키마**: CloudEvents envelope + self-descriptive payload. 처음부터 Avro + Schema Registry 적용 (Flink 네이티브 지원, Debezium CDC 기본 포맷, JDBC Sink 자동 매핑)
- **Exactly-once 방향성**: Kafka idempotence + Flink checkpointing + outbox 이벤트의 idempotent 소비자 조합
- **Serving 분리 권장**: Flink는 Kafka에 파생 이벤트만 발행, query/serving은 PostgreSQL에서 담당

### Korean Market Data Source Reality (완료)
- **KIS가 사실상 유일한 무료 실시간 ingress**
- **WebSocket 한도**: 앱키당 **1세션**, 실시간체결가/호가/예상체결/체결통보 **합산 41건 등록**
- **REST 한도**: 실전 20건/초, 모의 2건/초 → 개발 환경에서 전종목 폴링은 비현실적
- **pykrx / finance-datareader**: 일봉 위주라 intraday polling 대체 불가
- **KRX / 코스콤 bulk API**: 유료 또는 법인 전용이라 학생 프로젝트에 부적합
- **결론**: 현재 범위는 KIS 기반 41종목 집중이 현실적

### Future US Market Option (완료)
- **Finnhub**: 무료 tier에서 WebSocket 50종목 + REST 무제한 + 히스토리 30년 → 추후 미장 확장 시 유력 후보
- **Alpaca**: 무료 paper trading + IEX feed WebSocket 30종목 → trading simulation 확장 후보
- **현재 판단**: 프로젝트는 한국시장 유지, 다만 `MarketDataSource` 추상화로 미장 확장 비용을 낮춤

### KIS SDK / 공식 샘플 참고
- 실시간시세 WebSocket 파이썬 샘플은 한국투자증권 `open-trading-api`와 Wikidocs 기준으로 해석
- 응답 형식은 `0|TR_ID|건수|payload...` 구조이며, `건수`가 004처럼 여러 틱을 포함할 수 있음
- 주식체결가 `H0STCNT0` payload는 **46개 필드**로 해석하고, count 기반 페이징 처리 고려 필요

## Deep Dive 논의 완료

### 이벤트 스키마 설계
- CloudEvents envelope 기반 `BaseEvent` → `StockTickEvent` / `AlertEvent` / `PatternEvent` 계층
- KIS raw format: `0|H0STCNT0|004|종목^시간^가격^...` (pipe+caret separated, count 기반 multi-record payload)
- **Kafka topic 구조**:
  - `stock-ticks`: raw ingress
  - `stock-alerts`: 복합 조건 알림 + 집계 컨텍스트
  - `stock-patterns`: 기술 패턴 + 집계 컨텍스트
- `stock-candles`는 삭제: KIS WebSocket 필드 7/8/9/10이 VWAP + OHLC를 이미 제공
- 별도 `stock-metrics` 토픽은 두지 않음: Flink 내부 상태로 유지하고, 외부 소비가 필요한 값은 alert/pattern payload에 포함
- 스키마 진화: backward compatible만 허용
- `source` 필드로 시장/공급자(KIS, Finnhub 등)를 구분해 future multi-market 확장 가능하게 설계

### KIS API 연동 패턴
- `MarketDataSource` ABC를 먼저 정의하고, 현재는 `KISMarketDataSource`로 구현
- 추후 `FinnhubMarketDataSource` 같은 어댑터를 붙여도 downstream 파이프라인은 최소 수정으로 유지
- `KISTickParser`: raw → expanded domain event 변환 (SIGN_MAP + 필드 인덱스 정규화)
- `ParsedTick`은 최소 다음 필드를 포함:
  - `symbol`, `price`, `volume`, `trade_time`, `change_sign`
  - `change_rate(6)`, `vwap(7)`, `open(8)`, `high(9)`, `low(10)`
  - `cumulative_volume(14)`, `trade_strength(19)`
  - `market_session(35)`, `trading_halted(36)`, `time_classification(44)`, `vi_trigger_price(46)`
- `KISConnectionManager`: 앱키당 **1세션 / 총 41건 등록 한도**를 전제로 운영
- 서비스는 다중 유저이므로, **실시간 구독 풀은 전체 유저 관심종목의 union**으로 관리하고, 유저별 필터링은 별도 레이어에서 처리
- 세션/시간대 판정은 고정 시계가 아니라 **필드 35/44 기반**으로 처리

### 파생 이벤트 로직
- **Layer 1 — Sliding Window Metrics (Flink 내부 상태)**
  - 5분 슬라이딩 윈도우 등락률
  - 거래량 Z-score
  - 체결강도 이동평균
  - VWAP 편차
- **Layer 2 — Alert Rules → `stock-alerts`**
  - `PRICE_ALERT`: 5분 등락률 ±5% AND 거래량 Z-score > 2.0
  - `VI_IMMINENT`: 현재가가 정적VI발동기준가에 근접 (예: ±1%)
  - `MOMENTUM_SHIFT`: 체결강도 급변
  - `TRADING_HALT`: 거래정지 상태 전환 감지
- **Layer 3 — Technical Pattern CEP → `stock-patterns`**
  - 플러거블 `DetectionStrategy` ABC + `StrategyRegistry`
  - 1차 범위: 골든/데드크로스, RSI 과매수/과매도, MACD 시그널 교차
  - 새 전략 추가 = 클래스 하나 구현
- alert / pattern 이벤트는 `trigger.values`에 집계값을 함께 담아 self-descriptive하게 발행
- 동시호가 / 거래정지 구간은 이벤트 비활성화 또는 threshold 조정 대상으로 취급

### 세션 / 시장 특수성 처리
- KIS가 이미 제공하는 전일대비율, VWAP, OHLC는 **입력 필드**로 사용하고, Flink는 시간축 분석과 복합 조건 생성에 집중
- `market_session(35)` 기반으로 시초가 과열기 / 정규장 / 동시호가를 판별
- `trading_halted(36)`가 `Y`면 alert 생성 비활성화
- `vi_trigger_price(46)`를 이용해 **거래소가 실제로 발동하기 전** 선행 경고 이벤트를 만들 수 있음

### CDC + Outbox 패턴
- **단일 PostgreSQL**을 Bronze / Silver / Gold / serving / archive의 기준 DB로 사용
- tick hot path는 **DB-first가 아니라 Kafka-first**로 둠:
  - `KIS WebSocket → app → Kafka stock-ticks`
  - `Kafka stock-ticks → custom persistence consumer → PostgreSQL tick_history`
- pattern persistence도 **우리가 만드는 custom persistence consumer**를 통해 적재한다:
  - `Kafka stock-patterns → custom persistence consumer → PostgreSQL pattern_events`
- 배치 리포트 수집은 PostgreSQL에 저장하되, 유저 알림이 필요한 경우 outbox에 이벤트 기록
- **Kafka Connect 클러스터 역할 (Debezium Source 중심)**:
  - Debezium Source: `outbox_events → Kafka enrichment-events` (CDC)
- **Debezium 용도 2가지**:
  - Silver enrichment 결과 발행 (기존)
  - 유저 알림: 배치 enrichment 완료 시 "리포트 도착" 이벤트 → alert-service → WebSocket push (신규)
- Flink가 만드는 `stock-alerts`, `stock-patterns`는 direct produce
- PostgreSQL 설정은 `wal_level=logical` 필요

### 컴포넌트 역할 분리 원칙
- **Connect** = 데이터 이동 (적재, CDC). 비즈니스 로직 없음
- **Flink** = 스트림 처리 (윈도우, CEP). exactly-once는 checkpoint로 보장
- **FastAPI alert-service** = 서빙 (DB 저장 + WebSocket push). 커스텀 Consumer
- **Custom persistence consumer** = `stock-ticks`, `stock-patterns`를 PostgreSQL에 적재
- **Kafka Streams** = 사용하지 않음 (Flink + FastAPI로 커버)

### Kafka Reliability 설정
- **Broker**: `replication.factor=3, min.insync.replicas=2, unclean.leader.election.enable=false`
- **Producer (kis-ws-bridge)**: `acks=all, enable.idempotence=true, delivery.timeout.ms=120000, retries=MAX_INT`
- **Consumer (alert-service)**: `enable.auto.commit=false, auto.offset.reset=earliest, isolation.level=read_committed`
- **전 토픽 acks=all**: Deterministic Replay가 포트폴리오 요소이므로 tick 유실 시 replay가 깨짐
- **Consumer 멱등 처리**: alert DB 저장은 upsert (`ON CONFLICT DO NOTHING`)로 중복 방지
- **Flink exactly-once**: Flink checkpointing + Kafka Transaction 조합 (Flink가 내부 처리)

### SDI2 Ch13 포트폴리오 요소 (업데이트)
1. **Deterministic Replay**: Kafka `stock-ticks` + PostgreSQL tick archive 기반 리플레이
2. **Paper Trading Engine**: 현재 범위에서는 제외, 후속 확장 후보
3. **Event Sourcing + CQRS (간소화)**:
   - Write path: `KIS → Kafka → Flink → Kafka`
   - Read path: `PostgreSQL serving / archive`
   - Alert 상태는 일단 `non-expired` / `expired` 두 개만 유지
   - expired 전환은 유저 명시적 체크 시에만 처리
4. **Order Book (L1/L2)**: KIS 호가 데이터로 실시간 오더북 구현 (확장 후보)
5. **Snapshot + Recovery**: 주기적 스냅샷 + Kafka replay 복구
6. **Market Data Publisher**: raw tick / alert / pattern을 명시적으로 분리 발행

### 4개 책 교차분석 포트폴리오 맵

#### DDIA (Kleppmann)
- Tier1: Event Time + Watermarks (Ch11), Outbox Pattern (Ch7+11), Partitioning (Ch6)
- Tier2: Replication (Ch5), Kafka as Total Order Broadcast (Ch9), Medallion in PostgreSQL (Ch10+11)
- Tier3: RSM (Ch1), Storage Engines (Ch3), Schema Evolution (Ch4)

#### Data Mesh (Dehghani)
- DO: Domain-Oriented Ownership (4도메인), Data Contracts (ODCS/Avro), 자동화된 품질 검사, Data Lineage
- SKIP: Self-Serve Platform (1인 프로젝트), 거버넌스 위원회, RBAC

#### FDE (Reis & Housley)
- Core: Data Engineering Lifecycle 5단계, Medallion Architecture
- Undercurrents: DataOps (관측성/모니터링), Data Quality (계층별 검증), Orchestration (Airflow DAG)
- Advanced: **Single PostgreSQL serving + Kafka backbone**, Progressive Validation, CI/CD for pipelines

#### SDI1+SDI2 추가 챕터 (Ch13 외)
- SDI1 Ch4: Rate Limiter (KIS WebSocket / REST 한도 관리)
- SDI1 Ch10: Notification System (alert delivery, retry, DLQ)
- SDI2 Ch5: Metrics Monitoring (Prometheus + Grafana 아키텍처)
- SDI2 Ch10: Real-time Leaderboard (strategy ranking 확장 시)
- SDI2 Ch12: Digital Wallet (paper trading 재개 시 확장 후보)

#### 책 밖 (2026 DE 트렌드)
- Time-Series DB (QuestDB): tick data 최적화 확장 후보
- DLQ + Graceful Degradation: poison pill 격리, 파이프라인 resilience
- Cost Optimization / FinOps: 월 비용 분석 + 최적화 결정 근거
- Multi-market abstraction: 한국시장 우선 구현 후 미장 adapter 추가 가능 구조

### 전체 포트폴리오 요소 (우선순위 재정렬)
- Tier S: Watermarks, Flink CEP, Data Contracts, Replay
- Tier A: Operational Serving, Single-DB Medallion, Progressive Quality, MarketDataSource abstraction
- Tier B: Order Book, Monitoring Dashboard, Outbox for enrichment, Strategy Interface
- Tier C: DLQ, Rate Limiter, QuestDB, Notifications, Leaderboard, Paper Trading

### Recommended Build Order (학생 프로젝트 기준)

#### Now — Korean Market Core 우선
1. KIS WebSocket → Kafka `stock-ticks` 실시간 ingress 완성
2. Kafka Connect 클러스터 구성 (JDBC Sink + Debezium) + PostgreSQL `tick_history` / serving snapshot 구조 정리
3. Flink sliding window + `stock-alerts` / `stock-patterns` 생성
4. alert-service (FastAPI): stock-alerts + enrichment-events 구독 → DB 저장 + WebSocket push
5. LangGraph Analysis Agent를 PostgreSQL 기반 serving layer에 연결

#### Next — Reliability / Replay Foundation
1. Event Time + Watermarks를 Flink 기본 처리 방식으로 정교화
2. Avro Schema Registry 기반 Data Contracts 정교화 (처음부터 Avro이므로 강화에 집중)
3. Deterministic Replay service / runbook 추가
4. Silver enrichment Outbox + Debezium 경로 정리

#### Later — Expansion
1. KIS 호가 기반 order book / depth 이벤트 확장
2. `MarketDataSource` 추상화를 활용한 미장(Finnhub) adapter 추가 검토
3. Replay가 충분히 검증된 뒤 Paper Trading Engine 재검토

> **Rule of thumb**: 현재는 한국시장 41종목 범위에서 event pipeline을 완성하는 것이 우선이다. 미장 확장은 구조적으로 가능하게 설계하되, 구현 순서는 replay / quality / serving 이후다.

### KIS SDK 분석 (업데이트)

#### open-trading-api (참고 구현)
- `kis_auth.py`: OAuth2 + WebSocket 접속키 + AES-256 복호화 + rate limiting 로직 참고 가능
- `KISWebSocket` 클래스: 연결/재연결/heartbeat/retry 패턴이 잘 정리돼 있음
- 100+ WebSocket 함수: 체결가, 호가, 체결통보 등 전부 구현됨
- **주의**: 2025-04-28 공식 안내 기준, 앱키당 **1세션 / 실시간 등록 총 41건**이 최신 제약
- 샘플 / 커뮤니티 자료 중 40개, 100개 등 예전 수치가 섞여 있으므로 공식 유량 공지를 우선 신뢰
- 실행 플랜은 `mojito2`나 공식 샘플 중 하나로 표준화하되, **유량 제약 자체는 공통 전제**

#### kis-ai-extensions (선택적 활용)
- AI 에이전트용 플러그인 (Claude, Cursor, Codex, Gemini)
- Strategy YAML DSL → Flink CEP 룰 포맷으로 재활용 가능
- Backtester (QuantConnect Lean) → Deterministic Replay 이후 확장 후보
- 80+ 기술지표 + 66 캔들패턴 → Strategy Interface 확장 후보
- Security Hooks → CI/CD 시크릿 가드 패턴

#### Strategy Lifecycle (새 포트폴리오 요소)
YAML 정의 → Flink Strategy Interface 등록 → Kafka Replay 검증 → Agent-assisted analysis (학생 범위)
→ "전략을 어떻게 테스트/배포하나요?" 면접 답변의 시작점

### AI Agent + MCP 프론트엔드 레이어 (업데이트)
- **Strategy YAML DSL**: 기술지표 / 패턴 정의를 향후 선언적으로 관리하는 확장 포인트
- **MCP 서버 3개**: Market Data, Research, Strategy
- **AI Agent가 Tool로 접근하는 데이터**:
  - PostgreSQL만 (Bronze/Silver/Gold + serving snapshot + 최신 가격 + 검색 + alerts)
  - Kafka 직접 접근 없음 (CQRS Read path = PostgreSQL)
- **End-to-end 시나리오**: "급락 분석 → 재무 확인 → 리포트 요약 확인 → 전략 제안"을 대화형으로 지원
- Agent는 query/analysis 전용. 실시간 이벤트는 alert-service가 처리
- **포트폴리오 피치**: "한국시장 제약(41 슬롯) 하에서도 이벤트 파이프라인과 분석 레이어를 설계했다"

### Kubernetes 구성 (논의 완료)
- **Operator/Helm으로 인프라**: Strimzi(Kafka), Flink Operator, Bitnami(PostgreSQL/Redis), kube-prometheus-stack
- **직접 만드는 앱**: kis-ws-bridge, flink-jobs, alert-service, mcp-servers, web-app
- **Connect 커넥터 설정**: JDBC Sink config, Debezium Source config (Connect 클러스터 위에서 운영)
- **환경 분리**: Kustomize base + overlays (local/dev)
- **로컬**: kind + helmfile sync
- **힘 빼는 원칙**: Secret 기본, ClusterIP 서비스, 고정 replica, GitHub Actions CI/CD
- **클라우드 비용 예상**: GKE ~$400/mo (최소 구성)

### Agent 아키텍처 (논의 완료)
- **단계적 접근**: Phase 1 (LangGraph 단일 에이전트 + MCP) → Phase 2 (4 에이전트 + A2A 분리)
- **LangGraph StateGraph**: 명시적 노드(Agent/Tools/Guardrail), 체크포인팅, 노드별 관측성
- **A2A v1.0** (2026-03-12): Proto 기반 정규 스펙, 멀티 바인딩(JSON+HTTP/gRPC/JSON-RPC)
  - MCP = agent↔tools, A2A = agent↔agent (보완 관계)
  - Python SDK 0.3.25 안정, v1.0 Alpha (Q2 2026 정식)
  - 3개 전문 에이전트: Price, Analysis, Strategy
- **Claude Code 패턴**: while-loop + tool calls, 8-12 반복 제한, 시스템 프롬프트 매 호출 주입
- **프론트엔드**: Gradio (웹 데모) + Textual (TUI/CLI), FastAPI 백엔드
- **가드레일 필수**: 금융 에이전트 → 액션 실행 전 human/approval 노드 명시적 분리

### Scope Simplification Update (학생 프로젝트 기준)
- **Paper Trading은 현재 범위에서 제외**: 이벤트 파이프라인 + Agent 분석/질의까지 우선 구현
- **스토리지 경계 재정의**:
  - PostgreSQL = Bronze / Silver / Gold / serving / archive
  - Kafka = 실시간 이벤트 backbone
- **외부 수집 경계 분리**:
  - Realtime Ingress = KIS WebSocket (앱키당 41건 등록 한도)
  - Batch Ingress = Toss/Hankyung 수집 + enrichment
- **서비스 모델**:
  - API 구독 슬롯은 서비스 전체 구독 풀
  - 유저별 관심종목 / 필터링은 상위 서비스 레이어에서 처리
- **Phase 3 목표 변경**: Trading simulation 대신 Agent serving/query layer 구현
- **향후 확장**: `MarketDataSource`를 통해 미장(Finnhub 등) adapter 추가 가능하게 설계
