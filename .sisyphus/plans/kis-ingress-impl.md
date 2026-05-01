# KIS Realtime Ingress — Implementation Plan

## TL;DR

> **Quick Summary**: PR #14 설계 문서 기준으로 KIS WebSocket 실시간 수집 서비스를 구현한다. monorepo 내 독립 uv workspace member로 구성, 인증 → 연결 → 구독 → 파싱 → 정규화까지 Kafka 직전 파이프라인 + DI container + entrypoint를 포함한다.
>
> **Deliverables**:
> - uv workspace 셋업 + src layout 마이그레이션
> - KIS 인증 (TokenManager, ApprovalKeyManager)
> - WebSocket 연결/재연결 (KISWebSocketClient, KISConnectionManager)
> - 구독 관리 (KISSubscriptionPool)
> - 장 세션 전환 (MarketSessionPort, Adapters, Router, Switcher)
> - 메시지 파싱 (KISRawMessageParser, KISTickParser, ParsedTick)
> - DI container + __main__.py entrypoint
> - Avro schema 파일 배치 (schemas/stock-ticks.avsc)
> - 설계 문서 반영 (D1)
>
> **Estimated Effort**: Medium-Large
> **Parallel Execution**: YES — 3 waves
> **Critical Path**: Task 1 → Task 2 → Task 6 → Task 8 → Task 9 → Task 10

---

## Context

### Source of Truth
- **PR #14** (`feat/kis-ingress-design-v2` branch): `docs/design/12-kis-realtime-ingress-design.md`
- PR body 명시: "TickSink / KISSettings 핵심 컴포넌트 제거 (과설계)"
- 상위 freeze doc: `docs/design/11-design-freeze-discussion-pack.md`

### Current Code State
```
services/kis_ingestion/
├── ws_example.py          # POC — approval key, WS 연결, 구독, 파싱, 출력
└── models/
    ├── __init__.py         # re-exports
    ├── constants.py        # APPROVAL_URL, WS_URL, SIGN_LABELS, TR_IDS
    ├── requests.py         # SubscribeMessage (Pydantic, alias 패턴)
    ├── tick.py             # TickRecord (46필드, _FIELD_ORDER, parse_payload)
    └── orderbook.py        # OrderBookRecord (54필드)
```
- `pyproject.toml` (root): 아직 workspace 아님 (members 없음)
- `schemas/`: `.gitkeep`만 존재

### Interview Decisions (확정)
- **Repo**: monorepo `services/kis_ingestion/` — uv workspace member
- **Layout**: src layout (`services/kis_ingestion/src/kis_ingestion/`)
- **DI**: `container.py` 분리, `__main__.py`는 container 호출 + signal handling
- **__init__.py**: re-export only (models/), 나머지 빈 것 안 만듦
- **ws_example.py**: 수정 가능 (import 경로 변경)
- **TickSink 제거**: TickSink, LoggingTickSink, producer.py placeholder 없음
- **KISSettings**: 핵심 컴포넌트 아님 — bootstrap input only (config.py에 단순 pydantic-settings)
- **Avro**: `schemas/stock-ticks.avsc` (루트 공유)
- **Subscription**: bootstrap 시 env에서 고정, cap 기본 40, configurable
- **Market switching**: `NEW_MKOP_CLS_CODE` signal 기반, schedule은 bootstrap만
- **QA**: 간소화 (1-2 verification 커맨드/task)
- **Final verification**: F1-F2 (2개)

---

## Work Objectives

### Core Objective
PR #14 설계 문서의 컴포넌트를 src layout 기반 독립 서비스로 구현하여, KIS WebSocket에서 실시간 체결 데이터를 수신하고 정규화된 ParsedTick까지 처리할 수 있는 상태를 만든다.

### Concrete Deliverables
- `services/kis_ingestion/pyproject.toml` — 독립 패키지
- `services/kis_ingestion/src/kis_ingestion/` — 전체 패키지
- `schemas/stock-ticks.avsc` — Kafka handoff schema (파일만)
- 모든 컴포넌트 단위 테스트

### Definition of Done
- [ ] `uv run --project services/kis_ingestion pytest` 전체 통과
- [ ] `python -m kis_ingestion` 실행 시 KIS WebSocket 연결 → 구독 → 파싱 루프 동작
- [ ] ws_example.py가 새 import 경로로 정상 동작

