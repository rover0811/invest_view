# 디버깅 핸드오프 — 실시간 가격 stale 문제

> **이 문서의 목적**: 답을 떠먹여 주는 문서가 아니다. 진단으로 확정된 *사실*과, 네가 직접 코드를 열어 *이해하고* 고쳐야 할 위치, 그리고 그 과정에서 스스로 답해야 할 질문을 모아둔 지도다.
> **원칙**: 이해하지 못한 채 고치면 면접에서 똑같이 무너진다. 각 단계의 "스스로 답하기"에 본인 언어로 답을 쓸 수 있을 때만 다음으로 넘어갈 것.
> **관련 이슈**: #29 (stale 가격 — 메인), #30 (시간 컬럼 text 타입 — 근본), #22 (exactly-once sink — 연관)

---

## 0. 증상 (재현된 사실)

- 화면(serving)의 005930 가격 = **336,500원** (`last_trade_time=125047`, 12:50:47 값에 멈춤)
- 같은 시각 KIS 실시간 수신가 = **343,000원** (kis-ingestion 로그 06:58:48 UTC)
- 005930만 stale. 나머지 종목(000660 등)은 14:01까지 정상 갱신.

→ "완전히 틀린 값"이 아니라 **특정 종목이 과거 시점에 멈춘 stale**. 수집은 정상인데 저장/서빙 경로에서 최신 틱이 반영되지 않음.

---

## 1. 운영 DB에서 확정한 증거 (2026-06-16, homelab)

접속: `ssh hyunsoo@100.89.9.31` → `kubectl -n postgres exec postgresql-0 -- env PGPASSWORD=<postgres-postgres-password> psql -U postgres -d invest_view`

### 증거 A — 수집은 정상 (343,000 수신 중)
```
kis-ingestion 로그: 06:58:48 symbol=005930 price=343000
                    07:02:02 WebSocket receive failed, reconnecting: no close frame received or sent
```
kis-ingestion 재시작 횟수: **15회** (`kubectl -n invest get pods`)

### 증거 B — bronze에 동일 틱이 무한 중복
```sql
SELECT count(*) total, count(DISTINCT tick_dedupe_key) distinct_keys
FROM bronze.tick_history WHERE symbol='005930';
-- total = 810407,  distinct_keys = 810407   (전체 bronze 약 500만 행)

SELECT symbol, trade_time, received_at, price, kafka_offset
FROM bronze.tick_history WHERE symbol='005930' ORDER BY kafka_offset DESC LIMIT 5;
-- 1890349 | 125111 | 2026-06-12T03:51:12 | 336500
-- 1890348 | 125111 | 2026-06-12T03:51:12 | 336500   ← 같은 값이 offset마다 반복
```

### 증거 C — 시간 컬럼이 text
```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_schema='bronze' AND table_name='tick_history'
  AND column_name IN ('received_at','trade_time');
-- received_at | text
-- trade_time  | text   (값 예: '125111' = HHMMSS 문자열)
```

---

## 2. 현재 세운 가설 (인과 사슬) — *검증 대상이지 결론 아님*

```
WebSocket 잦은 끊김 (15회 재시작)
  → 재연결 시 Kafka offset이 안정적으로 커밋되지 않거나 재발행
  → 동일 과거 틱 무한 재처리 (005930 ~81만 중복)
  → consumer lag 폭증 → 최신 틱(343,000)이 큐 뒤에 막혀 처리 안 됨
  → snapshot upsert가 trade_time 비교 없는 LWW라 과거 틱(336,500)으로 계속 덮어씀
  → 시간이 text라 "진짜 최신"을 시간으로 거를 수도 없음
  → 화면 = 336,500 (stale)
```

**이 사슬의 각 고리를 코드로 직접 확인하기 전까지는 추측이다.** 아래 4개 조사 트랙으로 하나씩 검증할 것.

---

## 3. 조사 트랙 (코드를 열고 *이해*한 뒤 답하기)

### 트랙 1 — snapshot은 정말 무조건 덮어쓰는가? (가장 빠른 확인)
**파일**: `services/tick_persistence/src/tick_persistence/repository/snapshot.py`
**함수**: `SnapshotRepository.upsert_snapshot` → `on_conflict_do_update`

