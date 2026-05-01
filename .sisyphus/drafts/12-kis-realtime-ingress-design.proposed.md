---
aliases:
tags:
created: 2026-04-29 02:53:52
---

# 12 KIS Realtime Ingress Design

이 문서는 두드림 v1의 **KIS scope 전용 상세 설계**다. 상위 의사결정은 [[11-design-freeze-discussion-pack]]를 따르되, **실시간 ingress 경계는 이 문서를 source of truth**로 본다.

## Freeze Sync Extract

- 실시간 ingress source는 **KIS only**다.
- 운영 기준은 **앱키당 1세션 / 최신 메모 기준 41건 등록 한도**다. 다만 구현 상수는 40/41 차이를 흡수하도록 configurable cap으로 둔다.
- access token과 WebSocket approval key는 **분리된 lifecycle**로 관리한다.
- KIS scope의 책임은 **정규화된 `stock-ticks` 발행까지**이며, 이후 Flink/serving은 다른 scope다.

## 0. Scope Boundary

### In Scope

- KIS Open API 인증/인가 흐름 정리
- KIS WebSocket 연결/재연결/heartbeat 관리
- 서비스 단위 실시간 구독 풀 관리 (최대 41종목)
- raw payload 수신 및 파싱
- KIS raw message -> normalized tick event 변환
- Kafka `stock-ticks` 발행
- 장 상태/거래정지/VI 관련 필드 해석

### Out of Scope

- Flink 윈도우/CEP 로직
- alert / pattern 생성 규칙
- WebSocket 사용자 전달
- Toss / Hankyung batch enrichment
- Agent query path
- paper trading

### Boundary Statement

KIS scope의 책임은 **한국투자증권 Open API로부터 실시간 데이터를 안정적으로 받아, 서비스 전체 구독 풀을 관리하고, downstream이 바로 소비 가능한 정규화 이벤트를 Kafka에 발행하는 것**까지다.

## 1. Why separate this file

- 현재 `[[11-design-freeze-discussion-pack]]`는 KIS, Flink, serving, batch, reliability를 한 문서에 같이 담고 있다.
- 하지만 KIS 쪽은 외부 API 제약, 인증 수명, WebSocket 세션 관리, raw payload 해석처럼 **외부 의존성과 운영 리스크가 가장 강한 영역**이다.
- 따라서 팀 논의 기준으로는 먼저 KIS를 확정한 뒤, 이후 scope 문서를 **stream / serving / batch**로 나누는 편이 맞다.

## 2. External constraints

- 실시간 ingress는 **KIS only**다.
- KIS WebSocket은 **앱키당 1세션 / 총 41건 등록 한도**를 전제로 설계한다.
- 서비스는 다중 유저지만, KIS 구독은 **유저별이 아니라 서비스 전체 subscription pool**로 관리한다.
- REST access token은 별도 lifecycle을 가지며, WebSocket approval key와 분리해서 관리한다.
- access token / approval key는 둘 다 24시간 수명을 전제로 운영한다.
- Toss / Hankyung은 비실시간 enrichment source이며 KIS scope 밖이다.
- 전종목 실시간 커버리지는 현재 범위에서 불가능하다.

### Confirmed operational facts

| 항목 | 값 / 규칙 |
| --- | --- |
| REST token endpoint | `POST /oauth2/tokenP` |
| WebSocket approval endpoint | `POST /oauth2/Approval` |
| REST token TTL | 24시간 (`expires_in=86400`) |
| Token 재발급 특성 | 6시간 이내 재발급 시 동일 토큰 반환 패턴이 있음 |
| Approval key TTL | 24시간 |
| Approval key request field | `secretkey` 사용 (`appsecret` 아님) |
| WebSocket prod endpoint | `ws://ops.koreainvestment.com:21000` |
| WebSocket paper endpoint | `ws://ops.koreainvestment.com:31000` |
| REST current-price endpoint | `GET /uapi/domestic-stock/v1/quotations/inquire-price` |
| KRX realtime tick TR ID | `H0STCNT0` |
| KRX realtime orderbook TR ID | `H0STASP0` |
| NXT realtime tick TR ID | `H0NXCNT0` |
| NXT realtime orderbook TR ID | `H0NXASP0` |
| WebSocket transport | `ws://` plain-text (public realtime channel) |

