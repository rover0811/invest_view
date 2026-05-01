# Draft: KIS Realtime Ingress Implementation

## Current State (PR #12)

### What exists
- `ws_example.py` — working WebSocket POC (approval key, connect, subscribe, parse, print)
- `models/constants.py` — URLs, TR IDs, sign labels
- `models/requests.py` — `SubscribeMessage` Pydantic model (subscribe wire format)
- `models/tick.py` — `TickRecord` (H0STCNT0, 46-field index-based parsing)
- `models/orderbook.py` — `OrderBookRecord` (H0STASP0, 54-field parsing)
- `pyproject.toml` — uv project with pydantic, requests, websockets

### What design doc requires but PR doesn't have
1. **KISTokenManager** — REST access token lifecycle (발급/갱신/만료 관리)
2. **KISApprovalKeyManager** — approval key lifecycle (현재는 단순 함수 하나)
3. **KISConnectionManager** — WebSocket session lifecycle (reconnect, backoff, state transitions)
4. **KISSubscriptionPool** — desired/actual state 기반 subscription reconciliation
5. **KISWebSocketClient** — reconnect/heartbeat 포함한 managed client
6. **KISRawMessageParser** — raw message 분리 (현재 ws_example.py 인라인)
7. **KISTickParser** — raw → normalized ParsedTick 변환 (현재 TickRecord가 혼합)
8. **StockTickProducer** — Kafka stock-ticks 발행
9. **Model layering** — RawKISTickRecord → ParsedTick → StockTickEvent 분리
10. **REST client** — current-price snapshot (kis_rest_client)
11. **Recovery flow** — reconnect backoff, resubscribe, 401 handling
12. **DB schema** — realtime_subscriptions, kis_connection_sessions tables
13. **Config** — configurable subscription cap, backoff params

## Gap Summary
- PR은 "KIS WebSocket 동작 확인" POC
- Design doc은 "production-grade ingress pipeline" 전체
- 간극이 크므로, PR의 코드를 seed로 쓰되 대부분 새로 작성해야 함

## User Decisions (confirmed)

### Scope
- **이번 범위**: Kafka 직전까지 — Auth, Connection, Subscription, Parser/TickParser
- **다음 단계**: StockTickProducer + Kafka + Avro, DB 테이블
- **출력**: abstract TickSink protocol → LoggingTickSink (Kafka producer는 나중에)

### Infrastructure
- **Kafka**: 아직 없음 → 추상 인터페이스로 격리
- **PostgreSQL**: 아직 없음 → subscription state는 in-memory
- **Config**: pydantic-settings + 환경변수 (.env)

### Test Strategy
- **Framework**: pytest
- **Approach**: KIS API mock, 각 컴포넌트별 유닛테스트
- **Infrastructure setup**: pytest + pytest-asyncio 필요

### Defaults Applied
- Model layering: RawKISTickRecord → ParsedTick (design doc 따름)
- ws_example.py: 유지 (reference), production code는 별도 모듈
- SubscriptionPool: in-memory (DB interface 준비만)
- Config: pydantic-settings BaseSettings

## 추가 논의 (2차)

### async HTTP 클라이언트 — httpx 확정
- **결정**: httpx 사용
- 이유: 프로젝트가 async 중심, 향후 REST 확장 예정, 네이티브 async가 자연스러움
- pyproject.toml에 httpx 추가, requests는 ws_example.py용으로 유지

### NXT scope 변경 — IN scope로 확대
- 유저 요구: 24시간 tick 수집 → NXT 필수
  - KRX: 09:00-15:30
  - NXT: 08:00-20:00
- 영향:
  - KISRawMessageParser: H0NXCNT0, H0NXASP0 TR ID 처리
  - KISTickParser: NXT tick도 동일한 46필드 형식 파싱
  - KISSubscriptionPool: TR ID 인식 필요
  - Constants/Config: NXT URL/TR ID 포함

### Encrypted message — 사실상 비해당
- KIS 암호화는 계좌/체결통보(H0STCNI0) 같은 TR에서 발생
- H0STCNT0(체결가), H0STASP0(호가)는 공개 시세 → 평문
- 우리는 계좌 TR을 구독하지 않으므로 encrypted=1이 올 이유 없음
- **결정**: defensive guard만 유지 (encrypted=="1"이면 warning 로그 + skip). 복호화 구현 불필요.

