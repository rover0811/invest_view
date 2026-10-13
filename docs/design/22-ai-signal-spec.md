# 22. AI 시그널 기능 명세

## 배경

- 목표는 토스증권 "AI 시그널"과 같은 기능을 구현하기입니다.
- AI 시그널은 주가가 왜 올랐는지, 왜 떨어졌는지를 짧은 문장으로 알려 줍니다.
- 이 기능을 만들려면 뉴스, 소셜, 공시 데이터를 실시간으로 모아야 합니다.
- 그리고 LLM으로 그 데이터를 요약하고, 분석하고, 필요한 정보를 추출해야 합니다.

## 요약

- 이 문서는 가격 급변에 "관련 소식"을 연결하는 데이터 모델을 정의합니다.
- 저장소는 PostgreSQL만 사용하고, Neo4j는 사용하지 않습니다.
- 각 절의 상태 표시는 다음과 같습니다.
  - **[합의]**: 결정이 끝난 내용입니다.
  - **[제안]**: 아직 합의하지 않은 내용입니다.
  - **[미정]**: 다음에 결정할 내용입니다.

## 1. 목표

### 1.1 만들 결과

급변이 발생하면, 그 급변과 관련된 이벤트를 연결합니다.

```
SK하이닉스  -10.2%
중국 메모리 경쟁 우려로 반도체 업종 급락
```

### 1.2 원칙

1. 이 기능은 인과를 주장하지 않습니다. 이 기능은 "관련 소식"을 보여 줍니다.
2. 가격이 먼저 움직이고 기사가 나중에 나오는 경우가 많습니다. 그래서 모든 연결에 선행과 후행을 기록합니다.
3. 근거가 약하면 문구를 보여 주지 않습니다. 틀린 설명보다 빈칸이 낫습니다.

### 1.3 이 설계가 필요한 이유

2026-10-13에 KIS 종합 시황/공시(제목) API로 SK하이닉스(000660)의 기사 169건을 수집했습니다. 다음 문제를 확인했습니다.

| 관찰 | 수치 | 설계에 주는 영향 |
| --- | --- | --- |
| 원천 종목 태그가 000660인 기사 | 169건 중 50건 (30%) | 원천 태그만으로는 종목 연결이 부족합니다 |
| 기사당 종목 태그 수 | 169건 모두 1개 | 두 종목을 다룬 기사도 한 종목에만 연결됩니다 |
| 같은 일련번호에서 제목이 바뀐 기사 | 1건 확인 | 문서에 버전이 필요합니다 |
| 같은 제목의 중복 기사 | 12건 | 기사를 사건 단위로 묶어야 합니다 |
| 시장 시황 기사 | 169건 중 78건 | 시장 요인과 종목 요인을 나눠야 합니다 |

## 2. 전체 구조 [합의]

```
document --< document_entity >-- entity         관찰: 이 기사에 이 엔티티가 있다
   |
   +-- event_id (N:1)                            클러스터링: 이 기사는 이 사건에 속한다
          |
        event --< event_entity >-- entity        판단: 이 사건의 대상과 역할
          |
          +--< attribution >-- move              귀속: 이 사건이 이 급변과 관련된다

entity --< entity_relation >-- entity            관계: 엔티티 사이의 구조 정보
```

두 관계를 구분합니다.

| 관계 | 질문 | 연결 |
| --- | --- | --- |
| 클러스터링 | 이 기사들은 같은 사건인가? | 문서 N : 이벤트 1 |
| 귀속 | 이 사건은 이 급변과 관련되는가? | 이벤트 N : 급변 M |

## 3. 테이블 구조 규칙 [합의]

- 유형이 여러 개인 테이블은 공통 테이블과 서브타입 테이블로 나눕니다. 이 방식을 class table inheritance라고 합니다.
- 공통 테이블에는 모든 유형이 공유하는 필드만 둡니다.
- 서브타입 테이블은 공통 테이블과 1:1로 연결합니다. 서브타입의 PK는 공통 테이블의 FK입니다.
- 유형별 필드에 JSON 컬럼을 사용하지 않습니다. 타입과 NOT NULL 제약으로 값을 보호합니다.
- 새 유형을 추가할 때는 서브타입 테이블을 하나 추가합니다. 공통 테이블은 바꾸지 않습니다.

이 규칙은 `entity`, `document`, `event`에 적용합니다.

## 4. 엔티티 [합의]

### 4.1 엔티티 테이블

```
entity              entity_id PK, entity_type, name
 +- entity_symbol   국내 상장 종목 (ticker, market)
 +- entity_sector   섹터 (분류 체계, 코드, 레벨)
 +- entity_market   시장 (KOSPI, KOSDAQ, US 등)
 +- entity_org      외부 기업과 기관 (CXMT, 연준, 마이크론 등)
```

- 모든 엔티티는 같은 ID 체계를 사용합니다. 그래서 관계 테이블에서 종류가 다른 엔티티를 연결할 수 있습니다.
- 섹터 코드는 KIS 주식기본조회의 지수업종 분류를 사용합니다.