### Known ambiguity to carry forward

- 최신 설계 문서와 운영 메모는 **41건 등록 한도**를 기준으로 잡고 있다.
- 다만 일부 공식 샘플/구현 패턴은 **40개 구독**을 기준으로 설명한다.
- 따라서 구현에서는 hard-coded 상수 하나로 박지 말고, **configurable subscription cap**으로 두는 것이 안전하다.

### Clarification

- 현재 논의에서 말하는 "24시간 수집"은 literal 24/7이 아니라 **KRX + NXT extended-hours coverage**를 뜻한다.
- 운영 기본 정책은 **08:00~09:00 NXT, 09:00~15:30 KRX 우선, 15:30~20:00 NXT**다.
- 09:00~15:30 겹침 구간에서는 KRX와 NXT를 동시에 물지 않고, **KRX 우선 단일 활성 market**으로 본다.
- 공개 시세 WebSocket 채널은 `ws://` 기반 plain-text라는 점을 **설계 선택이 아니라 외부 API 제약**으로 문서화한다.

## 3. KIS scope responsibilities

| 책임 | 설명 |
| --- | --- |
| Auth bootstrap | access token / approval key 발급 및 만료 관리 |
| Session management | WebSocket 연결 생성, 재연결, heartbeat, 상태 전이 관리 |
| Subscription reconciliation | watchlist union을 KIS 등록 가능한 실시간 구독 풀로 반영 |
| Payload parsing | raw KIS 문자열 payload를 record 단위로 확장 |
| Normalization | KIS 특화 필드를 domain tick event로 정규화 |
| Publishing | 정규화된 tick을 Kafka `stock-ticks`에 발행 |
| Guardrails | 장 상태/거래정지/VI 필드 해석, rate limit / reconnect 안전장치 |

## 4. Proposed components

```mermaid
flowchart LR
    KIS[KIS Open API]
    Settings[KISSettings]
    Token[KISTokenManager]
    Approval[KISApprovalKeyManager]
    Conn[KISConnectionManager]
    Pool[KISSubscriptionPool]
    SessionPort[MarketSessionPort]
    Router[MarketSessionRouter]
    Switcher[ScheduleBasedSessionSwitcher]
    KRX[KRXSessionAdapter]
    NXT[NXTSessionAdapter]
    WS[KISWebSocketClient]
    Parser[KISRawMessageParser]
    TickParser[KISTickParser]
    Sink[TickSink]
    Producer[StockTickProducer]
    Kafka[(Kafka: stock-ticks)]

    Settings --> Token
    Settings --> Approval
    Settings --> Pool
    Settings --> Conn
    KIS --> Token
    KIS --> Approval
    Token --> Conn
    Approval --> Conn
    Switcher --> Router
    Router --> SessionPort
    KRX --> SessionPort
    NXT --> SessionPort
    Router --> Pool
    Pool --> Conn
    Conn --> WS
    WS --> Parser
    Parser --> TickParser
    TickParser --> Sink
    Sink --> Producer
    Producer --> Kafka
```

### Component definitions