스스로 답하기:
- [ ] `on_conflict_do_update`에 `where` 조건이 있는가, 없는가? 없다면 어떤 틱이든 마지막에 도착한 게 이긴다(LWW). 이게 stale write를 허용하는가?
- [ ] `last_trade_time`은 무슨 타입으로 들어가는가? (→ #30) 이 값으로 "더 최신 틱만 반영" 조건을 안전하게 걸 수 있는가? `'125111'` vs `'140214'` 문자열 비교는 항상 옳은가? 자정 경계(`235959`→`000001`)에서는?
- [ ] DDIA 5장 "동시 쓰기 충돌" / "Last-Write-Wins의 위험"을 읽고, 이 upsert가 그 사례에 해당하는지 본인 언어로 설명해보기.

### 트랙 2 — 왜 같은 틱이 81만 번 중복되는가?
**파일**: `services/kis_ingestion/src/kis_ingestion/connection_manager.py`, `producer.py`
**그리고**: tick_persistence의 Kafka consumer 설정 (consumer group, auto-commit, offset reset)

스스로 답하기:
- [ ] 재연결 시 `session_id`/`sequence`가 어떻게 바뀌는가? (로그상 재연결마다 새 session_id, sequence는?) 이게 dedupe_key에 영향을 주는가?
- [ ] `tick_dedupe_key`는 무엇으로 구성되는가? (symbol + trade_time + ? ) distinct_keys가 81만이라는 건 dedupe_key가 매번 *다르게* 생성된다는 뜻이다 — 왜 다르게 생성될까? (힌트: 같은 체결 틱인데 key가 달라지는 요소가 들어가 있지 않은가?)
- [ ] consumer는 offset을 언제 커밋하는가? 재연결/재시작 시 `auto.offset.reset`이 earliest면 무슨 일이 일어나는가?
- [ ] DDIA 11장 "정확히 한 번 처리" / "멱등성"을 읽고, at-least-once 전달에서 멱등성이 깨지면 왜 무한 재처리가 되는지 설명해보기.

### 트랙 3 — WebSocket은 왜 자꾸 끊기는가?
**파일**: `connection_manager.py` (reconnect 로직), `docs/design/12-kis-realtime-ingress-design.md`
**로그**: `07:02:02 WebSocket receive failed: no close frame received or sent`

스스로 답하기:
- [ ] reconnect 백오프 전략은? (선형? 지수? 횟수 제한?) `no close frame`은 보통 무슨 상황인가 (서버측 비정상 종료/타임아웃)?
- [ ] KIS WebSocket의 ping/pong(keepalive) 설정값은 얼마인가? half-open 연결을 감지하기에 적절한가?
- [ ] 재연결이 잦을 때 모든 종목 재구독이 보장되는가? 005930만 stale인 것과 재구독 누락이 관계있는가?
- [ ] DDIA 8장 "신뢰할 수 없는 네트워크" / "타임아웃"을 읽고, 재연결 폭풍(reconnect storm)을 막는 정석(지수 백오프 + jitter)이 왜 필요한지 설명해보기.

### 트랙 4 — 시간을 text로 저장한 게 왜 모든 걸 악화시키는가? (근본, #30)
**위치**: ingestion에서 `received_at`/`trade_time`을 만드는 지점 → bronze 모델 정의

스스로 답하기:
- [ ] 시간이 timestamp가 아니라 text면, 트랙1의 "더 최신 틱만 반영" 조건부 upsert를 안전하게 걸 수 있는가?
- [ ] Flink watermark는 지금 ingress-time(received_at) 기준이다. event-time(체결 시각) 기준으로 바꾸려면 trade_time이 무슨 타입이어야 하는가?
- [ ] DDIA 8장 "event-time vs processing-time"을 읽고, 둘을 혼동하면 어떤 정합성 문제가 생기는지 본인 프로젝트 사례로 설명해보기.

---

## 4. 고치기 전 반드시 통과할 관문

각 트랙의 "스스로 답하기"를 **말로 설명할 수 있을 때만** 코드를 고친다. 고치는 순서 제안:

1. **트랙 1 + 트랙 4 묶어서**: 시간 컬럼을 timestamp로 → snapshot을 조건부 upsert(`WHERE excluded.<event_ts> > 기존`)로. (#30 → #29 동시 완화)
2. **트랙 2**: dedupe_key가 매 재처리마다 달라지는 원인 제거 + consumer offset 커밋 점검. (중복 폭발 차단, #22와 연결)
3. **트랙 3**: 재연결 지수 백오프 + jitter + 재구독 보장. (근본 트리거 완화)

각 수정마다:
- [ ] 수정 전후로 위 검증 쿼리(증거 A/B/C) 다시 실행해 숫자가 바뀌는지 확인
- [ ] 기존 81만 중복행 정리 계획 (DELETE 또는 토픽/테이블 재적재)
- [ ] 단위/통합 테스트 추가 (`services/tick_persistence/tests/`에 이미 test_repository.py 등 존재 — out-of-order 틱 시나리오 테스트 추가)

---

## 5. 이 디버깅이 면접에서 갖는 의미 (DDIA × JD)

이 버그 하나가 DDIA 핵심 4개 장을 관통한다. 고친 뒤 "왜?"에 답할 수 있으면 그대로 워스토리가 된다.

| 트랙 | DDIA 장 | 네이버 예상 질문 | JD 연결 |
|---|---|---|---|
| 1 (LWW) | 5장 동시 쓰기 충돌 | "최신 데이터를 어떻게 보장했나?" | 데이터 정합성 |
| 2 (중복) | 11장 멱등성/exactly-once | "at-least-once에서 중복을 어떻게 막나?" | 확장성+장애대응(Required) |
| 3 (재연결) | 8장 신뢰못할 네트워크 | "장애 시 재처리를 어떻게 안정화하나?" | 장애대응(Required) |
| 4 (시간 text) | 8장 event-time | "event-time과 processing-time을 어떻게 다뤘나?" | 데이터 품질 |

**핵심 포지션**: "exactly-once 했습니다"가 아니라 **"어디까지 보장되고 어디부터 한계인지, 그 한계를 어떻게 진단하고 좁혔는지"**를 말할 수 있는 사람이 된다.

---

## 부록 — 빠른 검증 쿼리 모음
```sql
-- 현재 stale 상태
SELECT symbol, last_price, last_trade_time, updated_at, now()-updated_at AS staleness
FROM serving.symbol_snapshot WHERE symbol='005930';

-- 중복 폭발 여부
SELECT count(*) total, count(DISTINCT tick_dedupe_key) keys
FROM bronze.tick_history WHERE symbol='005930';

-- 최근 적재 흐름 (살아있나)
SELECT symbol, max(persisted_at) FROM bronze.tick_history
GROUP BY symbol ORDER BY 2 DESC LIMIT 8;
```