### 4.2 별칭 테이블

```
entity_alias        entity_id, alias, match_rule
```

- 이 테이블은 제목에서 엔티티를 찾는 별칭 사전입니다.
- 예: 000660에는 "SK하이닉스", "하이닉스", "하닉", "닉스"가 있습니다.
- 예: "삼전닉스"는 005930과 000660에 함께 연결합니다.
- 외부 기업도 같은 방식으로 연결합니다. 예: "CXMT", "창신메모리"

## 5. 엔티티 관계 [합의]

### 5.1 관계 테이블

```
entity_relation
  from_entity_id, to_entity_id, relation_type
  valid_from, valid_to
  known_at
  source                  KIS_SECTOR | MANUAL | LLM_EXTRACT
  evidence_document_id
  decided_by, probability
```

- 섹터 소속은 컬럼이 아니라 관계로 저장합니다. 한 종목이 여러 분류 체계나 테마에 속할 수 있기 때문입니다.

### 5.2 관계 유형 정의

관계 유형은 정의 테이블에 등록된 값만 사용합니다.

```
relation_type_def
  relation_type PK, from_entity_type, to_entity_type,
  is_symmetric, is_time_varying, description
```

| relation_type | from | to | 대칭 | 예 |
| --- | --- | --- | --- | --- |
| `BELONGS_TO` | symbol | sector | 아님 | 000660 소속 반도체 |
| `COMPETES_WITH` | symbol, org | symbol, org | 대칭 | 000660, CXMT |
| `SUPPLIES_TO` | symbol, org | symbol, org | 아님 | 한미반도체가 000660에 공급 |
| `SUBSIDIARY_OF` | symbol, org | symbol, org | 아님 | 솔리다임이 000660의 자회사 |

### 5.3 무결성 규칙

1. `entity_relation.relation_type`은 `relation_type_def`를 FK로 참조합니다.
2. from과 to의 엔티티 종류가 정의와 맞는지 적재할 때 검사합니다.
3. 대칭 관계는 한 행만 저장합니다. 두 ID를 정렬해서 저장하고, 조회할 때 양방향으로 읽습니다.

### 5.4 이번 범위의 데이터

- `BELONGS_TO`: KIS 지수업종으로 채웁니다.
- 나머지 3개 유형: 반도체 종목 중심으로 수동 시드 20건에서 30건을 넣습니다.
- 수동 시드는 `source=MANUAL`로 표시합니다. 나중에 자동 추출 결과를 평가할 때 정답셋으로 사용합니다.
- 기사에서 LLM으로 관계를 추출하는 작업은 이번 범위에 넣지 않습니다.

### 5.5 그래프 DB를 사용하지 않는 이유

- 필요한 질의 깊이는 2홉 이내입니다. 조인과 재귀 CTE로 처리할 수 있습니다.
- 모든 홉에 시점 조건(`known_at <= :at`)이 필요합니다. SQL에서는 WHERE 조건 하나로 처리합니다.
- 이벤트, 링크, 귀속을 같은 트랜잭션에서 적재합니다. 저장소를 나누면 동기화 문제가 생깁니다.
- 홈랩은 16GB 단일 노드입니다. JVM 기반 그래프 DB를 추가할 메모리가 부족합니다.
- 다음 조건이 생기면 그래프 DB를 다시 검토합니다.
  - 깊이가 정해지지 않은 다중 홉 탐색을 대량으로 해야 할 때
  - 경로마다 점수를 매겨 상위 경로만 남겨야 할 때
- 테이블은 노드와 간선에 1:1로 대응합니다. 그래서 나중에 이관할 때 비용이 작습니다.

## 6. 문서 [합의, 필드는 미정]

- KIS 기사와 증권사 리포트를 같은 `document` 테이블에 저장합니다.
- 원천별 필드는 서브타입 테이블에 저장합니다. 예: `document_kis_news`, `document_report`
- 문서는 이벤트에 N:1로 연결합니다.

### 6.1 문서와 엔티티의 연결

```
document_entity     document_id, entity_id, link_method, link_confidence
```

- 이 테이블은 관찰한 사실을 저장합니다. 이 값은 수정하지 않습니다.
- `link_method`는 연결 방법입니다.
  - `source_tag`: 원천이 붙인 종목 태그
  - `title_alias`: 별칭 사전으로 제목에서 찾은 결과
- 링크 정확도 지표는 이 테이블로 계산합니다.

### 6.2 제목에서 엔티티를 찾는 방법

1. `entity_alias` 전체로 제목을 한 번에 검색합니다.
2. 겹치는 결과가 있으면 긴 별칭을 선택합니다.
3. 일반 단어와 같은 종목명은 금지 목록으로 제외합니다. 예: "대상", "한국"
4. 그룹명만 있는 경우는 종목에 연결하지 않습니다. 예: "SK", "LG"

## 7. 이벤트 [합의, 필드는 미정]