| Component | Responsibility |
| --- | --- |
| `KISTokenManager` | REST access token 발급/갱신 담당 |
| `KISApprovalKeyManager` | WebSocket approval key 발급/갱신 담당 |
| `KISSettings` | auth / websocket / subscription cap / watch symbols bootstrap config |
| `KISConnectionManager` | 1세션 제약 하에서 연결 lifecycle 관리 |
| `KISSubscriptionPool` | 유저 관심종목 union -> KIS 등록 대상 집합 관리 |
| `MarketSessionPort` | 현재 활성 market이 어떤 TR ID 집합을 쓰는지 추상화 |
| `KRXSessionAdapter` | KRX 기준 tick/hoga TR ID 제공 |
| `NXTSessionAdapter` | NXT 기준 tick/hoga TR ID 제공 |
| `MarketSessionRouter` | 런타임에 활성 market adapter를 교체하고 subscription pool에 반영 |
| `ScheduleBasedSessionSwitcher` | 시간대 기준으로 KRX/NXT 전환 baseline 계산 |
| `KISWebSocketClient` | KIS 실시간 메시지 수신 및 reconnect/heartbeat 처리 |
| `KISRawMessageParser` | `0|TR_ID|건수|payload` envelope를 해석하고 raw record string list로 분해 |
| `KISTickParser` | KIS 인덱스 기반 필드를 domain event로 정규화 |
| `TickSink` | normalized tick handoff abstraction |
| `StockTickProducer` | `TickSink`를 구현하는 Kafka handoff adapter |

### KIS scope ERD / schema slice

`[[11-design-freeze-discussion-pack]]`의 ERD는 프로젝트 전체 기준이고, 아래 다이어그램은 그중 **KIS scope가 직접 책임지는 부분만 잘라낸 slice**다.

```mermaid
erDiagram
    SYMBOL ||--o| REALTIME_SUBSCRIPTION : is_target
    KIS_CONNECTION_SESSION ||--o{ REALTIME_SUBSCRIPTION : serves
    SYMBOL ||--o{ TICK_HISTORY : produces
    KIS_CONNECTION_SESSION ||--o{ TICK_HISTORY : ingests

    SYMBOL {
        string symbol PK
        string market
        string name
        boolean is_active
    }
    KIS_CONNECTION_SESSION {
        uuid connection_session_id PK
        string environment
        string session_status
        int subscription_cap
        timestamptz connected_at
        timestamptz last_heartbeat_at
    }
    REALTIME_SUBSCRIPTION {
        string symbol PK
        uuid connection_session_id FK
        string source
        string tr_id
        string desired_status
        string actual_status
        int priority
        timestamptz refreshed_at
    }
    TICK_HISTORY {
        string event_id PK
        string symbol FK
        uuid connection_session_id FK
        string raw_tr_id
        timestamptz event_time
        decimal price
        bigint volume
    }
```

### KIS scope schema notes

| 테이블/상태 | 소유 범위 | 설명 |
| --- | --- | --- |
| `integration.realtime_subscriptions` | KIS scope | 서비스 전체 desired subscription pool과 실제 등록 상태를 표현 |
| `integration.kis_connection_sessions` | KIS scope | 현재/직전 WebSocket 세션 상태와 heartbeat 추적 |
| `bronze.tick_history` | KIS scope writes, platform owns retention | custom persistence consumer가 적재하는 대상. KIS scope의 Kafka handoff 결과가 저장됨 |
| access token / approval key state | runtime only | Secret/메모리 캐시 상태로 두고, 기본 설계에서는 DB 테이블로 저장하지 않음 |

### Design intent

- `integration.realtime_subscriptions`는 유저 watchlist가 아니라 **KIS가 실제로 맞춰야 하는 service-level subscription state**다.
- `desired_status`와 `actual_status`를 분리한 이유는 reconnect / resubscribe 과정에서 drift를 추적하기 위해서다.
- `connection_session_id`를 붙여두면 어떤 세션에서 수집된 tick인지, 재연결 전후 상태가 어땠는지 추적하기 쉬워진다.
- token / approval key는 영속 테이블보다 runtime manager가 더 적합하다. 이유는 보안성과 TTL 중심 lifecycle 때문이다.

### Implementation recommendations

- `KISTokenManager`, `KISApprovalKeyManager`는 둘 다 **async lock + double-check** 패턴으로 구현한다.
- REST auth bootstrap은 sync wrapper보다 **`httpx.AsyncClient` 기준**으로 설계하는 편이 낫다.
- token refresh buffer는 수십 초 단위, approval key buffer는 그보다 더 넉넉하게 두는 편이 안전하다.
- `KISConnectionManager`는 단순 reconnect만 하지 말고 **desired subscription registry**를 유지해야 한다.
- `KISRawMessageParser`는 envelope parse + record splitting까지만 맡고, 타입 변환은 `KISTickParser`가 담당하는 편이 안전하다.
- 초기 watch symbols source는 serving layer가 아니라 **config/env bootstrap**으로 두고, 이후 serving integration에서 source를 교체하는 편이 현실적이다.
- field aliasing은 raw schema와 internal schema를 분리한다. 가능하면 Pydantic alias 계층을 사용한다.