### Must Have
- 설계 문서 Section 4의 모든 컴포넌트 (StockTickProducer 제외)
- async lock + double-check 패턴 (TokenManager, ApprovalKeyManager)
- httpx.AsyncClient 기반 REST 호출
- desired/actual subscription state 추적
- reconnect with linear backoff + resubscribe
- NEW_MKOP_CLS_CODE signal 기반 market 전환
- Kafka header 구조: session_id (UUID) + sequence (monotonic)
- 49-field stock-ticks contract (46 tick + 3 meta)

### Must NOT Have (Guardrails)
- TickSink / LoggingTickSink — 과설계로 제거됨
- KISSettings를 핵심 아키텍처 컴포넌트로 격상 — bootstrap input only
- producer.py placeholder 파일 — Kafka 구현은 다음 단계
- 빈 __init__.py 파일 — re-export 용도만
- REST snapshot (current-price) 호출 — v1 범위 아님
- 장 캘린더 관리 — signal 기반으로 불필요
- Sisyphus trailer in commits

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — 모든 검증은 agent-executed.

### Test Decision
- **Infrastructure exists**: NO (신규 셋업)
- **Automated tests**: YES (tests-after)
- **Framework**: pytest + pytest-asyncio
- **Test 위치**: `services/kis_ingestion/tests/`

### QA Policy
- 각 task마다 1-2개 검증 커맨드
- UI 없음 — 전부 CLI/pytest 기반

---

## Execution Strategy

### Target Directory Tree (최종)
```
invest_view/                           # monorepo root
├── pyproject.toml                     # workspace root (members 추가)
├── schemas/
│   └── stock-ticks.avsc               # 49-field Avro schema
├── services/
│   └── kis_ingestion/
│       ├── pyproject.toml             # 독립 패키지
│       ├── ws_example.py              # POC (import 경로 갱신)
│       ├── src/
│       │   └── kis_ingestion/
│       │       ├── __main__.py        # entrypoint
│       │       ├── container.py       # DI container
│       │       ├── config.py          # pydantic-settings
│       │       ├── token_manager.py   # KISTokenManager
│       │       ├── approval_key_manager.py  # KISApprovalKeyManager
│       │       ├── ws_client.py       # KISWebSocketClient
│       │       ├── connection_manager.py    # KISConnectionManager
│       │       ├── subscription_pool.py     # KISSubscriptionPool
│       │       ├── market_session.py  # Port + Adapters + Router + Switcher
│       │       ├── raw_parser.py      # KISRawMessageParser
│       │       ├── tick_parser.py     # KISTickParser + ParsedTick
│       │       └── models/
│       │           ├── __init__.py    # re-export only
│       │           ├── constants.py
│       │           ├── requests.py
│       │           ├── tick.py
│       │           └── orderbook.py
│       └── tests/
│           ├── conftest.py
│           ├── test_config.py
│           ├── test_token_manager.py
│           ├── test_approval_key_manager.py
│           ├── test_raw_parser.py
│           ├── test_tick_parser.py
│           ├── test_subscription_pool.py
│           ├── test_market_session.py
│           ├── test_connection_manager.py
│           └── test_ws_client.py
```

### Parallel Execution Waves