### Type coercion + 이상 데이터 처리 전략
- 2단계 분리 확정 (raw=str, normalized=typed)
- 유저 질문: "KIS는 정해진 스키마를 보내는데, 이상한 값이 올 수 있나?"
  - KIS는 대체로 정형적이지만, 빈 문자열(""), 장 시작 전 0값, 예상 외 포맷 가능
- 유저 질문: "이상 데이터 재시도 불가 → DLQ?"
  - 현재 Kafka 없으므로 formal DLQ는 다음 단계
  - 이번 범위에서의 전략:
    - 핵심 필드(symbol, price, volume) 변환 실패 → ParseError raise + raw 첨부 + warning 로그
    - 비핵심 필드(vi_trigger_price, market_termination_code) → Optional[T], None 설정 + debug 로그
    - 에러 카운터(metrics 용) 누적
    - Kafka 추가 시 ParseError → DLQ topic으로 자연 확장 가능

### pydantic-settings + .env + DI
- 유저 확인: pydantic-settings로 .env 관리 + DI 원함

## 추가 논의 (3차) — 시간 기반 TR 전환 설계

### 문제 정의
- KRX(09:00-15:30)와 NXT(08:00-20:00)의 운영 시간이 다름
- 24시간 수집이면 시간대별로 어떤 TR을 구독할지 전환 로직이 필요
- 디자인 문서 5-2절은 "사용자 watchlist 변경"만 다루고, 시간 기반 전환은 미설계
- 유저: "서킷 브레이커처럼 유량 제어 + WS 갈아끼우기"

### 핵심 설계 질문
1. KRX+NXT 겹치는 시간(09:00-15:30)에 둘 다 구독? 하나만?
   → 둘 다면 종목당 2 subscription, 실질 cap ~20
2. 전환 기준: 시계 시간(스케줄) vs 시장 신호(NEW_MKOP_CLS_CODE)?
3. 전환 중 데이터 손실 허용 범위?
4. WS 세션은 1개만 유지? 전환 시 끊고 다시?

### 후보 설계안
- A: 듀얼 구독 (KRX+NXT 동시) — cap 낭비
- B: 시간 기반 스위칭 — 스케줄러가 TR 전환
- C: 시장 신호 반응 — NEW_MKOP_CLS_CODE 기반
- D: 스케줄 + reconciliation loop

### 유저 결정
- 겹침 시간: KRX만 (정규장 우선). NXT는 장외시간만.
- 전환 트리거: 하이브리드 — 스케줄 기본 + 시장 신호 보정 (서버 시간 의존성 고려)
- 세션 전략: 실제 테스트 필요 — 데이터 유실 가능성 때문에 경험적 검증 필요

### 시간대별 TR 매핑 (확정)
| 시간대         | 활성 시장 | 체결가 TR   | 호가 TR     |
|---------------|----------|------------|------------|
| 08:00~09:00   | NXT only | H0NXCNT0   | H0NXASP0   |
| 09:00~15:30   | KRX 우선 | H0STCNT0   | H0STASP0   |
| 15:30~20:00   | NXT only | H0NXCNT0   | H0NXASP0   |
| 20:00~08:00   | OFF      | (구독 해제) | (구독 해제) |

### 아키텍처 패턴 — Port & Adapter (Strategy)
- **Port**: `MarketSessionPort(Protocol)` — get_tick_tr_id(), get_hoga_tr_id()
- **Adapters**: `KRXSessionAdapter(MarketSessionPort)`, `NXTSessionAdapter(MarketSessionPort)` — **명시적 상속**
- **Router**: `MarketSessionRouter` — 런타임에 adapter를 swap, Pool에 TR 변경 전파
- Pool은 KRX/NXT를 모르고, Port 인터페이스만 알면 됨
- 전환 시: `router.switch_to(NXTSessionAdapter())` → Pool reconcile → ConnectionManager 실행

### 관심종목 관리 — 환경변수 기반
- `KIS_WATCH_SYMBOLS='005930,000660,035720'` 환경변수로 관리
- `KISSettings.watch_symbols: list[str]` — 시작 시 로드
- Startup: settings.watch_symbols → pool.add(symbols)
- 런타임 변경은 다음 단계 (serving layer 연동 시)

### 설계 결정 확인
- Adapter는 MarketSessionPort **명시적 상속** (Protocol structural subtyping이 아닌)
- TickSink: Kafka 전환 비용을 0으로 만들기 위한 인터페이스. 유지.
- asyncio.Lock: 단일 프로세스 async 표준 패턴. double-check 유지.
- add() 호출: 환경변수에서 로드. 인터페이스는 향후 확장 대비.