## 5. Core flows

### 5-1. Startup flow

1. 시스템 시작 시 `KISSettings`를 로드하고 초기 `watch_symbols`를 확보한다.
2. REST access token을 warm-up 한다.
3. WebSocket approval key를 발급받는다.
4. `MarketSessionRouter`가 현재 시간대 기준 활성 market adapter를 결정한다.
5. `KISConnectionManager`가 단일 세션을 연다.
6. `KISSubscriptionPool`의 현재 union을 기준으로 종목 등록을 수행한다.
7. 수신된 raw message는 parser -> tick parser -> sink -> Kafka producer 순서로 처리된다.

### 5-1.a. Auth bootstrap details

#### REST access token request

```json
{
  "grant_type": "client_credentials",
  "appkey": "<APP_KEY>",
  "appsecret": "<APP_SECRET>"
}
```

#### WebSocket approval key request

```json
{
  "grant_type": "client_credentials",
  "appkey": "<APP_KEY>",
  "secretkey": "<APP_SECRET>"
}
```

### Design implication

- access token과 approval key는 **서로 다른 endpoint / 서로 다른 수명 관리 객체**로 본다.
- 따라서 `KISTokenManager`와 `KISApprovalKeyManager`를 분리하는 현재 설계가 맞다.
- `KISConnectionManager`는 token manager를 직접 소유하기보다, 상위 bootstrap에서 token warm-up 후 approval/ws lifecycle에 집중하는 편이 더 단순하다.

### 5-2. Subscription reconciliation flow

1. 사용자 watchlist 변경이 발생하면 serving layer가 subscription pool 갱신 요청을 만든다.
2. `KISSubscriptionPool`은 전체 유저 관심종목 union을 재계산한다.
3. 41개 한도를 넘는 경우 우선순위 정책으로 active set을 결정한다.
4. `KISConnectionManager`는 현재 등록 상태와 목표 상태를 diff하여 subscribe/unsubscribe를 수행한다.

### 5-2.a. Market session switching baseline

1. `ScheduleBasedSessionSwitcher`가 현재 시각 기준 목표 market adapter를 계산한다.
2. 목표 adapter가 현재와 다르면 `MarketSessionRouter`가 active adapter를 교체한다.
3. `KISSubscriptionPool`은 기존 desired set의 `tr_id`를 새 adapter 기준으로 갱신한다.
4. `KISConnectionManager`는 unsubscribe/subscribe diff를 실행한다.
5. market signal(`NEW_MKOP_CLS_CODE`) 기반 보정은 이후 확장 포인트로 둔다.

### 5-3. Runtime tick flow

1. KIS가 raw realtime payload를 전송한다.
2. `KISRawMessageParser`는 `TR_ID`, count, raw records를 분리한다.
3. `KISTickParser`는 field index를 기반으로 `ParsedTick`으로 정규화한다.
4. `StockTickProducer`는 `stock-ticks` Avro event를 발행한다.
5. downstream(Flink / custom persistence consumer)은 여기서부터만 KIS 세부 포맷을 몰라도 된다.

### 5-3.a. Subscribe / unsubscribe wire format

#### Subscribe

```json
{
  "header": {
    "approval_key": "<APPROVAL_KEY>",
    "custtype": "P",
    "tr_type": "1",
    "content-type": "utf-8"
  },
  "body": {
    "input": {
      "tr_id": "H0STCNT0",
      "tr_key": "005930"
    }
  }
}
```

#### Unsubscribe

```json
{
  "header": {
    "approval_key": "<APPROVAL_KEY>",
    "custtype": "P",
    "tr_type": "2",
    "content-type": "utf-8"
  },
  "body": {
    "input": {
      "tr_id": "H0STCNT0",
      "tr_key": "005930"
    }
  }
}
```