```
Wave 1 (Foundation — 동시 실행):
├── Task 1: Workspace + src layout 셋업 + models 이전 [quick]
├── Task 2: config.py + Avro schema 파일 배치 [quick]

Wave 2 (Core Components — Task 1 완료 후, 서로 독립적으로 동시 실행):
├── Task 3: KISTokenManager [unspecified-high]
├── Task 4: KISApprovalKeyManager [unspecified-high]
├── Task 5: KISSubscriptionPool [unspecified-high]
├── Task 6: Market Session (Port + Adapters + Router + Switcher) [unspecified-high]
├── Task 7: KISRawMessageParser + KISTickParser [unspecified-high]

Wave 3 (Integration — Wave 2 완료 후):
├── Task 8: KISWebSocketClient [deep]
├── Task 9: KISConnectionManager [deep]
├── Task 10: DI container + __main__.py + ws_example.py 경로 갱신 [unspecified-high]
├── Task 11: 설계 문서 반영 (D1) [quick]

Wave FINAL (Review):
├── F1: Plan compliance + code quality audit [oracle]
├── F2: Scope fidelity + real QA [unspecified-high]
→ Present results → Get user okay
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| 1 | — | 2,3,4,5,6,7 |
| 2 | 1 | 10 |
| 3 | 1 | 9,10 |
| 4 | 1 | 9,10 |
| 5 | 1 | 9,10 |
| 6 | 1 | 9,10 |
| 7 | 1 | 9,10 |
| 8 | 1 | 9 |
| 9 | 3,4,5,6,7,8 | 10 |
| 10 | 2,9 | F1,F2 |
| 11 | 1 | — |
| F1 | 10 | — |
| F2 | 10 | — |

### Agent Dispatch Summary

- **Wave 1**: 2 tasks — T1 `quick`, T2 `quick`
- **Wave 2**: 5 tasks — T3-T7 `unspecified-high`
- **Wave 3**: 4 tasks — T8 `deep`, T9 `deep`, T10 `unspecified-high`, T11 `quick`
- **FINAL**: 2 tasks — F1 `oracle`, F2 `unspecified-high`

---

## TODOs

- [ ] 1. Workspace 셋업 + src layout 마이그레이션

  **What to do**:
  - 루트 `pyproject.toml`에 `[tool.uv.workspace]` 추가: `members = ["services/*"]`
  - `services/kis_ingestion/pyproject.toml` 생성: name=`kis-ingestion`, Python>=3.11, dependencies=[pydantic, httpx, websockets, pydantic-settings]
  - `services/kis_ingestion/src/kis_ingestion/` 디렉토리 생성
  - 기존 `services/kis_ingestion/models/` → `services/kis_ingestion/src/kis_ingestion/models/`로 이동 (constants.py, requests.py, tick.py, orderbook.py, __init__.py)
  - `ws_example.py`는 `services/kis_ingestion/ws_example.py`에 유지하되, import 경로를 `from models import ...` → `from kis_ingestion.models import ...`로 변경
  - `uv sync` 실행하여 workspace 정상 동작 확인

  **Must NOT do**:
  - 빈 __init__.py 파일 생성 (models/__init__.py만 유지)
  - 기존 모델 코드 내용 변경 (경로만 이동)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Task 2와 동시 — 단, Task 2는 Task 1의 디렉토리 생성에 의존하므로 실제로는 Task 1 먼저)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 2,3,4,5,6,7,8,11
  - **Blocked By**: None

  **References**:
  - `pyproject.toml:1-11` — 현재 루트 pyproject.toml, workspace 설정 추가 대상
  - `services/kis_ingestion/models/__init__.py:1-14` — 기존 re-export 패턴, 그대로 유지
  - `services/kis_ingestion/ws_example.py:23-30` — `from models import ...` 라인, import 경로 변경 대상
  - uv workspace 문서: https://docs.astral.sh/uv/concepts/projects/workspaces/

  **Acceptance Criteria**:
  - [ ] `uv sync` 성공
  - [ ] `uv run --project services/kis_ingestion python -c "from kis_ingestion.models import TickRecord, SubscribeMessage, TR_IDS"` 성공
  - [ ] `services/kis_ingestion/models/` 디렉토리 없음 (src/로 이동 완료)

  **Commit**: YES
  - Message: `feat(kis-ingestion): scaffold uv workspace member with src layout`

- [ ] 2. Config + Avro schema 파일 배치

  **What to do**:
  - `services/kis_ingestion/src/kis_ingestion/config.py` 생성
    - `pydantic-settings` 기반 `KISConfig` 클래스: `app_key`, `app_secret`, `ws_url` (default WS_URL), `approval_url` (default APPROVAL_URL), `watch_symbols` (list[str], env에서 JSON string으로), `subscription_cap` (int, default 40)
    - env prefix: `KIS_`
    - 핵심 컴포넌트가 아닌 bootstrap input — 단순하게 유지
  - `schemas/stock-ticks.avsc` 생성
    - 설계 문서 Section 7의 49-field contract 기반 Avro schema
    - 구현이 아닌 contract 문서 용도 — 파일 배치만
  - `services/kis_ingestion/tests/test_config.py` 작성
    - env에서 정상 로드 테스트
    - watch_symbols JSON parsing 테스트
    - subscription_cap default 테스트

  **Must NOT do**:
  - KISSettings를 핵심 아키텍처 컴포넌트로 격상
  - config.py에 로직 추가 (순수 데이터 홀더만)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (Task 1의 디렉토리 구조 필요)
  - **Parallel Group**: Wave 1 (Task 1 직후)
  - **Blocks**: Task 10
  - **Blocked By**: Task 1

  **References**:
  - 설계 문서 Section 7 (`stock-ticks` v1 contract) — 49-field schema 정의, Avro schema의 field 목록과 타입
  - 설계 문서 Section 6-4 — `KIS_WATCH_SYMBOLS` JSON string 형식 권장
  - `services/kis_ingestion/models/constants.py:1-2` — APPROVAL_URL, WS_URL default 값
  - 설계 문서 Section 2 (confirmed operational facts) — endpoint URLs

  **Acceptance Criteria**:
  - [ ] `uv run --project services/kis_ingestion pytest tests/test_config.py` 통과
  - [ ] `schemas/stock-ticks.avsc` 파일 존재, JSON valid

  **Commit**: YES
  - Message: `feat(kis-ingestion): add config and stock-ticks avro schema`

- [ ] 3. KISTokenManager

  **What to do**:
  - `services/kis_ingestion/src/kis_ingestion/token_manager.py` 생성
  - REST access token 발급/갱신 담당
  - `httpx.AsyncClient` 기반 `POST /oauth2/tokenP` 호출
  - async lock + double-check 패턴: 동시 refresh 방지
  - 만료 시각 추적 + refresh buffer (수십 초 전 갱신)
  - 401 시 강제 갱신 경로
  - `services/kis_ingestion/tests/test_token_manager.py` 작성
    - happy path: 토큰 발급 + 캐싱 + 만료 후 갱신
    - 동시 호출 시 single request (lock 검증)
    - 401 강제 갱신

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Tasks 4,5,6,7과 동시)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 9
  - **Blocked By**: Task 1

  **References**:
  - 설계 문서 Section 5-1.a — REST token request JSON format (`grant_type`, `appkey`, `appsecret`)
  - 설계 문서 Section 2 (confirmed operational facts) — `POST /oauth2/tokenP`, TTL 24h, 6시간 내 재발급 시 동일 토큰
  - 설계 문서 Section 4 (implementation recommendations) — async lock + double-check, httpx.AsyncClient, refresh buffer 수십 초
  - `services/kis_ingestion/ws_example.py:33-46` — 기존 sync approval key 발급 코드 (참고용, token endpoint는 다름)

  **Acceptance Criteria**:
  - [ ] `uv run --project services/kis_ingestion pytest tests/test_token_manager.py` 통과

  **Commit**: YES (Task 4와 묶음)
  - Message: `feat(kis-ingestion): implement auth managers (token + approval key)`

- [ ] 4. KISApprovalKeyManager

  **What to do**:
  - `services/kis_ingestion/src/kis_ingestion/approval_key_manager.py` 생성
  - WebSocket approval key 발급/갱신 담당
  - `httpx.AsyncClient` 기반 `POST /oauth2/Approval` 호출
  - 주의: request body field는 `secretkey` (appsecret 아님)
  - async lock + double-check 패턴
  - 만료 시각 추적 + refresh buffer (token보다 더 넉넉하게)
  - `services/kis_ingestion/tests/test_approval_key_manager.py` 작성

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Tasks 3,5,6,7과 동시)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 9
  - **Blocked By**: Task 1

  **References**:
  - 설계 문서 Section 5-1.a — approval key request JSON (`grant_type`, `appkey`, `secretkey`)
  - 설계 문서 Section 2 — `POST /oauth2/Approval`, TTL 24h, `secretkey` 필드명
  - `services/kis_ingestion/ws_example.py:33-46` — 기존 `get_approval_key()` sync 구현 (async로 재작성 기준)
  - `services/kis_ingestion/models/constants.py:1` — APPROVAL_URL 값

  **Acceptance Criteria**:
  - [ ] `uv run --project services/kis_ingestion pytest tests/test_approval_key_manager.py` 통과

  **Commit**: YES (Task 3과 묶음)

- [ ] 5. KISSubscriptionPool

  **What to do**:
  - `services/kis_ingestion/src/kis_ingestion/subscription_pool.py` 생성
  - desired state manager: bootstrap 시 env에서 고정 종목 세트 로드
  - `desired` set과 `actual` set 분리 추적
  - `diff()` → subscribe/unsubscribe 목록 산출
  - cap 초과 시 reject (ValueError)
  - market 전환 시 tr_id 일괄 교체: `switch_market(adapter)` → desired set의 tr_id를 새 adapter 기준으로 갱신
  - `services/kis_ingestion/tests/test_subscription_pool.py` 작성
    - desired/actual 동기화
    - cap 초과 reject
    - diff 산출 (subscribe/unsubscribe 목록)
    - market switch 시 tr_id 교체

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Tasks 3,4,6,7과 동시)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 9
  - **Blocked By**: Task 1

  **References**:
  - 설계 문서 Section 4 — KISSubscriptionPool: "유저 관심종목 union -> KIS 등록 대상 집합 관리"
  - 설계 문서 Section 5-2 — subscription reconciliation flow: "v1에서 pool은 bootstrap 시 고정", cap 초과 시 reject
  - 설계 문서 Section 5-2.a — market switching: "KISSubscriptionPool은 기존 desired set의 tr_id를 새 adapter 기준으로 갱신"
  - 설계 문서 Section 5-3.a — subscribe semantics: desired state manager, `tr_type=1` subscribe / `tr_type=2` unsubscribe
  - `services/kis_ingestion/models/requests.py:21-33` — SubscribeMessage 구조 (subscribe/unsubscribe wire format)

  **Acceptance Criteria**:
  - [ ] `uv run --project services/kis_ingestion pytest tests/test_subscription_pool.py` 통과

  **Commit**: YES (Task 6과 묶음)
  - Message: `feat(kis-ingestion): implement subscription pool and market session`

- [ ] 6. Market Session (Port + Adapters + Router + Switcher)

  **What to do**:
  - `services/kis_ingestion/src/kis_ingestion/market_session.py` 생성 (한 파일에 전부)
  - `MarketSessionPort` (Protocol): 현재 활성 market의 TR ID 집합 반환
  - `KRXSessionAdapter`: tick=H0STCNT0, hoga=H0STASP0
  - `NXTSessionAdapter`: tick=H0NXCNT0, hoga=H0NXASP0
  - `MarketSessionRouter`: 런타임에 active adapter 교체, subscription pool에 switch_market 호출
  - `ScheduleBasedSessionSwitcher`: 시간대 기준 초기 market 결정 (08:00-09:00 NXT, 09:00-15:30 KRX, 15:30-20:00 NXT)
  - 런타임 전환: tick의 `NEW_MKOP_CLS_CODE` signal 기반 → Router가 adapter 교체
  - `services/kis_ingestion/tests/test_market_session.py` 작성
    - Adapter TR ID 반환
    - Switcher 시간대별 market 결정
    - Router adapter 교체 + pool switch 연동

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Tasks 3,4,5,7과 동시)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 9
  - **Blocked By**: Task 1

  **References**:
  - 설계 문서 Section 4 — MarketSessionPort, KRXSessionAdapter, NXTSessionAdapter, MarketSessionRouter, ScheduleBasedSessionSwitcher 정의
  - 설계 문서 Section 5-2.a — market session switching 확정: schedule은 bootstrap만, 런타임은 `NEW_MKOP_CLS_CODE` signal, unsub 먼저 → sub 나중, 2-3초 빈 구간 허용
  - 설계 문서 Section 2 (Clarification) — 운영 시간대: 08:00-09:00 NXT, 09:00-15:30 KRX 우선, 15:30-20:00 NXT
  - 설계 문서 Section 6-1.a — TR ID 매핑 테이블
  - `services/kis_ingestion/models/constants.py:16-21` — TR_IDS dict (기존 정의)

  **Acceptance Criteria**:
  - [ ] `uv run --project services/kis_ingestion pytest tests/test_market_session.py` 통과

  **Commit**: YES (Task 5와 묶음)

- [ ] 7. KISRawMessageParser + KISTickParser

  **What to do**:
  - `services/kis_ingestion/src/kis_ingestion/raw_parser.py` 생성
    - `KISRawMessageParser`: `0|TR_ID|count|payload` envelope 해석 → `RawKISMessage` (encrypted, tr_id, count, payload)
    - record splitting: payload를 `^` 기준 분리 → `list[str]` records
    - PINGPONG 메시지 감지
    - JSON 응답 (subscribe ACK/NACK) 감지
  - `services/kis_ingestion/src/kis_ingestion/tick_parser.py` 생성
    - `KISTickParser`: raw record string → `ParsedTick` 정규화
    - `ParsedTick`: 49-field 모델 (46 tick + source_tr_id, market, received_at)
    - 기존 `TickRecord`의 `_FIELD_ORDER`와 `parse_payload` 로직을 활용하되, meta 3개 필드 추가
    - 가격/비율은 Decimal, 수량은 int, 시각은 string 유지 (설계 문서 Section 6-4)
  - `services/kis_ingestion/tests/test_raw_parser.py` 작성
    - envelope parse, record split, PINGPONG 감지, JSON 응답 감지
  - `services/kis_ingestion/tests/test_tick_parser.py` 작성
    - 46-field string → ParsedTick 변환, 타입 정확성

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Tasks 3,4,5,6과 동시)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 9
  - **Blocked By**: Task 1

  **References**:
  - 설계 문서 Section 4 — KISRawMessageParser: "envelope parse + record splitting까지만", KISTickParser: "field index 기반 정규화"
  - 설계 문서 Section 5-3 — runtime tick flow: raw → parser → tick parser → producer
  - 설계 문서 Section 6 (recommended model layering) — RawKISResponse, RawKISTickRecord, ParsedTick 모델 분리
  - 설계 문서 Section 7 — stock-ticks v1 contract 49-field 테이블 (field name, KIS wire name, type)
  - `services/kis_ingestion/models/tick.py:1-99` — 기존 TickRecord (46필드, _FIELD_ORDER, parse_payload) — ParsedTick의 base
  - `services/kis_ingestion/ws_example.py:89-103` — 기존 envelope parsing 로직 (0|1 → parts split, encrypted 체크)
  - 설계 문서 Section 6-4 — 가격 Decimal, 수량 int, 시각 string

  **Acceptance Criteria**:
  - [ ] `uv run --project services/kis_ingestion pytest tests/test_raw_parser.py tests/test_tick_parser.py` 통과

  **Commit**: YES
  - Message: `feat(kis-ingestion): implement raw parser and tick parser`

- [ ] 8. KISWebSocketClient

  **What to do**:
  - `services/kis_ingestion/src/kis_ingestion/ws_client.py` 생성
  - `websockets` 라이브러리 기반 WebSocket 클라이언트
  - 단일 연결 관리: connect, disconnect
  - PINGPONG 자동 응답 (pong)
  - message receive loop → callback/async iterator 패턴
  - subscribe/unsubscribe 메시지 전송 (SubscribeMessage 활용)
  - 연결 상태 추적 (connected/disconnected)
  - `services/kis_ingestion/tests/test_ws_client.py` 작성
    - mock WebSocket으로 connect/disconnect, message receive, PINGPONG 응답 테스트

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2와 동시 가능하나, 설계상 Wave 3으로 분류)
  - **Parallel Group**: Wave 3 (Task 9와 동시)
  - **Blocks**: Task 9
  - **Blocked By**: Task 1

  **References**:
  - 설계 문서 Section 4 — KISWebSocketClient: "KIS 실시간 메시지 수신 및 reconnect/heartbeat 처리"
  - 설계 문서 Section 2 — WebSocket endpoint: `ws://ops.koreainvestment.com:21000`, plain-text
  - 설계 문서 Section 5-3.a — subscribe/unsubscribe wire format (JSON header/body)
  - `services/kis_ingestion/ws_example.py:66-119` — 기존 websockets 사용 패턴 (connect, recv, PINGPONG, subscribe)
  - `services/kis_ingestion/models/requests.py:21-33` — SubscribeMessage.subscribe(), to_wire()

  **Acceptance Criteria**:
  - [ ] `uv run --project services/kis_ingestion pytest tests/test_ws_client.py` 통과

  **Commit**: YES (Task 9와 묶음)
  - Message: `feat(kis-ingestion): implement ws client and connection manager`

