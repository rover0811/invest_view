---
aliases:
- 18 Agent Fundamental Research Proposal
tags:
- design
- agent
- strands
- fundamental
- report
- text-to-sql
- search
- chat
- session
created: 2026-06-02
updated: 2026-06-02
version: 3
---

# 18 Agent Fundamental Research Proposal (v3)

이 문서는 [[13-agent-layer-proposal]]에서 **명시적 범위 제외**(Research MCP 비활성)였던 **펀더멘털(재무제표) + 증권사 리포트 분석**을 에이전트 레이어에 정식으로 편입하는 설계다.

doc 13은 실시간 시세 기반 read-only 분석만 다뤘다. 이 문서는 그 위에 **재무제표 / 증권사 리포트 도메인**을 얹을 때의 토폴로지, 데이터 접근 방식, 검색 전략, tool 설계, 평가, 구현 계획을 정의한다.

에이전트 엔지니어가 이 문서 하나로 **종목 질문이 들어왔을 때 무엇을 어떻게 가져오고, 재무제표는 어떻게 selective하게 쿼리하며, 리포트는 어떻게 검색·평가하고, 토폴로지를 왜 이렇게 잡는지** 파악할 수 있어야 한다.

## v2 변경 요약 (v1 대비)

v1은 검색 엔지니어 리뷰에서 다음 결함이 드러나 전면 개정했다.

| # | v1의 문제 | v2의 수정 |
| --- | --- | --- |
| 1 | 재무제표를 "검색" 도메인에 포함 (카테고리 에러) | 재무제표를 **결정론적 selective query**로 재분류. 검색 아님 |
| 2 | `pg_bigm LIKE`를 "검색"으로 취급 (랭킹 없음) | 리포트만 검색 대상. lexical baseline → 측정 → reranker 에스컬레이션 |
| 3 | 평가를 Recall@10 단독 | **Recall@5 + MRR + Context Precision** (ticker-scoped + LLM 컨텍스트 특성 반영) |
| 4 | Text-to-SQL을 막연히 언급 | **고정 쿼리 템플릿(intent→파라미터)** 명시. 자유 SQL 생성 금지 |

## v2.1 실측 정정 (2026-06-02 DB 직접 쿼리)

v2 초안의 두 블로커가 **실제 DB 쿼리로 확인한 결과 이미 해소**되어 정정한다.

| v2 가정 | 실측 결과 | 영향 |
| --- | --- | --- |
| reference ticker = `A005930` → 표준화 선행 필요 | **이미 `005930`** (A prefix 없음). `to_storage_ticker()`가 적재 시 제거. `serving`과 동일 포맷 | **이슈 0-1(ticker 표준화) 불필요.** JOIN 즉시 동작 (40종목 unmatched=0) |
| 리포트 코퍼스 2~3건 → 백필 선행 필수 | **이미 2,319건 적재됨** | **이슈 0-3(백필) 사실상 완료.** 검색 baseline 측정 즉시 가능 |
| financial 3개년 | **10개년** 보유 (BAL/INC/CAS 각 10 period) | period_range 파라미터 의미 확대 |
| consensus에 `summary` 컬럼 | **없음** — `full_text`만 존재 | 요약은 LLM 생성 or full_text 노출로 처리 |

실측 커버리지: `bronze_market_ticker` 4007행, `bronze_financial_statement` 8295행/2765종목, `bronze_consensus_report` 2319건, `serving.symbol_snapshot` 40종목 (전부 reference에 매칭).

## v3 변경 요약 (v2.1 대비)

v2까지는 **데이터 접근 토폴로지**(결정론 selective query + 확률 검색)만 다뤘다. 백엔드 에이전트를 실제 챗 제품으로 붙이려면 **대화 제품층**(chat history, new chat, 멀티턴, stop, regenerate 등 일반 AI 챗 기능)과 **펀더멘털/리포트 도메인의 구체 유스케이스**가 필요한데 v2는 둘 다 비어 있었다. v3은 이 갭을 채운다.

| # | v2까지의 공백 | v3의 추가 |
| --- | --- | --- |
| 1 | 유스케이스 0개 (doc 13의 시세 전용 UC 암묵 상속) | **§15 UC 매트릭스** — 4데이터축(주가·이벤트·재무·리포트) × 조합차원(단일/2축/3축/비교). v1은 전부 포함, **평가는 v1 보류** |
| 2 | chat history / 세션 / 멀티턴 미정의 | **§16 채팅 제품층** — `agent.chat_sessions`/`chat_messages` 스키마, parent_id 브랜칭, status enum, 세션 = ticker 고정 |
| 3 | "SSE 엔드포인트" 한 줄 | **§17 API 계약** — new chat/history/stream/stop/regenerate/title/delete 엔드포인트 + SSE 취소 + SessionRepository 어댑터 |
| 4 | 세션 저장 "Strands→PG" 막연 (doc 13) | 우리 테이블 = source of truth, Strands `SessionRepository`는 그 위 어댑터(하이브리드). 실측 결과 Strands PG 백엔드 부재 → BUILD 확정 |

핵심 결정 근거(턴키 프레임워크/LangGraph 영속화/OSS 챗앱 스키마/커뮤니티 실무자 5각도 조사)는 **부록 B**에 정리한다. 한 줄 요약: **"프레임워크가 데이터를 소유하게 하지 마라 — Strands는 stateless brain, 대화 상태는 우리 Postgres가 소유."**

## 0. 핵심 설계 원칙 — 결정론적 vs 확률적 분리

이 설계의 가장 중요한 결정은 **데이터를 두 부류로 나누는 것**이다.

| 분류 | 데이터 | 접근 방식 | 정확성 |
| --- | --- | --- | --- |
| **결정론적 (Deterministic)** | 실시간 시세, 재무제표 수치 | **고정 SQL 템플릿 selective query** | 정확값 보장 |
| **확률적 (Probabilistic)** | 증권사 리포트 본문 (자유 텍스트) | **lexical 검색 → (조건부) reranker** | recall/precision 측정 대상 |

- 숫자/수치(가격, EBITDA, 영업이익)는 **검색하지 않는다.** ticker로 정확히 쿼리해서 가져온다.
- 리포트 본문(자연어 분석/논리)만 **검색** 대상이다.
- LLM은 **계산기가 아니다.** 결정론적 계산(성장률, 비율)은 SQL/도구에서, LLM은 해석·트렌드·종합만 담당한다.