### Subscription semantics

- `tr_type=1`은 subscribe, `tr_type=2`는 unsubscribe다.
- `tr_key`는 종목 코드다.
- 등록 상태는 request/response 한 번으로 끝나는 stateless 조회가 아니라, **세션에 붙는 지속 구독 상태**로 다룬다.
- 따라서 `KISSubscriptionPool`은 단순 리스트가 아니라 **desired state manager**여야 한다.
- reconnect 이후 자동 복구를 위해 현재 desired subscription set과 실제 등록 성공 이력을 따로 추적해야 한다.

### 5-4. Recovery flow

1. WebSocket 연결이 끊기면 `KISConnectionManager`가 재연결 backoff를 시작한다.
2. 새 approval key가 필요하면 갱신 후 재접속한다.
3. 연결 복구 후 마지막 desired subscription set을 기준으로 재등록한다.
4. 복구 이후의 데이터 정합성은 downstream replay 정책으로 보완한다.

### Recovery implementation note

- KIS 쪽은 과격한 재시도보다 **짧은 선형 backoff**가 더 현실적이다.
- reconnect 성공만으로 끝내지 말고, **resubscribe completed**를 별도 성공 조건으로 둔다.
- 401/인가 실패 시 token/approval key 강제 갱신 경로를 별도로 둔다.

## 6. External interface draft

### 6-0. Important separation

KIS scope에서 가장 먼저 분리해야 하는 것은 아래 세 가지다.

1. **REST auth contract** — token 발급과 현재가 snapshot 조회
2. **WebSocket wire contract** — approval key, subscribe/unsubscribe, TR ID
3. **Normalized domain contract** — Kafka `stock-ticks`에 실리는 내부 표준 tick event

이 세 층을 섞으면, KIS 문서 필드와 우리 내부 모델이 뒤엉켜 이후 scope(Flink, serving)까지 KIS 포맷이 침투한다.

### 6-1. REST / WebSocket contract groups

KIS 쪽 인터페이스는 코드에서 바로 문서 필드명을 복붙하기보다 아래처럼 그룹으로 나누는 편이 낫다.

| 그룹 | 예시 필드 | 용도 |
| --- | --- | --- |
| 인증 헤더 | `authorization`, `appkey`, `appsecret` | REST 인증/인가 |
| 라우팅 헤더 | `tr_id`, `tr_cont`, `custtype` | API 종류/연속조회/고객타입 지정 |
| 식별/추적 헤더 | `personalseckey`, `seq_no`, `gt_uid` | 계정/요청 추적 |
| 조회 파라미터 | `FID_COND_MRKT_DIV_CODE`, `FID_INPUT_ISCD` | 종목/시장 지정 |
| 시세 응답 필드 | `stck_prpr`, `prdy_vrss`, `prdy_ctrt`, `acml_vol` | 현재가/등락/거래량 |
| 실시간 체결 payload | `H0STCNT0` 기반 인덱스 필드 | tick 이벤트 원천 |

### 6-1.a. Relevant TR IDs

| TR ID | 설명 | 용도 |
| --- | --- | --- |
| `H0STCNT0` | 국내주식 실시간체결가 | v1 핵심 tick source |
| `H0NXCNT0` | NXT 실시간체결가 | extended-hours tick source |
| `H0STASP0` | 국내주식 실시간호가 | 향후 order book 확장 후보 |
| `H0NXASP0` | NXT 실시간호가 | extended-hours order book source |
| `H0STANC0` | 국내주식 실시간예상체결 | 장전/장후 확장 후보 |
| `H0STCNI0` | 국내주식 실시간체결통보 | 계좌/체결통보 계열, v1 scope 밖 |
| `FHKST01010100` | 주식현재가 시세 REST | bootstrap snapshot / fallback 조회 후보 |

### 6-1.b. Current-price REST query draft

```json
{
  "FID_COND_MRKT_DIV_CODE": "J",
  "FID_INPUT_ISCD": "005930"
}
```