- [ ] 9. KISConnectionManager

  **What to do**:
  - `services/kis_ingestion/src/kis_ingestion/connection_manager.py` 생성
  - 1세션 제약 하에서 연결 lifecycle 총괄 관리
  - 조합 대상: KISApprovalKeyManager, KISWebSocketClient, KISSubscriptionPool, KISRawMessageParser, KISTickParser, MarketSessionRouter
  - startup flow: approval key → ws connect → subscribe pool → receive loop
  - reconnect: 짧은 선형 backoff, resubscribe completed를 별도 성공 조건으로
  - 401/인가 실패 → token/approval key 강제 갱신
  - desired subscription registry 유지 — reconnect 후 자동 재등록
  - session_id (UUID) 갱신 + sequence 0 리셋 (reconnect 시)
  - tick 수신 → raw_parser → tick_parser → 로깅 (Kafka 전 단계)
  - NEW_MKOP_CLS_CODE signal 감지 → MarketSessionRouter.switch() 호출
  - `services/kis_ingestion/tests/test_connection_manager.py` 작성
    - startup flow mock 테스트
    - reconnect + resubscribe 시나리오
    - session_id 갱신 확인

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (Wave 2 전체 + Task 8 필요)
  - **Parallel Group**: Wave 3 (Task 8 완료 후)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 3,4,5,6,7,8

  **References**:
  - 설계 문서 Section 4 — KISConnectionManager: "1세션 제약 하에서 연결 lifecycle 관리", "desired subscription registry 유지"
  - 설계 문서 Section 5-1 — startup flow 전체 (auth bootstrap → connect → subscribe → receive)
  - 설계 문서 Section 5-4 — recovery flow: 재연결 backoff, approval key 갱신, desired set 기준 재등록, session_id/sequence 관리
  - 설계 문서 Section 5-2.a — market switching: unsub 먼저 → sub 나중, NEW_MKOP_CLS_CODE signal
  - 설계 문서 Section 4 (implementation recommendations) — "reconnect 성공만으로 끝내지 말고 resubscribe completed를 별도 성공 조건으로"
  - 설계 문서 Section 7 (Kafka record 구조) — session_id (UUID), sequence (monotonic), gap 감지용

  **Acceptance Criteria**:
  - [ ] `uv run --project services/kis_ingestion pytest tests/test_connection_manager.py` 통과

  **Commit**: YES (Task 8과 묶음)