이 분리가 v1의 가장 큰 오류(재무제표를 검색으로 다룬 것)를 바로잡는다.

## 0-1. 이 문서가 doc 13과 다른 점

| 항목 | doc 13 (기존) | doc 17 v2 (이 문서) |
| --- | --- | --- |
| 데이터 도메인 | 실시간 시세 | + 재무제표, 증권사 리포트 |
| Research MCP | 인터페이스만, 비활성 | **활성** (데이터 소스 확보로 트리거 충족) |
| 재무제표 | 없음 | **결정론적 selective query** (검색 아님) |
| 리포트 | 없음 | **lexical 검색** (백필 후), recall@5/MRR 측정 |
| 데이터 접근 | `serving.*` view only | + `reference.*` selective query |
| 주 상호작용 | 자유 질문 | **종목 차트 페이지 컨텍스트** (ticker 항상 주어짐) |

doc 13 에스컬레이션 트리거(line 542) "외부 리서치 소스 승인 시 Research MCP 활성화" 조건이 데이터 적재로 충족되어 본 문서로 범위를 연다.

## 1. 전제 조건

doc 13 결정을 계승하며 다음을 추가/명시한다.

- Agent 데이터 읽기 경로는 **PostgreSQL only** — Kafka 직접 접근 금지 (계승)
- Agent는 **query/analysis 전용** — 매매 주문 실행 없음 (계승)
- **검색 인프라는 PostgreSQL 내부에서만 해결** — Elasticsearch/OpenSearch 외부 검색엔진 v1 미도입
- **ticker 포맷은 `005930` 단일 표준** — `A` prefix 없는 6자리 (2-2 참조)
- **Text-to-SQL은 고정 템플릿 방식** — LLM이 자유 SQL을 생성하지 않는다 (4절)
- **임베딩/pgvector는 v1 범위 밖** — lexical 검색으로 시작, 측정 후 필요 시 도입
- **read-only DB role + allow-list 테이블** — DB 레벨에서 강제, 프롬프트 의존 금지

참고: [[13-agent-layer-proposal]], [[11-design-freeze-discussion-pack]], [[15-batch-enrichment-design]]

## 2. 데이터 접근 모델

### 2-1. Agent가 읽는 테이블/뷰

| 스키마.객체 | 분류 | 접근 | 상태 |
| --- | --- | --- | --- |
| `serving.symbol_snapshot` | 결정론적 | SQL 템플릿 | 기존 |
| `serving.symbol_intraday_5m` | 결정론적 | SQL 템플릿 | 기존 |
| `serving.symbol_signal_timeline` | 결정론적 | SQL 템플릿 | 기존 |
| `reference.bronze_financial_statement` | 결정론적 | **selective query** | 적재완료(연간) |
| `reference.bronze_consensus_report` | 확률적 | **lexical 검색** | **백필 필요** |
| `reference.bronze_market_ticker` | 결정론적 | SQL 템플릿 | 적재완료 |

### 2-2. ticker 표준: `005930` 단일 포맷

**실측 결과 이미 통일됨 (`005930`).** `serving.symbol_snapshot.symbol`과 `reference.bronze_market_ticker.ticker` 모두 A prefix 없는 6자리다. stock-crawler의 `to_storage_ticker()`가 적재 시 `A` prefix를 제거하므로 포맷이 일치한다. 40종목 LEFT JOIN에서 unmatched=0 확인.

| # | 질문 | 결정 |
| --- | --- | --- |
| T1 | 표준 포맷 | `005930` (A prefix 없음) — **이미 적용됨** |
| T2 | 추가 작업 | 없음 (적재 시점에 이미 정규화됨) |
| T3 | JOIN | `reference.* ON ticker = serving.*.symbol` 즉시 동작 |

> 주의: batch_enrichment 코드/ORM은 별도 레포(`stock-crawler`)에 존재하나, **데이터는 동일 DB `invest_view`의 `reference` 스키마에 적재 완료**. invest_view에는 ORM이 없어 agent/API tool은 raw SQL로 `reference.*`를 조회한다.

### 2-3. cross-schema 조인

모든 스키마가 단일 DB `invest_view` 안에 있어 `schema.table` 직접 조인 가능. 선례: `serving.symbol_signal_timeline`이 이미 `alert_service.alert_events` ∪ `gold.pattern_events` UNION 구현 중.

## 3. 주 상호작용: 종목 컨텍스트 우선

### 3-1. ticker는 항상 주어진다

사용자는 **종목 차트 페이지에서 질문**한다. 대상 ticker는 페이지 컨텍스트로 확정되어 있다.

- **종목 기준 fetch가 1급(default)** — ticker로 관련 데이터를 가져오는 것이 기본
- **검색은 부차** — 리포트 본문에서 특정 내용을 찾을 때만
- **비교는 다중 ticker 쿼리** — 1종목이 대다수, 2+ 비교도 지원

### 3-2. ticker를 ambient context로 주입 (Strands)

LLM이 프롬프트에서 ticker를 추출하지 않는다. 차트 페이지가 아는 ticker를 `invocation_state`로 주입하고 tool이 `ToolContext`로 읽는다.

```python
from strands import Agent, tool, ToolContext

@tool(context=True)
def get_symbol_snapshot(tool_context: ToolContext) -> dict:
    """현재 종목의 최신 시세 스냅샷. 종목 질문 시 가장 먼저 호출."""
    ticker = tool_context.invocation_state.get("current_ticker")
    ...

result = agent("지금 왜 올라?", invocation_state={"current_ticker": "005930"})
```

다중 종목 비교는 LLM이 명시적으로 `tickers=['005930','000660']`을 tool 파라미터로 전달한다(ambient는 기본 1종목, 비교 시 파라미터로 확장).

## 4. Text-to-SQL: 고정 템플릿 방식

### 4-1. 왜 자유 SQL 생성이 아닌가

자유형 NL2SQL은 프로덕션에서 무너진다.

- 실행 정확도: Spider 1.0 91% → BIRD(현실 데이터) 82% → 엔터프라이즈 스키마 **6~21%로 붕괴**
- 환각 모드: 없는 컬럼 생성, 3테이블+ 조인 시 테이블당 실패율 +20%, dialect 불일치
- 보안: 자유 SQL은 injection/blind query 위험

**결정: intent → 검증된 고정 SQL 템플릿 + 타입드 파라미터.** LLM은 SQL을 생성하지 않고, "어떤 템플릿에 어떤 파라미터를 넣을지"만 결정한다.