이 endpoint는 종목 bootstrap snapshot이나 reconnect 이후 보정 조회에 사용할 수 있지만, **실시간 ingress 대체 수단으로 쓰면 안 된다.**

### 6-1.c. WebSocket semantic request / response model

#### RequestHeader (subscribe / unsubscribe)

| Wire field | 의미 | 내부 권장 이름 |
| --- | --- | --- |
| `approval_key` | 웹소켓 접속키 | `approval_key` |
| `custtype` | 고객타입 | `custtype` |
| `tr_type` | 거래타입 (`1` subscribe / `2` unsubscribe) | `tr_type` |
| `content-type` | 컨텐츠타입 | `content_type` |

#### RequestBody

| Wire field | 의미 |
| --- | --- |
| `tr_id` | 거래 ID |
| `tr_key` | 종목 구분값 |

#### Response shape

- request는 JSON header/body 구조로 보내지만,
- 실제 체결 stream은 `H0STCNT0` 기준 **delimited raw record**로 들어오므로, `ResponseHeader`를 안정적인 JSON 객체처럼 가정하지 않는 편이 맞다.
- 즉, response 쪽은 `header/body JSON`보다 **semantic field map**으로 문서화하는 편이 구현에 더 유리하다.

### 6-2. Important modeling rule

문서 원문 필드명을 그대로 Python dataclass attribute로 쓰면 안 된다. 예를 들어 `content-type`은 Python identifier가 아니므로, 구현에서는 다음 둘 중 하나로 처리해야 한다.

- 내부 모델은 `content_type`처럼 **snake_case canonical name**을 사용하고 wire name은 alias로 매핑
- 또는 HTTP header / query param은 모델 객체보다 **dict builder**로 다루기

즉, KIS 문서명은 **wire contract**, 우리 코드명은 **internal contract**로 분리하는 것이 맞다.

### Recommended model layering

- `RawKISResponse`: `0|TR_ID|count|payload` envelope를 표현
- `RawKISTickRecord` / `split_records`: payload를 record 단위 `list[str]`로 확장
- `CurrentPriceSnapshot`: REST 현재가 응답용 내부 표준 모델
- `ParsedTick`: WebSocket tick용 내부 표준 모델
- `StockTickEvent`: Kafka handoff용 Avro event 모델

### 6-3. Note on the updated WebSocket snippet

이번에 붙인 스니펫은 방향이 맞다. 이건 **REST 현재가 모델이 아니라 WebSocket subscribe contract + `H0STCNT0` semantic field set**으로 취급해야 한다.

- `RequestHeader` / `RequestBody`는 WebSocket subscribe-unsubscribe contract 계열이다.
- `ResponseBody`는 JSON 응답이라기보다, `H0STCNT0` raw payload를 parser가 해석한 뒤의 **semantic body layout**에 가깝다.
- 따라서 이 스니펫은 `kis_ws_client`와 `KISTickParser` 문서 기준으로 쓰고, `kis_rest_client`의 quote model과는 분리해야 한다.

### 6-4. Modeling caveat

- `content-type`은 Python identifier가 아니므로 코드에서는 `content_type` alias를 써야 한다.
- KIS wire payload는 본질적으로 문자열 기반이므로, raw parser 단계에서 바로 `float`로 고정하지 않는 편이 안전하다.
- 가격/비율 필드는 내부 모델에서 `Decimal`, 수량 계열은 `int`, 시각 필드는 문자열 -> time/datetime 변환 계층을 두는 편이 낫다.
- 즉, 붙인 dataclass는 **문서용 semantic schema**로는 좋지만, runtime raw parser 타입으로 그대로 쓰는 것은 피하는 편이 맞다.
- `KIS_WATCH_SYMBOLS` 같은 list config는 `pydantic-settings` 기본 동작상 JSON 문자열(예: `["005930","000660"]`)로 두는 편이 안전하다.
- comma-separated 문자열을 허용하려면 별도 validator를 둬야 하며, 그 전에는 기본 동작처럼 문서화하지 않는 편이 맞다.