- [ ] 10. DI Container + Entrypoint + ws_example.py 경로 갱신

  **What to do**:
  - `services/kis_ingestion/src/kis_ingestion/container.py` 생성
    - `create_container(config: KISConfig) -> Container` 또는 단순 factory function
    - 객체 그래프 조립: config → auth managers → subscription pool → market session → parsers → ws client → connection manager
    - 별도 DI 프레임워크 없이 plain factory 패턴
  - `services/kis_ingestion/src/kis_ingestion/__main__.py` 생성
    - `python -m kis_ingestion` entrypoint
    - config 로드 (KISConfig from env)
    - container 생성
    - asyncio.run + signal handling (SIGINT, SIGTERM → graceful shutdown)
  - `services/kis_ingestion/ws_example.py` import 경로 최종 확인
    - `from kis_ingestion.models import ...` 가 정상 동작하는지 확인 (Task 1에서 이미 변경했을 것이지만, 최종 검증)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (전체 컴포넌트 필요)
  - **Parallel Group**: Wave 3 (마지막)
  - **Blocks**: F1, F2
  - **Blocked By**: Tasks 2, 9

  **References**:
  - 인터뷰 결정: "별도 container.py" — DI container 분리, __main__.py는 container 호출 + signal handling만
  - 설계 문서 Section 5-1 — startup flow: config → token warm-up → approval key → market router → connect → subscribe → receive
  - 설계 문서 Section 4 (design intent) — ".env에서 읽은 값은 auth bootstrap 단계에서 필요한 입력만 auth 관련 객체에 주입"
  - 설계 문서 Section 4 (mermaid diagram) — 컴포넌트 간 의존성 그래프 전체

  **Acceptance Criteria**:
  - [ ] `uv run --project services/kis_ingestion python -c "from kis_ingestion.container import create_container"` 성공
  - [ ] `uv run --project services/kis_ingestion python -m kis_ingestion --help` 또는 env 없이 실행 시 graceful error

  **Commit**: YES
  - Message: `feat(kis-ingestion): wire DI container and entrypoint`