근거: 고정 템플릿(intent classification + slot filling) = Exact Match 92% vs 자유생성 60%. 프로덕션 쿼리 14,000개 중 350개 템플릿이 93.5% 커버.

### 4-2. "무엇을 뽑을지는 열고, 어떻게 뽑을지는 고정"

재무제표 분석의 핵심 패턴:

- **고정**: SQL 템플릿(JSONB 추출 로직, 조인, 파라미터 바인딩)
- **개방**: LLM이 파라미터(어떤 종목, 어떤 statement, 어떤 항목, 어떤 기간)와 해석(트렌드, 비교, 시나리오)을 결정

## 5. 재무제표 selective query (결정론적)

### 5-1. 데이터 구조 (검증됨)

`reference.bronze_financial_statement`:
- 한 종목 = **3행** (`selected_factor->>'code'` = BAL 대차/INC 손익/CAS 현금흐름)
- 각 행의 `financial_table` JSONB: `[{period, value:[{itemNameKor, value, unitType, item, parentItem, itemNameEng}]}]`
- 기간: 연간 `"2024/12"`, 분기 `"2023-03"` / `selected_period->>'code'` = Y/Q
- **EBITDA = INC 행에서 `itemNameKor='*EBITDA'`** / EBIT, 주당순이익도 INC
- `ticker` 단일 인덱스 존재 → 다종목 `ANY(:tickers)` 가능
- **한 행 JSONB가 수십~수백 KB** → 통째로 LLM에 넘기면 컨텍스트 폭발

### 5-2. selective query가 필요한 이유

전체 JSONB를 넘기지 않고 **필요한 행/값만 추출**한다. 3종목 × 3 statement = 9행 전체는 수 MB. `item_names` 필터로 필요 항목만 뽑으면 수십 바이트로 축소. selective projection으로 다중 종목 비교가 한 컨텍스트에 들어간다.

### 5-3. tool 시그니처

```python
@tool
def get_financials(
    tickers: list[str],                    # ['005930'] 또는 ['005930','000660']
    stmt_type: str,                        # 'BAL' | 'INC' | 'CAS'
    item_names: list[str] | None = None,   # ['*EBITDA','영업이익'], None이면 전체
    period_type: str = 'Y',                # 'Y' | 'Q'
    start_period: str | None = None,       # '2022/12'(Y) 또는 '2022-03'(Q)
    end_period: str | None = None,
) -> list[dict]:                           # [{ticker, period, item, value, unit}, ...]
    """재무제표에서 특정 종목·항목·기간 수치만 추출. 다종목 비교 지원."""
    ...
```

### 5-4. SQL 패턴 (이중 unnest)

```sql
SELECT
    fs.ticker,
    entry->>'period'            AS period,
    item->>'itemNameKor'        AS item,
    (item->>'value')::float     AS value,
    item->>'unitType'           AS unit
FROM reference.bronze_financial_statement fs,
     jsonb_array_elements(fs.financial_table) AS entry,
     jsonb_array_elements(entry->'value')     AS item
WHERE fs.ticker = ANY(:tickers)
  AND fs.selected_factor->>'code' = :stmt_type
  AND fs.selected_period->>'code' = :period_type
  AND (:item_names IS NULL OR item->>'itemNameKor' = ANY(:item_names))
  AND (:start_period IS NULL OR entry->>'period' >= :start_period)
  AND (:end_period   IS NULL OR entry->>'period' <= :end_period)
ORDER BY fs.ticker, entry->>'period' DESC;
```

### 5-5. 출력 형태 + 계산 위치

- **반환은 long format flat rows**: `[{ticker, period, item, value, unit}]`. nested JSON보다 LLM 비교 정확도 우수 (tabular 구조)
- **결정론적 계산은 SQL/도구에서**: EBITDA 성장률 `(현재-과거)/NULLIF(과거,0)`를 SQL로 계산해 반환. LLM은 산술 신뢰 불가(다단계 산술 정확도 급락 사례) → LLM은 트렌드 해석·비교·시나리오만
- **개방형 분석**: "무엇을 볼지(파라미터)"와 "어떻게 해석할지"는 LLM이 결정, "어떻게 뽑을지(SQL)"는 고정

## 6. 리포트 검색 (확률적)

### 6-1. 코퍼스 상태 — 이미 확보됨 (실측)

`reference.bronze_consensus_report`에 **2,319건 적재 완료** (2026-06-02 DB 실측). v2 초안의 "2~3건, 백필 선행 필수" 가정은 틀렸고, 백필이 사실상 완료된 상태다. **검색 baseline 측정을 즉시 시작할 수 있다.**

- 컬럼: `report_idx`, `report_date`, `stock_name`, `ticker`, `title`, `target_price`, `investment_opinion`, `author`, `provider`, `full_text` 등
- 주의: 프론트/agent가 기대한 `summary` 컬럼은 **없음** — `full_text`(PDF 본문)만 존재. 요약이 필요하면 LLM 생성 or full_text 노출
- 추가 백필이 필요하면(더 긴 기간) `backfill_6_months()`가 스크래퍼 레포에 이미 구현되어 있으나, 현재 코퍼스로 검색 검증은 충분

(재무제표도 동일 DB에 8295행/2765종목 적재 완료. 추가 작업 불필요.)

### 6-2. 검색 전략: `pg_bigm` baseline → 측정 → 개선

PostgreSQL only + 한국어 제약 하에서:
- PG 네이티브 `tsvector`는 한국어 교착어 실패
- 진짜 BM25 확장(ParadeDB 등)은 한국어 alpha/성능회귀
- **`pg_bigm`(bigram) + ticker 선필터**를 baseline으로 시작

단, `pg_bigm LIKE`는 **boolean 필터이지 랭킹이 아니다**(TF/IDF 없음). 따라서:
- v1 baseline: `pg_bigm` + ticker 선필터로 후보 확보
- **측정 후 개선**: recall이 부족하거나 랭킹이 나쁘면 cross-encoder reranker(예: `bge-reranker-v2-m3`) 또는 `textsearch_ko` 형태소 검색으로 에스컬레이션

### 6-3. 리포트 tool 시그니처