즉, 구현 파일도 최소 아래 2개로 분리하는 편이 맞다.

- `kis_rest_client` — token / current price / reference lookup
- `kis_ws_client` — approval key / subscribe / raw tick stream

## 7. Normalized domain model

### Runtime parsing boundary

- `KISRawMessageParser`는 envelope parse와 record splitting까지만 수행하고, record는 **문자열 리스트**로 유지한다.
- `KISTickParser`가 문자열 기반 raw record를 `ParsedTick`으로 정규화하면서 `Decimal`/`int` 변환을 수행한다.
- 현재 계획상 `H0STCNT0`와 `H0NXCNT0`는 동일한 46-field 레이아웃을 공유하는 것으로 본다.
- 핵심 필드(`symbol`, `price`, `volume`) 변환 실패는 fail-fast로 다루고, 비핵심 필드는 Optional 처리 여지를 둔다.

### Raw `H0STCNT0` semantic body set

현재 KIS WebSocket 체결 모델은 아래 semantic field set을 기준으로 문서화한다.

| 그룹 | 필드 |
| --- | --- |
| 식별 / 시각 | `MKSC_SHRN_ISCD`, `STCK_CNTG_HOUR`, `BSOP_DATE` |
| 현재가 / 등락 | `STCK_PRPR`, `PRDY_VRSS_SIGN`, `PRDY_VRSS`, `PRDY_CTRT` |
| 장중 가격 | `WGHN_AVRG_STCK_PRC`, `STCK_OPRC`, `STCK_HGPR`, `STCK_LWPR` |
| 호가 / 잔량 | `ASKP1`, `BIDP1`, `ASKP_RSQN1`, `BIDP_RSQN1`, `TOTAL_ASKP_RSQN`, `TOTAL_BIDP_RSQN` |
| 거래량 / 거래대금 | `CNTG_VOL`, `ACML_VOL`, `ACML_TR_PBMN`, `VOL_TNRT`, `PRDY_SMNS_HOUR_ACML_VOL`, `PRDY_SMNS_HOUR_ACML_VOL_RATE` |
| 체결강도 / 체결건수 | `SELN_CNTG_CSNU`, `SHNU_CNTG_CSNU`, `NTBY_CNTG_CSNU`, `CTTR`, `SELN_CNTG_SMTN`, `SHNU_CNTG_SMTN`, `CCLD_DVSN`, `SHNU_RATE`, `PRDY_VOL_VRSS_ACML_VOL_RATE` |
| 시가/고가/저가 비교 시각 | `OPRC_HOUR`, `OPRC_VRSS_PRPR_SIGN`, `OPRC_VRSS_PRPR`, `HGPR_HOUR`, `HGPR_VRSS_PRPR_SIGN`, `HGPR_VRSS_PRPR`, `LWPR_HOUR`, `LWPR_VRSS_PRPR_SIGN`, `LWPR_VRSS_PRPR` |
| 세션 / 시장 제어 | `NEW_MKOP_CLS_CODE`, `TRHT_YN`, `HOUR_CLS_CODE`, `MRKT_TRTM_CLS_CODE`, `VI_STND_PRC` |

### Design implication

- raw KIS body는 생각보다 풍부하다.
- 하지만 v1의 `stock-ticks`는 이 전체를 그대로 외부 계약으로 노출하는 것이 아니라, **downstream에 필요한 안정 필드만 추린 normalized projection**으로 두는 편이 낫다.
- order book / quote depth 확장을 시작하면 `ASKP1`, `BIDP1`, 잔량 계열은 별도 extended tick schema나 별도 topic 후보가 된다.

### ParsedTick minimum contract

현재 문서 기준으로 `ParsedTick`은 최소 아래 필드를 포함한다.