- [ ] 11. 설계 문서 반영 (D1)

  **What to do**:
  - `docs/design/12-kis-realtime-ingress-design.md`를 구현 결과 기준으로 갱신
    - Section 4 component definitions: 실제 파일 경로 추가
    - Section 9 resolved decisions: 구현 확정 사항 반영
    - 구현 과정에서 발견된 설계 변경 사항 반영
  - **주의**: PR #14 브랜치의 파일을 직접 수정하는 것이 아니라, main 브랜치 기준으로 작업. PR #14 머지 여부와 관계없이 현재 브랜치에서 문서 갱신.

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (다른 Wave 3 태스크와)
  - **Parallel Group**: Wave 3
  - **Blocks**: None
  - **Blocked By**: Task 1 (최소한 구조 확정 후)

  **References**:
  - `docs/design/12-kis-realtime-ingress-design.md` — 현재 main 브랜치 버전
  - PR #14 브랜치 버전: `git show feat/kis-ingress-design-v2:docs/design/12-kis-realtime-ingress-design.md`
  - 구현된 전체 컴포넌트 파일 경로 (Task 1-10 결과)

  **Acceptance Criteria**:
  - [ ] 설계 문서의 component definitions에 실제 파일 경로가 기재되어 있음

  **Commit**: YES
  - Message: `docs(kis-ingestion): align design doc with implementation`