```python
@tool
def get_recent_reports(tickers: list[str], limit: int = 5) -> list[dict]:
    """종목 최근 리포트 N건 (검색 아님, 날짜순)."""

@tool
def search_reports(tickers: list[str], keyword: str, limit: int = 5) -> list[dict]:
    """종목 리포트 본문에서 키워드 검색 (ticker 선필터 + pg_bigm)."""

@tool
def get_consensus(tickers: list[str]) -> list[dict]:
    """목표주가/투자의견 집계 (AVG(target_price), GROUP BY provider). 결정론적."""
```

목표주가·투자의견은 **정규화 컬럼**이므로 검색이 아니라 결정론적 집계. `full_text`만 검색 대상.

## 7. 평가 (리포트 검색에 한정)

### 7-1. 지표: Recall@5 + MRR + Context Precision

ticker-scoped(작은 후보셋) + LLM 다운스트림 특성상:
- **Recall@5**(주 지표) — ticker로 거른 작은 셋에서 Recall@10은 거의 1.0이라 vanity. k=5가 LLM primacy zone 유지, "lost in the middle" 회피
- **MRR**(랭킹) — Recall@5는 정답이 1위든 5위든 동일 통과. LLM은 순서대로 읽으므로 최상위 배치가 중요. MRR로 보완
- **Context Precision**(노이즈 가드) — distractor가 LLM 환각 유발

목표: **Recall@5 ≥ 0.85, MRR ≥ 0.90, Context Precision ≥ 0.75** (FinAgentBench도 nDCG@5/MRR@5 사용).

### 7-2. 골든셋 구축

- Ragas evolutionary 생성으로 (질문, 정답 리포트) 쌍 ~200개
- **Answerability 필터**(NVIDIA): "이 리포트만으로 답 가능한가"로 걸러냄
- 50개 수동 검수 = 골든셋
- **합성 쿼리 어휘 누수 주의**: 원문 어휘 그대로 쓰면 pg_bigm 점수 부풀려짐. 실사용 trace 샘플링으로 보정

## 8. 토폴로지: agents-as-tools

doc 13 Phase 2 결정을 펀더멘털/리포트에 적용. a2a/graph/swarm/workflow 모두 부적합(같은 프로세스·같은 DB·한 팀, 고정 경로 아님, peer 핸드오프 불필요, 대화형 필요).

> agents-as-tools = a2a의 로컬 버전. 독립 배포·확장·다른 팀 소유 필요 시에만 a2a 전환.

- **Phase 1**: 단일 `MarketAnalystAgent`. 실시간 + 재무 + 리포트 tool 전부 등록. agent loop가 tool 순서 조율
- **Phase 2**: 실사용 데이터로 분리 효용 증명 후 specialist 분리 (Price/Fundamental/Research). A2A 형태 contract로 미래 분리 대비

## 9. Tool 전체 목록

| MCP | Tool | 분류 | 데이터 |
| --- | --- | --- | --- |
| Market Data | `resolve_symbol` | 결정론 | bronze_market_ticker |
| Market Data | `get_symbol_snapshot` | 결정론 | symbol_snapshot |
| Market Data | `get_intraday_bars` | 결정론 | symbol_intraday_5m |
| Market Data | `get_signal_timeline` | 결정론 | symbol_signal_timeline |
| Fundamental | `get_financials` | 결정론(selective) | bronze_financial_statement |
| Fundamental | `compare_financials` | 결정론(selective, 다종목) | 동일 |
| Research | `get_recent_reports` | 결정론(날짜순) | bronze_consensus_report |
| Research | `search_reports` | **확률(검색)** | bronze_consensus_report.full_text |
| Research | `get_consensus` | 결정론(집계) | bronze_consensus_report |

### Structured Output (evidence + freshness)

```python
class AnalysisResponse(BaseModel):
    summary: str
    evidence: list[str]              # view/테이블, report_id, period 등
    data_freshness: datetime         # 사용 데이터 중 최신 시점
    coverage_note: str | None        # 41슬롯 밖이면 명시
```

## 10. 가드레일

- 매매 지시 차단 (계승)
- 증거 기반 응답 강제 (계승)
- 41슬롯 커버리지 공개 (계승)
- write tool 없음 + **read-only role + allow-list 테이블** (DB 레벨 강제)
- **예측 면책**: "단기 주가 예측"은 재무/리포트 근거 **시나리오 분석**으로 한정, 단정적 가격 예측 금지
- **데이터 신선도 차등**: 실시간(초)/재무(분기)/리포트(발행일) 구분 표기

## 11. 구현 계획

### Phase 0 — Spike (1~2주)

> ticker 표준화·백필은 실측 결과 이미 완료(v2.1) → Phase 0에서 제거됨.

**증명할 것:**
1. `financial_table` JSONB에서 selective query로 EBITDA 정확 추출 (다종목 포함) — 실측 SQL 검증됨, tool화만 남음
2. `pg_bigm` + ticker 선필터로 한국어 리포트 검색 동작 (코퍼스 2,319건 위에서)
3. ticker를 `invocation_state` ambient로 주입, tool이 환각 없이 fetch
4. 가드레일이 매매 지시/단정 예측 차단

### Phase 1 — MVP (2~3주)

- 단일 MarketAnalystAgent, 결정론 + 확률 tool 전체
- Structured output (evidence, freshness, coverage)
- 리포트 검색 골든셋 50개 + Recall@5/MRR baseline 측정
- 가드레일 + 정책 테스트

### Phase 2 — Multi-agent (2~3주)

- Coordinator + Price/Fundamental/Research specialist
- agents-as-tools, A2A 형태 contract

### Phase 3 — 검색 품질 고도화 (조건부)

- Recall@5/MRR baseline 불충분 시에만: reranker(`bge-reranker-v2-m3`) 또는 `textsearch_ko` 도입 후 재측정

## 12. 이슈 브레이크다운

### Phase 0

> ~~**이슈 0-1: ticker 표준화**~~ — 실측 결과 이미 `005930` 통일됨. **불필요.**

> ~~**이슈 0-3: 리포트 백필**~~ — 실측 결과 2,319건 이미 적재됨. **불필요** (더 긴 기간 원하면 `backfill_6_months()` 선택적 실행).

> **이슈 0-2: 재무제표 selective query tool**
> - `get_financials(tickers[], stmt_type, item_names[], period_type, start/end)` 구현
> - 이중 unnest SQL(실측 검증됨), 다종목 `ANY(:tickers)`, long format 반환
> - 성장률 등 계산은 SQL에서
> - 완료: 단일/다종목 EBITDA 추출 정확(10개년 보유), JSONB 통째 반환 안 함