| Field | Source |
| --- | --- |
| `symbol` | `MKSC_SHRN_ISCD` |
| `trade_time` | `STCK_CNTG_HOUR` |
| `price` | `STCK_PRPR` |
| `volume` | `CNTG_VOL` |
| `change_sign` | `PRDY_VRSS_SIGN` |
| `change_rate` | `PRDY_CTRT` |
| `vwap` | `WGHN_AVRG_STCK_PRC` |
| `open` | `STCK_OPRC` |
| `high` | `STCK_HGPR` |
| `low` | `STCK_LWPR` |
| `cumulative_volume` | `ACML_VOL` |
| `trade_strength` | `CTTR` |
| `market_session` | `NEW_MKOP_CLS_CODE` |
| `trading_halted` | `TRHT_YN` |
| `time_classification` | `HOUR_CLS_CODE` |
| `market_termination_code` | `MRKT_TRTM_CLS_CODE` |
| `vi_trigger_price` | `VI_STND_PRC` |

### Design principle

- downstream은 KIS field index를 직접 알지 않아야 한다.
- KIS 특화 포맷 해석은 `KISTickParser`에서 끝내고, 이후는 domain event만 본다.
- `market_session`, `trading_halted`, `vi_trigger_price`는 단순 metadata가 아니라 downstream rule의 핵심 입력이다.
- raw `H0STCNT0` body와 normalized `ParsedTick`은 같은 것이 아니다. 후자는 전자의 **안정된 projection**이다.

### REST snapshot model vs realtime tick model

- REST current-price 응답은 snapshot 성격이라 `stck_prpr`, `prdy_vrss`, `prdy_ctrt`, `stck_hgpr`, `stck_lwpr`, `acml_vol` 같은 필드군을 가진다.
- realtime tick은 `H0STCNT0` payload를 파싱한 시계열 이벤트다.
- 따라서 `CurrentPriceSnapshot`과 `ParsedTick`은 **같은 파일이 아니라 다른 모델**로 유지하는 편이 낫다.

## 8. Kafka handoff contract

| Topic | Producer | Meaning |
| --- | --- | --- |
| `stock-ticks` | `StockTickProducer` | KIS raw payload가 아니라 정규화된 domain tick event |

### KIS -> Kafka handoff rule

- KIS 쪽은 `stock-ticks`까지만 책임진다.
- Flink가 필요한 윈도우/패턴/알림은 이 이후 scope다.
- KIS가 이미 제공하는 VWAP/OHLC는 **입력 필드**로 사용하므로, ingress scope에서 재계산하지 않는다.
- planning phase에서는 `TickSink`를 handoff port로 두고, `LoggingTickSink` 같은 임시 구현으로 대체 가능하게 두는 편이 좋다.
- `StockTickProducer`는 이 handoff port를 구현하는 concrete Kafka sink로 보면 된다.

## 9. Open design questions

- 41종목을 넘는 watchlist union이 생기면 어떤 우선순위 정책으로 active subscription set을 정할 것인가?
- 공식 샘플의 40개 기준과 최신 운영 메모의 41건 기준 중, 실제 테스트에서 어떤 cap을 운영 상수로 채택할 것인가?
- subscribe/unsubscribe 반영 주기를 event-driven으로 둘지, 짧은 reconcile loop로 둘지?
- KRX↔NXT 전환 시 unsubscribe -> subscribe와 subscribe -> unsubscribe 중 어떤 순서를 기본 전략으로 둘 것인가?
- market signal(`NEW_MKOP_CLS_CODE`) 기반 보정을 언제 추가할 것인가?
- 종목별 bootstrap snapshot이 필요하면 REST current price 조회를 언제 호출할 것인가?
- reconnect 직후 누락된 틱을 어디까지 보정할 것인가?

## 10. Immediate next split after KIS

KIS 문서 다음으로는 아래 순서로 분리하는 것이 자연스럽다.

1. `13-stream-detection-design.md` — Flink window / alert / pattern scope
2. `14-alert-serving-design.md` — alert-service / notification / WebSocket scope
3. `15-batch-enrichment-design.md` — Airflow / outbox / Debezium scope

## Related Notes

- [[11-design-freeze-discussion-pack]]
- [[event-driven-stock-pipeline]]
- [[04-sequence-alert-detection]]
- [[09-c4-component-ingestion-stream]]