---

## Final Verification Wave

- [ ] F1. **Plan Compliance + Code Quality Audit** — `oracle`
  Read the plan. 각 Must Have 검증: 파일 존재, import 가능, 테스트 통과. 각 Must NOT Have 검증: codebase에 TickSink/LoggingTickSink/producer.py 없음. `uv run --project services/kis_ingestion pytest` 전체 통과. linting/type errors 체크.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tests [PASS/FAIL] | VERDICT`

- [ ] F2. **Scope Fidelity + Real QA** — `unspecified-high`
  각 task의 "What to do" vs 실제 diff 1:1 비교. `python -m kis_ingestion` 실행 테스트 (env 없이 graceful fail 확인). ws_example.py import 경로 확인. 설계 문서 Section 4 컴포넌트 전수 체크.
  Output: `Tasks [N/N compliant] | Components [N/N] | VERDICT`

---

## Commit Strategy

| After Task | Message | Files |
|-----------|---------|-------|
| 1 | `feat(kis-ingestion): scaffold uv workspace member with src layout` | pyproject.toml (root+service), src/kis_ingestion/models/*, ws_example.py |
| 2 | `feat(kis-ingestion): add config and stock-ticks avro schema` | config.py, schemas/stock-ticks.avsc |
| 3+4 | `feat(kis-ingestion): implement auth managers (token + approval key)` | token_manager.py, approval_key_manager.py, tests |
| 5+6 | `feat(kis-ingestion): implement subscription pool and market session` | subscription_pool.py, market_session.py, tests |
| 7 | `feat(kis-ingestion): implement raw parser and tick parser` | raw_parser.py, tick_parser.py, tests |
| 8+9 | `feat(kis-ingestion): implement ws client and connection manager` | ws_client.py, connection_manager.py, tests |
| 10 | `feat(kis-ingestion): wire DI container and entrypoint` | container.py, __main__.py, ws_example.py |
| 11 | `docs(kis-ingestion): align design doc with implementation` | docs/design/12-*.md |

---

## Success Criteria

### Verification Commands
```bash
uv run --project services/kis_ingestion pytest          # 전체 테스트 통과
uv run --project services/kis_ingestion python -c "from kis_ingestion.container import create_container"  # import 성공
uv run python services/kis_ingestion/ws_example.py --help  # POC 정상 동작
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] 설계 문서 Section 4 컴포넌트 전수 구현 (StockTickProducer 제외)