> **이슈 0-4: `pg_bigm` 검색 + Strands POC + SSE**
> - `pg_bigm` 확장 설치 (커스텀 postgres 이미지 필요) + GIN 인덱스
> - ticker 선필터 + bigram 검색
> - `invocation_state` ticker 주입, 결정론/확률 tool 각 1개, SSE 엔드포인트
> - 완료: ticker 무프롬프트 전달, 실제 수치/리포트 응답(환각 아님), SSE 정상

> **이슈 0-5: 채팅 세션 스키마 + history/CRUD (§16-4, §17-1)**
> - `agent` 스키마 + alembic `0002_agent_chat` — `chat_sessions`/`chat_messages`(parent_id, status enum)
> - `alert_service.users` FK, 세션=ticker 고정
> - new chat / 세션목록 / 메시지조회(Recursive CTE) 엔드포인트, 기존 `current_user_id` JWT 재사용
> - 완료: 세션 생성→조회→soft-delete 동작, parent_id 컬럼 존재(브랜칭 토대), 활성경로 CTE 동작

### Phase 1

> **이슈 1-1: MarketAnalystAgent 프로덕션** — 전 tool + structured output, UC 매트릭스(§15) L1~L3+비교 커버
> **이슈 1-2: 리포트 검색 골든셋 + baseline** — Ragas 50개, Recall@5/MRR/Precision 기록 (UC 단위 eval은 Phase 2)
> **이슈 1-3: 가드레일 + 예측 면책 정책 테스트** — 15개+ 금지 프롬프트
> **이슈 1-4: 채팅 제품층 완성 (§17-2~4)** — SSE 스트림 + in-process stop(부분응답 status='interrupted' 저장), regenerate(parent_id 브랜칭), 자동 제목, `PostgresSessionRepository` 어댑터로 Strands 멀티턴 복원

### Phase 2

> **이슈 2-1: Coordinator + 3 specialist**
> **이슈 2-2: agents-as-tools + A2A 형태 contract**

## 13. 주의사항

- **ticker 이미 통일됨**: `reference`/`serving` 모두 `005930`. 별도 정규화 불필요 (실측)
- **코퍼스 이미 확보**: 리포트 2,319건 적재됨. 검색 측정 즉시 가능 (실측)
- **재무제표는 검색 아님**: selective query(결정론). 숫자를 fuzzy 매칭하지 말 것
- **consensus summary 컬럼 없음**: `full_text`만 존재. 요약은 LLM 생성 or full_text 노출
- **자유 SQL 금지**: 고정 템플릿 + 파라미터만. LLM이 SQL 생성 안 함
- **계산은 SQL/도구**: LLM 산술 신뢰 불가. 성장률·비율은 SQL에서
- **JSONB 통째 금지**: selective projection으로 컨텍스트 예산 관리
- **reference ORM 부재**: agent tool은 raw SQL (ORM은 별도 stock-crawler 레포)
- **pg_bigm 설치**: base 이미지에 없음 → 커스텀 postgres 이미지 빌드 필요
- **검색 과설계 금지**: pgvector/reranker는 측정 후. baseline은 pg_bigm
- **예측 단정 금지**: 시나리오 분석으로만
- **세션=ticker 고정**: 종목 바꾸면 새 세션. ticker는 세션에서 읽어 ambient 주입(LLM 추출 금지)
- **대화상태는 우리 소유**: Strands는 stateless brain. chat_messages가 SoT, LangGraph/blob 저장 금지
- **parent_id는 처음부터**: regenerate 브랜칭의 토대. 나중에 추가 불가하므로 0-5에서 컬럼 포함
- **v1 stop은 in-process**: `is_disconnected`+`try/finally`. Redis pub/sub는 멀티워커 확장 시(§17-2)
- **UC 평가 v1 보류**: UC 커버리지만 넓힘. UC별 정량 eval은 trace 쌓인 후 Phase 2

## 14. 에스컬레이션 트리거

- Recall@5/MRR baseline 불충분 → reranker 또는 `textsearch_ko` (Phase 3)
- 의미 기반 검색 필요 → pgvector + RRF 하이브리드 재평가
- 지연/소유권 압력 → specialist를 별도 A2A 서비스로 분리
- 리포트 본문 전체(다중 페이지) 필요 → 수집기 full PDF 추출 확장
- 동시 사용자/멀티 워커로 stop 비용 증가 → Redis Pub/Sub + stop_signal + Celery/TaskIQ 디커플(§17-2)
- 실사용 trace 축적 → UC 매트릭스(§15) 단위 정량 eval 스위트 구성(Phase 2)

## 15. 유스케이스 매트릭스

v2까지 UC가 0개였던 게 가장 추상적인 부분이었다. doc 13의 시세 전용 UC 6개는 펀더멘털/리포트 도메인에 그대로 맞지 않는다. v3은 **데이터 축**과 **조합 차원**의 곱으로 UC를 체계적으로 도출한다.

### 15-1. 두 차원

- **데이터 축 4개**: 주가(시세) · 이벤트(급등/급락/VI/거래정지) · 재무제표 · 리포트
- **조합 차원**: 단일(1축) → 2축 → 3축 → 종목비교(다종목)

조합 차원이 핵심이다. **L2/L3(여러 축을 섞는 복합 질문)이야말로 에이전트 loop의 존재 이유**다 — LLM이 어떤 tool을 어떤 순서로 호출할지 자율 조율해야 답할 수 있다. 단일 축(L1)은 tool 1~2개로 끝나지만, "급락했는데 펀더멘털 문제야?"(L2)는 이벤트 조회 + 재무 selective query를 엮어야 한다.

### 15-2. L1 — 단일 축 (4종)

| UC | 축 | 예시 질문 | 핵심 tool |
| --- | --- | --- | --- |
| L1-P | 주가 | "지금 왜 올라?" | `get_symbol_snapshot`, `get_intraday_bars` |
| L1-E | 이벤트 | "오늘 VI 걸린 적 있어?" / "급등 신호 떴어?" | `get_signal_timeline` |
| L1-F | 재무 | "최근 3년 EBITDA 추이?" | `get_financials` (+SQL 성장률) |
| L1-R | 리포트 | "최근 리포트 뭐래?" / "목표주가 컨센서스?" | `get_recent_reports`, `get_consensus`, `search_reports` |

### 15-3. L2 — 2축 조합 (6종, 4C2)