- 이벤트는 공통 테이블 `event`와 유형별 서브타입 테이블로 구성합니다.
- 귀속 로직은 공통 테이블만 읽습니다.
- 이번 범위의 유형은 `NEWS`, `TARGET_PRICE_CHANGE`입니다.
- 공시, 실적, 금리는 이후에 서브타입 테이블로 추가합니다.

### 7.1 이벤트와 엔티티의 연결

```
event_entity
  event_id, entity_id
  role              primary | affected | actor
  decided_by        rule | llm | human
  probability
  model_version
```

- 이 테이블은 판단 결과를 저장합니다. 규칙이나 모델이 바뀌면 다시 계산합니다.
- `role`의 의미는 다음과 같습니다.
  - `primary`: 사건의 주 대상입니다.
  - `affected`: 사건의 영향을 받는 대상입니다.
  - `actor`: 사건을 일으킨 외부 행위자입니다.

### 7.2 판정 방법

| 단계 | 방법 | 결과 |
| --- | --- | --- |
| 1 | 별칭 사전 매칭 | `document_entity` |
| 2 | 연결된 종목의 지수업종과 섹터 키워드 사전 | 섹터 후보 |
| 3 | 이벤트당 LLM 1회 호출 | `event_entity`, 범위, 방향 |

- 3단계는 저가 모델을 사용합니다. 예: Claude Haiku, OpenRouter의 저가 모델
- 모델에는 2단계의 후보만 선택지로 줍니다. 그래서 모델이 없는 섹터를 만들지 못합니다.
- 임베딩 유사도는 범위 판정에 사용하지 않습니다. 유사도는 주제가 비슷한지만 알려 줍니다. 사건의 주 대상은 알려 주지 않습니다.

## 8. 급변과 귀속 [제안]

이 절은 아직 합의하지 않았습니다.

### 8.1 급변

```
move
  move_id             기존 alert_event_id를 사용
  symbol_entity_id
  window_start, window_end, direction, return_pct
  contrib_global, contrib_sector, contrib_idio
  beta_global, beta_sector, beta_estimated_on
  dominant_layer      GLOBAL | SECTOR | IDIO
  attribution_status  SHOWN | HIDDEN | NO_CANDIDATE
  version
```

- 수익률 분해 결과는 급변의 속성입니다. 그래서 귀속 테이블이 아니라 `move`에 둡니다.
- 기존 `gold.alert_events`는 수정하지 않습니다.

### 8.2 수익률 분해

```
r_i = beta_g * r_global + beta_s * r_sector_ex_i + e_i
```

- `r_global`: 직전 미국 정규장의 반도체 지표 수익률입니다. 한국 장보다 먼저 끝난 값입니다. 그래서 한국 시장이 이 값에 영향을 줄 수 없습니다.
- `r_sector_ex_i`: 같은 섹터에서 종목 i를 뺀 바스켓 수익률입니다. 비교 기준에 자기 자신이 들어가지 않습니다.
- `e_i`: 종목 고유 요인입니다.
- 이 분해는 통계적 분해입니다. 인과 추정이 아닙니다.

### 8.3 귀속

```
attribution
  move_id, event_id       PK
  rank
  timing                  LEADING | LAGGING
  lag_seconds
  score_direction, score_scope, score_source, score_recency, score_total
  reject_reason           DIRECTION_MISMATCH | AFTER_GRACE | LOW_SCORE
  decided_by, model_version, decided_at
  version
```

- 선택된 1건만 저장하지 않습니다. 후보 전체를 저장합니다.
- 이유 1: 탈락 사유를 보여 줄 수 있습니다.
- 이유 2: 선택 규칙을 바꾼 뒤 저장된 점수로 다시 계산할 수 있습니다.
- 늦게 나온 기사로 다시 귀속하면 `move.version`을 올립니다. 서빙 테이블은 버전이 더 클 때만 갱신합니다.

## 9. 남은 질문 [미정]

| 번호 | 질문 |
| --- | --- |
| 1 | 8절(급변, 수익률 분해, 귀속)을 이대로 확정하는가? |
| 2 | `document`의 공통 필드와 원천별 서브타입 필드는 무엇인가? |
| 3 | `event`의 공통 필드와 유형별 서브타입 필드는 무엇인가? |
| 4 | 클러스터링 규칙(유사도 기준, 시간 범위, 대표 문서 선택)은 무엇인가? |
| 5 | 귀속 선택 규칙과 점수 가중치, 문구를 숨기는 기준은 무엇인가? |
| 6 | 귀속을 별도 consumer에서 실행하는가, Flink에서 실행하는가? |
| 7 | 미국 반도체 지표를 어떤 상품으로 수집하는가? |
| 8 | 섹터 바스켓의 종목이 2개 이하일 때 분해를 어떻게 처리하는가? |
| 9 | 품질 지표와 평가셋(수작업 라벨 수, 측정 항목)은 무엇인가? |
| 10 | 서빙 테이블과 API, Agent 도구의 형태는 무엇인가? |