| UC | 조합 | 예시 질문 |
| --- | --- | --- |
| L2-PE | 주가+이벤트 | "급등했는데 지금 가격 어디쯤?" |
| L2-PF | 주가+재무 | "오르는데 재무도 받쳐줘?" |
| L2-PR | 주가+리포트 | "현재가가 목표주가 대비 어디쯤?" |
| L2-EF | 이벤트+재무 | "급락했는데 펀더멘털 문제야?" |
| L2-ER | 이벤트+리포트 | "VI 걸린 거 리포트에 언급 있어?" |
| L2-FR | 재무+리포트 | "재무는 좋은데 리포트 의견은?" |

### 15-4. L3 — 3축+ 복합 (대표 2종)

| UC | 조합 | 예시 질문 | 비고 |
| --- | --- | --- | --- |
| L3-PEF | 주가+이벤트+재무 | "급등 신호 뜬 종목인데, 가격·재무 종합하면?" | tool 3개 조율 |
| L3-PFR | 주가+재무+리포트 | "지금 살 타이밍이야?" | **예측 면책 가드레일**(§10) — 단정 금지, 시나리오로만 |

### 15-5. C — 종목 비교 (다종목)

| UC | 축 | 예시 질문 | tool |
| --- | --- | --- | --- |
| C-F | 재무 비교 | "삼성 vs 하이닉스 수익성" | `compare_financials(['005930','000660'])` |
| C-PR | 주가+리포트 비교 | "NAVER vs 카카오, 목표가 상승여력 큰 쪽?" | `get_consensus` + `get_symbol_snapshot` 다종목 |

비교는 ambient ticker가 아니라 LLM이 명시적으로 `tickers=[...]` 파라미터를 tool에 넘긴다(§3-2 계승).

### 15-6. v1 범위와 평가 정책

- **v1 범위**: L1 + L2 + L3 + 비교 **전부 포함.** 단일 `MarketAnalystAgent`의 agent loop가 tool 순서를 조율하므로 조합 UC도 코드 추가 없이 동작한다(tool 등록만 하면 됨).
- **평가는 v1 보류**: UC 커버리지는 넓히되, 정량 평가(Recall@5/MRR 골든셋, UC별 정답 루브릭)는 **v1에서 측정하지 않는다.** 리포트 검색 baseline 측정(§7)만 유지하고, UC 단위 eval 스위트는 실사용 trace가 쌓인 후 Phase 2에서 구성한다.
- **함의**: v1은 "동작 + 가드레일 준수"까지만 검증. UC 매트릭스는 프롬프트 설계와 tool 등록의 체크리스트 역할이며, 평가셋 구축 비용을 v1에 지우지 않는다.

## 16. 채팅 제품층 (chat product layer)

v2는 `invocation_state`로 ticker를 주입하는 것까지만 정의했다. 실제 챗 제품이 되려면 일반 AI 챗의 기본 기능(멀티턴, new chat, history, stop, regenerate, 자동 제목, 삭제/이름변경)이 필요하다. v3은 이 제품층을 정의한다.

### 16-1. 핵심 원칙 — Strands는 stateless brain, 대화 상태는 우리 Postgres가 소유

5각도 조사(부록 B)의 일치된 결론: **"프레임워크가 데이터를 소유하게 하지 마라."**

- 대화 기록/세션의 **source of truth = 우리가 설계한 `agent.chat_sessions`/`chat_messages` 테이블.**
- Strands는 **stateless brain** — 매 턴 우리가 컨텍스트를 먹여주고, 에이전트는 tool 호출과 응답 생성만 한다.
- Strands의 `SessionManager`(working-context 복원)는 우리 테이블 위에 **얇은 `SessionRepository` 어댑터**로 얹는다(§17-4).

실측: Strands는 `SessionManager`/`SessionRepository` 추상 인터페이스 + `FileSessionManager`/`S3SessionManager`만 제공. **PostgreSQL 백엔드는 공식·커뮤니티 모두 부재** → 직접 구현(BUILD) 확정.

### 16-2. 세션 = ticker 고정

- **한 세션 = 한 종목 차트 페이지.** `chat_sessions.ticker` NOT NULL.
- `invocation_state.current_ticker`는 **세션에서 읽어 매 턴 주입.** LLM이 프롬프트에서 ticker 추출하지 않음(환각 차단, §3-2 계승).
- 종목 비교(C-*)는 LLM이 tool 파라미터 `tickers=[...]`로 명시 확장. ambient는 기본 1종목.
- 종목을 바꾸면 **새 세션**.

### 16-3. 메시지 모델 — parent_id 인접 리스트 트리

OSS 챗앱 5개(LibreChat·Open WebUI·Lobe Chat·Chatbot UI·Vercel AI) 실측 결과, regenerate/branching의 업계 표준은 **`parent_id` 인접 리스트 트리**다. 단순 "마지막 메시지 삭제 후 재생성"이 아니라, 같은 부모에 형제 메시지를 추가해 버전 토글("2 of 3")을 지원한다.

- **regenerate**: 메시지 N에서 재생성 시, `parent_id = messages[N].parent_id`인 새 메시지를 만든다(N을 삭제하지 않음).
- **활성 경로 조회**: 전체 트리를 받아 Python에서 필터하지 말고 **Postgres Recursive CTE**로 leaf→root 활성 경로만 추출(커뮤니티 권장, 성능 우위).
- **중단 상태**: 불리언(`unfinished`/`done`) 대신 **단일 `status` enum**('streaming'/'complete'/'interrupted'/'error')이 SSE 상태 관리에 깔끔(Lobe Chat 패턴).

### 16-4. 스키마 (SQLAlchemy async + alembic)

기존 `alert_service`가 SQLAlchemy async + asyncpg + alembic을 쓰므로 동일 패턴. 신규 스키마 `agent`, alembic 마이그레이션 `0002_agent_chat` 추가.

```sql
CREATE SCHEMA IF NOT EXISTS agent;

CREATE TABLE agent.chat_sessions (
    session_id    UUID PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES alert_service.users(user_id),
    ticker        TEXT NOT NULL,                 -- 세션=종목 고정 (005930 포맷)
    title         TEXT,                          -- 자동 생성 (첫 Q/A 후 LLM 1콜)
    is_archived   BOOLEAN NOT NULL DEFAULT false, -- soft-delete
    meta          JSONB NOT NULL DEFAULT '{}',    -- 확장용 (시간프레임 등)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sessions_user_active
    ON agent.chat_sessions (user_id, updated_at DESC)
    WHERE is_archived = false;

CREATE TABLE agent.chat_messages (
    message_id    UUID PRIMARY KEY,
    session_id    UUID NOT NULL REFERENCES agent.chat_sessions(session_id) ON DELETE CASCADE,
    parent_id     UUID REFERENCES agent.chat_messages(message_id) ON DELETE SET NULL, -- 브랜칭
    role          TEXT NOT NULL,                 -- 'user'|'assistant'|'tool'|'system'
    content       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'complete', -- 'streaming'|'complete'|'interrupted'|'error'
    tool_trace    JSONB,                         -- 호출한 tool/파라미터/evidence
    usage         JSONB,                         -- {prompt_tokens, completion_tokens}
    error         JSONB,                         -- {code, message}
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_session_parent
    ON agent.chat_messages (session_id, parent_id);
```

설계 결정:
- `user_id` → `alert_service.users` FK (기존 사용자 테이블 재사용)
- `parent_id` self-FK → regenerate/edit 브랜칭의 토대 (나중에 추가 불가하므로 처음부터 포함)
- `status` enum → SSE 'Stop'으로 끊긴 부분응답을 `'interrupted'`로 저장
- `tool_trace` JSONB → §9 structured output(evidence/freshness)을 메시지에 영속화

## 17. API 계약 + 스트리밍

### 17-1. 엔드포인트

기존 `alert_service` FastAPI 앱에 라우터 추가(별도 서비스 분리 안 함, doc 13 Phase 1 계승). JWT는 기존 `current_user_id` deps 재사용.

| 메서드 | 경로 | 기능 |
| --- | --- | --- |
| POST | `/agent/sessions` | new chat — `{ticker}` 받아 session_id 발급 |
| GET | `/agent/sessions` | 내 세션 목록 (Recursive CTE 아님, 단순 목록) |
| GET | `/agent/sessions/{id}/messages` | 활성 경로 메시지 (Recursive CTE) |
| POST | `/agent/sessions/{id}/stream` | 메시지 전송 → SSE 스트림 |
| POST | `/agent/sessions/{id}/messages/{mid}/regenerate` | parent_id 브랜칭 재생성 → SSE |
| PATCH | `/agent/sessions/{id}` | 제목 수정 / archive(soft-delete) |
| DELETE | `/agent/sessions/{id}` | soft-delete (is_archived=true) |

자동 제목: 첫 Q/A 완료 후 LLM 1콜로 title 생성 → 세션 update(비동기, 스트림 차단 안 함).

### 17-2. SSE 스트리밍 + stop (v1: in-process 취소)

Strands `agent.stream_async()`(공식 async iterator) → FastAPI `StreamingResponse`로 yield. 'Stop'은 v1에서 **in-process 취소**로 처리한다.

```python
@router.post("/agent/sessions/{session_id}/stream")
async def stream(session_id: UUID, body: ChatIn, request: Request,
                 user_id: UUID = Depends(current_user_id)):
    ticker = await load_session_ticker(session_id, user_id)  # 세션=ticker 고정
    accumulated: list[str] = []
    async def gen():
        try:
            async for ev in agent.stream_async(
                body.text, invocation_state={"current_ticker": ticker}
            ):
                if await request.is_disconnected():   # 클라이언트 Stop/탭닫기
                    break
                if "data" in ev:
                    accumulated.append(ev["data"])
                    yield f"data: {ev['data']}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            # 부분응답을 status로 구분해 영속화
            status = "complete" if await_not_disconnected(request) else "interrupted"
            await persist_assistant_message(session_id, "".join(accumulated), status)
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})  # Nginx 버퍼링 차단
```

- **Zombie Generation 인지**: 커뮤니티가 지적한 함정 — stop 후에도 백엔드 LLM이 토큰을 계속 태우는 문제. v1은 **41종목 소규모 + 단일 워커**라 zombie 비용이 감당 가능하므로 `request.is_disconnected()` 체크 + `try/finally` 부분저장으로 충분하다.
- **에스컬레이션(§14)**: 동시 사용자/멀티 워커로 확장되면 **Redis Pub/Sub + stop_signal 플래그 + Celery/TaskIQ worker**로 디커플(커뮤니티 표준). 우리 스택엔 아직 Redis가 없어 v1 도입하지 않음. Kafka는 per-request 취소 신호용으론 지연/오버헤드가 커 부적합.

### 17-3. regenerate

`messages/{mid}/regenerate`는 mid의 `parent_id`를 그대로 물려받은 새 assistant 메시지를 생성하고 SSE로 스트림한다. 기존 mid는 삭제하지 않으므로 프론트가 형제 버전("2 of 3")을 토글할 수 있다.

### 17-4. SessionRepository 어댑터 (우리 테이블 위에)

멀티턴 working-context 복원은 Strands `RepositorySessionManager`에 우리 테이블을 백엔드로 꽂아 처리한다. `SessionRepository`(ABC) 8개 메서드를 위 `chat_sessions`/`chat_messages`에 매핑:

```python
from strands.session.session_repository import SessionRepository
from strands.session.repository_session_manager import RepositorySessionManager

class PostgresSessionRepository(SessionRepository):
    # create/read_session, create/read/update_agent,
    # create/read/update/list_message → agent.chat_* 테이블에 매핑
    ...

session_manager = RepositorySessionManager(
    session_id=str(session_id),
    session_repository=PostgresSessionRepository(session_factory),
)
agent = Agent(session_manager=session_manager, tools=[...])
```

- **단계**: v1은 ① 우리 테이블 + history/CRUD API를 먼저 구축(동작 확인) → ② 그 위에 `SessionRepository` 어댑터로 Strands 멀티턴 복원 연결. 같은 테이블을 둘이 공유.
- 컨텍스트 윈도잉(어떤 메시지를 LLM에 보낼지)은 Strands `ConversationManager`(`SlidingWindowConversationManager`)가 담당 — 직접 짜지 않음.

## 부록 A. 근거 (측정 데이터)

**Text-to-SQL 고정 템플릿:**
- 실행 정확도 Spider 91% → BIRD 82% → 엔터프라이즈 6~21% (자유 SQL 위험)
- 고정 템플릿(intent+slot) Exact Match 92% vs 자유생성 60%
- 350 템플릿이 프로덕션 쿼리 93.5% 커버

**검색 (dense 부적합, lexical+reranker 적합):**
- 임베딩 숫자 과제 0.54(랜덤 수준), 티커/수치 정확매칭 실패 (EACL/EMNLP)
- 한국 금융 BM25(NDCG@5 34.35) > text-embedding-3-small(31.21)
- reranker 한국어 MIRACL 25.8 → 44.0, Recall@5 ~60→90%+

**평가 (Recall@5):**
- ticker-scoped 작은 셋에서 Recall@10은 vanity (거의 1.0)
- LLM "lost in the middle" → k=5로 primacy zone 유지
- 리포트 relevant 보통 1~3건 → k=5 충분
- 합성 쿼리 어휘 누수로 점수 부풀림 → human anchor + trace 샘플링

**컨텍스트 예산 (selective projection):**
- 재무 JSONB 1행 수십~수백 KB → 항목당 ~20토큰으로 축소
- long format flat rows가 nested JSON보다 LLM 비교 정확도 우수
- LLM 산술 신뢰 불가 → 계산은 SQL

## 부록 B. 채팅 제품층 빌드-vs-어답트 근거 (5각도 조사)

§16/§17의 "직접 빌드" 결정은 5개 각도의 조사가 모두 같은 곳으로 수렴한 결과다.

### B-1. 턴키 챗봇 프레임워크 — 존재하나 UI 강결합

| 프레임워크 | 언어 | 대화관리 | PG | 브랜칭 | 라이선스 |
| --- | --- | --- | --- | --- | --- |
| Open WebUI | Python | 완비(폴더/핀/자동제목/soft-delete) | 네이티브(SQLAlchemy) | 풀 트리(`parent_id`) | MIT |
| Onyx(Danswer) | Python | 엔터프라이즈급 | 네이티브 | 풀 트리(`parent_message`/`latest_child`) | MIT |
| Chainlit | Python | 개발자용(thread resume) | 네이티브 | 부분(`steps.parentId`) | Apache 2.0 |
| LibreChat | Node | 고급(forking 3모드) | RAG만(history는 Mongo) | 풀 트리(`parentMessageId`) | MIT |

→ 턴키는 존재한다. 그러나 전부 **자체 UI에 강결합** — 백엔드만 떼어내려면 우리 도메인(ticker 고정 세션, Strands tool, 41슬롯 가드레일)과 무관한 거대 추상화를 통째로 들여야 한다.

### B-2. LangGraph 영속화 — 채택 부적합

- **결합도**: `PostgresSaver`는 Pregel 런타임에 강결합. `channel_versions`/`versions_seen` 내부 메타 필요 → Strands 에이전트를 LangGraph 셸로 감싸야 함.
- **조회 불가**: 상태를 `checkpoint_blobs`에 BYTEA blob으로 저장 → SQL로 chat history 직접 조회 불가(UI/분석 막힘).
- **프로덕션 함정**(커뮤니티 실측): 직렬화 85% 스토리지 bloat + 37% 토큰 overhead(#7714), async durability 메모리 누수 OOM(#7094), 체크포인트는 크래시 미감지(외부 워치독 필요, Diagrid).
- → blob이 아닌 plain text/JSONB로 우리가 소유하는 게 정답.

### B-3. OSS 챗앱 5개 스키마 실측 — `parent_id` 트리로 수렴

| 프로젝트 | 엔진 | 브랜칭 | 중단상태 | soft-delete |
| --- | --- | --- | --- | --- |
| LibreChat | Mongo | `parentMessageId` 트리 | `unfinished:bool` | `expiredAt` TTL |
| Open WebUI | PG/SQLite | `parent_id` 트리 | `done:bool` | `archived:bool` |
| Lobe Chat | PG(Drizzle) | `parentId` + message_groups | `status:enum` | `status:archived` |
| Chatbot UI | PG(Supabase) | 선형(`sequence_number`) | — | — |
| Vercel AI | PG(Drizzle) | 선형 | Stream 테이블 | — |

→ 메이저 3개(LibreChat/Open WebUI/Lobe Chat)가 `parent_id` 인접 리스트 트리. 중단상태는 Lobe Chat의 `status` enum이 불리언보다 깔끔 → §16-4 스키마에 반영.

### B-4. 커뮤니티 실무자 신호 (Reddit/HN/blog) — "프레임워크가 데이터를 소유하게 하지 마라"

- **LangGraph**: 로직 그래프로는 best지만 영속화는 직접(커스텀 saver로 메타데이터 tax 회피).
- **Chainlit**: "Streamlit of Chat" — 내부 도구엔 좋지만 프로덕트엔 rip-and-replace. 2025 커뮤니티 유지보수 전환.
- **SSE stop = Zombie Generation**: 제대로 하려면 FastAPI ↔ LLM task 디커플 → Celery/TaskIQ + Redis Pub/Sub + stop_signal. 단순 wrapper는 `GeneratorExit` 전파 실패.
- **브랜칭**: Postgres 인접 리스트(`parent_id`) + Recursive CTE로 활성 경로 조회가 표준.
- **2026 컨센서스**: "프레임워크 미니멀리즘" — Agent SDK는 stateless brain, 대화상태/스트리밍은 직접 Postgres/Redis 소유.

출처: open-webui/open-webui `models/chat_messages.py`, onyx-dot-app/onyx `db/models.py`, lobehub/lobe-chat `schemas/message.ts`, danny-avila/LibreChat `schema/message.ts`, langchain-ai/langgraph `checkpoint-postgres/base.py` 및 issue #7714/#7094, Strands `repository_session_manager.py` / `session_repository.py`, Diagrid "checkpoints are not durable execution".

### B-5. 종합 결론

| 레이어 | 결정 | 근거 |
| --- | --- | --- |
| 에이전트 두뇌 | Strands (stateless brain) | doc 13/18 계승 |
| 세션/메시지 영속화 | **직접 빌드** (chat_sessions/chat_messages) | 5각도 수렴, 데이터 소유권 |
| 브랜칭/regenerate | `parent_id` 인접 리스트 + Recursive CTE | OSS 표준 |
| 스트리밍 stop | v1 in-process(`is_disconnected`) → 확장 시 Redis pub/sub | 우리 스택에 Redis 부재 |
| Strands 연동 | chat_messages=SoT, 위에 `SessionRepository` 어댑터 | Strands PG 백엔드 부재 → BUILD |

## Related Notes

- [[13-agent-layer-proposal]]
- [[11-design-freeze-discussion-pack]]
- [[15-batch-enrichment-design]]
- [[event-driven-stock-pipeline]]
