# 13 Agent Layer Proposal

이 문서는 invest_view 데이터 파이프라인 위에 올릴 **AI 에이전트 레이어**의 유스케이스, 기술 선택, 구현 계획, 이슈 브레이크다운을 담는다.

에이전트 엔지니어가 이 문서 하나로 **뭘 만들어야 하고, 어떤 순서로 하고, 어디까지가 범위인지** 파악할 수 있어야 한다.

## 전제 조건

이 제안은 아래 기존 설계 결정을 따른다.

- Agent의 데이터 읽기 경로는 **PostgreSQL only** — Kafka 직접 접근 금지
- Agent는 **query/analysis 전용** — 매매 주문 실행 없음
- MCP 서버 3개 계획: Market Data, Strategy, Research
- 단계적 접근: 단일 에이전트 → 4 에이전트 (Coordinator + Price + Analysis + Strategy)
- 가드레일 필수: 금융 도메인이므로 액션 실행 전 human/approval 노드 분리

참고 문서: [[11-design-freeze-discussion-pack]], [[12-kis-realtime-ingress-design]]

## 기술 선택

기존 설계는 LangGraph를 전제했으나, 담당 엔지니어의 주 언어에 따라 아래 두 옵션 중 하나를 선택한다.

| | Python → **Strands Agents SDK** | Java → **LangChain4j** |
| --- | --- | --- |
| GitHub | strands-agents/sdk-python (5.7k stars) | langchain4j/langchain4j (7k+ stars) |
| 핵심 패턴 | LLM + system prompt + @tool | AiService + @Tool annotation |
| MCP 통합 | 네이티브 | 지원 |
| 멀티에이전트 | agents-as-tools, swarm, graph, A2A | agent 간 호출 패턴 |
| 스트리밍 | stream_async (SSE/WebSocket) | TokenStream |
| AWS Bedrock | 기본 provider | BedrockChatModel |
| 프로덕션 실적 | Kiro, Amazon Q, Eightcap | 다수 JVM 프로덕션 |
| 프레임워크 통합 | FastAPI | Spring Boot |

두 선택지 모두 이 문서의 유스케이스, 아키텍처, 가드레일, 이슈 구조는 동일하게 적용된다. 달라지는 건 SDK API와 배포 패턴뿐이다.

### 권장: Python (Strands)

두 언어 모두 익숙하지 않은 상태에서 에이전트 개발이 처음이라면 **Python + Strands를 권장**한다.

**Phase 1까지는 비슷하지만 Phase 2에서 차이가 크다.** Strands는 멀티에이전트 패턴(agents-as-tools, swarm, graph, A2A)이 SDK에 내장되어 있어 Coordinator → Specialist 구조를 한 줄로 연결할 수 있다. LangChain4j는 단일 에이전트 프레임워크에 가까워서 라우팅, 응답 병합, 실패 처리, hop 제한 등 오케스트레이션 레이어를 직접 구현해야 한다.

두 언어 모두 새로 배우는 상황이라면 학습 대상은 "언어 + SDK + 에이전트 개념" 세 가지인데, Python + Strands 조합이 이 셋을 가장 빠르게 줄여준다.

- Strands quickstart → 첫 agent 동작까지 30분
- 금융 도메인 샘플이 이미 존재 (personal-finance-assistant)
- 에이전트 커뮤니티, 튜토리얼, 레퍼런스가 Python 중심
- 기존 파이프라인(FastAPI, KIS ingestion)이 이미 Python이므로 코드 재사용 가능

Java를 선택할 합리적 이유가 있다면 (기존 조직 표준, Spring 인프라 의존 등) LangChain4j로 가되, Phase 2 멀티에이전트 구현 공수가 추가된다는 점을 감안할 것.

### 공통 선택 근거 (vs LangGraph)

기존 LangGraph 대비 위 두 옵션이 더 적합한 이유는 동일하다.

- 이 프로젝트는 **tool-calling 기반 read-only 분석 에이전트**이지, 복잡한 상태 머신이 아니다
- Phase 1은 단일 에이전트 + tool 호출이 전부 — StateGraph 보일러플레이트가 불필요
- MCP 네이티브 지원이 있어야 설계 문서의 MCP 서버 3개 구조에 자연스럽게 맞음

LangGraph이 더 나은 경우: 복잡한 human-in-the-loop 워크플로우, 체크포인팅 기반 재시도, 깊은 분기 제어가 핵심이 되는 시점. 현재 이 프로젝트는 해당하지 않는다.

### 학습 경로

에이전트 개발이 처음이라면 담당 언어에 맞는 경로를 따른다.

**Python (Strands)**

1. Strands Python Quickstart — agent loop, @tool, streaming 기초
   - https://strandsagents.com/latest/user-guide/quickstart/python/
2. Strands Finance Sample — 금융 도메인 agent가 tool을 쓰는 실제 예시
   - https://github.com/strands-agents/samples/tree/main/02-samples/11-personal-finance-assistant
3. MCP 개념 — tool을 MCP 서버로 분리하는 패턴
   - https://strandsagents.com/latest/user-guide/concepts/tools/mcp-tools/

**Java (LangChain4j)**

1. LangChain4j Get Started — AiService, @Tool, chat memory 기초
   - https://docs.langchain4j.dev/get-started
2. LangChain4j Tutorials — tool 정의, RAG, streaming 패턴
   - https://docs.langchain4j.dev/tutorials
3. LangChain4j MCP — MCP 서버 연결 패턴
   - https://docs.langchain4j.dev/integrations/mcp

**공통**

4. 우리 설계 문서 — 데이터 파이프라인 + 제약 이해
   - `docs/design/12-kis-realtime-ingress-design.md`

읽지 않아도 되는 것: LangGraph, CrewAI, AutoGen, Spring AI — 이 프로젝트에서는 위 두 옵션 외에는 쓰지 않는다.

## 유스케이스

### UC1. 지금 왜 움직여?

- **예시**: "삼성전자 지금 왜 올라?", "SK하이닉스 약세 이유가 뭐야?"
- **데이터**: symbol_snapshot, symbol_intraday_1m, symbol_signal_timeline
- **Tool**: resolve_symbol, get_symbol_snapshot, get_intraday_bars, get_signal_timeline

### UC2. 라이브 종목 스캔

- **예시**: "지금 브레이크아웃 패턴 나온 종목?", "최근 15분 알림 뜬 종목?"
- **데이터**: symbol_snapshot, symbol_signal_timeline, realtime_subscription_state
- **Tool**: scan_live_symbols, list_recent_alerts, rank_symbols_by_metric

### UC3. 종목 비교

- **예시**: "NAVER vs 카카오 오늘 어때?", "현대차 기아 중에 모멘텀 강한 쪽은?"
- **데이터**: symbol_snapshot, symbol_compare_metrics
- **Tool**: compare_symbols, get_symbol_snapshot

### UC4. 알림 히스토리 리뷰

- **예시**: "LG화학 아침 돌파 신호 이후 어떻게 됐어?", "오늘 POSCO 패턴 요약해줘"
- **데이터**: symbol_signal_timeline, symbol_intraday_1m
- **Tool**: list_recent_alerts, summarize_signal_followthrough, get_intraday_bars

### UC5. 전략 적합도

- **예시**: "모멘텀 전략에 맞는 종목?", "삼성전자 평균회귀 셋업에 해당해?"
- **데이터**: symbol_snapshot, symbol_compare_metrics, symbol_signal_timeline
- **Tool**: score_strategy_fit, rank_strategy_candidates

### UC6. 데이터 커버리지 확인

- **예시**: "셀트리온 지금 실시간 추적 중이야?", "내가 요청한 종목 중 41슬롯에 들어간 건?"
- **데이터**: realtime_subscription_state, symbol_snapshot
- **Tool**: get_realtime_coverage, resolve_symbol

## Agent 읽기 모델

Agent가 접근 가능한 PostgreSQL view 목록. 이 view 외의 테이블은 직접 접근하지 않는다.

| View | 상태 | 용도 |
| --- | --- | --- |
| serving.symbol_snapshot | 기존 | 종목 현재 상태 (가격, 등락, 거래량) |
| serving.symbol_intraday_1m | 신규 | 최근 분봉/피처 (silver 기반 비정규화) |
| serving.symbol_signal_timeline | 신규 | 알림/패턴 이벤트 타임라인 (gold 기반) |
| serving.symbol_compare_metrics | 신규 | 정규화된 종목간 비교 지표 |
| integration.realtime_subscription_state | 신규 | 41슬롯 실시간 구독 상태 |

## 아키텍처

### Phase 1 구조

FastAPI(기존 alert-service) 안에 에이전트 추가. 별도 서비스 분리 안 함.

- 엔드포인트: `POST /agent/chat/stream` (SSE)
- 에이전트: MarketAnalystAgent 1개
- MCP: Market Data MCP (활성) + Strategy MCP (활성)
- Research MCP: 인터페이스만 정의, 비활성

### Phase 2 구조

단일 에이전트를 4개로 분리. 같은 프로세스 내 agents-as-tools 패턴.

- CoordinatorAgent: intent 라우팅, 응답 조립
- PriceAgent: 실시간 상태, 스냅샷, 종목간 비교
- AnalysisAgent: 알림/패턴 해석, 후속 분석
- StrategyAgent: 전략 적합도 랭킹, 시나리오 분석

요청/응답 페이로드는 A2A 형태로 설계해두되, 서비스 분리는 하지 않는다. 트래픽과 소유권 압력이 생길 때 분리한다.

### 가드레일

- **매매 지시 차단**: 매수/매도 요청은 분석으로 리프레이밍
- **증거 기반 응답 강제**: 모든 답변에 참조 종목, 타임스탬프, 사용된 view, 신뢰도 한정자 포함
- **41종목 커버리지 공개**: 실시간 구독 세트에 없는 종목은 반드시 명시
- **write tool 없음**: 현재 범위에서 DB/API write 도구는 존재하지 않음

### 세션 관리

Strands session management를 PostgreSQL에 저장.

저장하는 것: session id, user id, 최근 논의 종목, 시간 프레임, 이전 답변 요약, tool 메타데이터

저장하지 않는 것: vector DB, semantic memory, 대용량 market payload. 메모리는 follow-up 질문 지원용이지 두 번째 데이터 소스가 아니다.

## 예상 파일 구조 (Python + Strands 기준)

```
src/agent/
├── __init__.py
├── config.py                       # DB URL, model provider, 상수
├── models.py                       # 응답 Pydantic (evidence, freshness)
├── guardrails.py                   # 매매지시 차단, 정책 hook
│
├── tools/
│   ├── __init__.py
│   ├── market_data.py              # @tool 6개: snapshot, intraday, compare, ...
│   └── strategy.py                 # @tool 3개: score, rank, followthrough
│
├── agents/
│   ├── __init__.py
│   ├── analyst.py                  # Phase 1: MarketAnalystAgent (단일)
│   ├── coordinator.py              # Phase 2: intent 라우팅
│   ├── price_agent.py              # Phase 2: PriceAgent
│   ├── analysis_agent.py           # Phase 2: AnalysisAgent
│   └── strategy_agent.py           # Phase 2: StrategyAgent
│
├── api/
│   ├── __init__.py
│   └── routes.py                   # FastAPI /agent/chat/stream (SSE)
│
└── prompts/
    ├── analyst.txt                 # Phase 1 system prompt
    ├── coordinator.txt             # Phase 2 system prompts
    ├── price.txt
    ├── analysis.txt
    └── strategy.txt
```

### 예상 코드량

| Phase | 파일 수 | Python 코드 | 프롬프트 |
| --- | --- | --- | --- |
| Phase 1 (단일 에이전트) | ~8개 | ~440줄 | ~30줄 |
| Phase 2 추가분 (멀티) | +5개 | +120줄 | +80줄 |
| 전체 | ~13개 | ~560줄 | ~110줄 |

코드의 절반은 tool 함수(DB 쿼리 래핑)다. 에이전트 자체는 한 파일에 30~40줄이며, 오케스트레이션은 Strands SDK가 처리하므로 직접 짜는 코드는 거의 없다.

### 각 파일의 역할

| 파일 | 설명 | 의존하는 Strands 개념 |
| --- | --- | --- |
| tools/market_data.py | serving.* view를 조회하는 @tool 함수 모음 | @tool 데코레이터 |
| tools/strategy.py | 전략 적합도 점수/랭킹 로직 | @tool 데코레이터 |
| agents/analyst.py | Agent(system_prompt, tools) 정의 | Agent 클래스 |
| agents/coordinator.py | specialist agent를 tool로 등록해 라우팅 | agents-as-tools |
| api/routes.py | stream_async로 SSE 응답 | streaming |
| guardrails.py | BeforeToolCallEvent hook으로 정책 강제 | hooks/plugins |
| models.py | structured output용 Pydantic 모델 | structured_output_model |
| config.py | BedrockModel 등 provider 설정 | model providers |
| prompts/*.txt | 에이전트별 system prompt (코드와 분리) | system_prompt 파라미터 |

## 선행 지식 및 학습 자료

에이전트 개발이 처음인 경우, 아래 순서로 학습한다. 각 항목은 선행 항목에 의존한다.

### 1단계: Strands 기초 (1~2일)

Agent가 tool을 호출하는 기본 루프를 이해한다.

| 주제 | 자료 | 읽는 이유 |
| --- | --- | --- |
| Agent loop 작동 원리 | https://strandsagents.com/latest/user-guide/concepts/agents/agent-loop/ | LLM이 tool을 선택하고 반복 호출하는 핵심 메커니즘 |
| Python Quickstart | https://strandsagents.com/latest/user-guide/quickstart/python/ | 첫 agent를 30분 내 동작시키기 |
| @tool 커스텀 도구 | https://strandsagents.com/latest/user-guide/concepts/tools/custom-tools/ | DB 쿼리를 tool로 감싸는 패턴 |

### 2단계: MCP + 스트리밍 (1~2일)

tool을 MCP 서버로 분리하고, 응답을 스트리밍하는 방법.

| 주제 | 자료 | 읽는 이유 |
| --- | --- | --- |
| MCP 도구 연결 | https://strandsagents.com/latest/user-guide/concepts/tools/mcp-tools/ | Market Data MCP, Strategy MCP 구현에 필요 |
| 스트리밍 응답 | https://strandsagents.com/latest/user-guide/concepts/streaming/ | FastAPI SSE 엔드포인트 구현에 필요 |
| Structured output | https://strandsagents.com/latest/user-guide/concepts/agents/structured-output/ | evidence/freshness를 포함한 응답 포맷 강제 |

### 3단계: 멀티에이전트 + 가드레일 (Phase 2 진입 시)

단일 에이전트가 안정된 후 학습해도 된다.

| 주제 | 자료 | 읽는 이유 |
| --- | --- | --- |
| Agents as tools | https://strandsagents.com/latest/user-guide/concepts/multi-agent/agents-as-tools/ | Coordinator → Specialist 패턴 |
| Hooks / Plugins | https://strandsagents.com/latest/user-guide/concepts/agents/hooks/ | 가드레일 (매매지시 차단, 정책 필터) |
| 세션 관리 | https://strandsagents.com/latest/user-guide/concepts/sessions/ | follow-up 질문을 위한 대화 컨텍스트 유지 |

### 참고: 금융 도메인 샘플

| 자료 | 설명 |
| --- | --- |
| https://github.com/strands-agents/samples — personal-finance-assistant | 멀티에이전트 금융 분석 (stock data agent + portfolio orchestration) |
| https://github.com/strands-agents/samples — finance-assistant-swarm-agent | equity research 자동 생성 (ticker → 분석 → 리포트) |

### 읽지 않아도 되는 것

- LangGraph, CrewAI, AutoGen 문서 — 이 프로젝트에서는 쓰지 않음
- Strands bidirectional streaming — 실험적 기능, 현재 범위 밖
- Strands agent-builder — 우리는 직접 에이전트를 정의함

## 구현 계획

### Phase 0 — Spike (1~2주)

**증명할 것 4가지, 그 외에는 하지 않는다.**

1. Strands가 PostgreSQL 기반 MCP tool을 깔끔하게 호출하는가
2. Agent가 serving.symbol_snapshot에서 읽어 환각 없이 답변하는가
3. FastAPI 스트리밍이 UI 사용에 문제 없는가
4. 가드레일이 매매 지시/조언 스타일 답변을 확실히 차단하는가

### Phase 1 — MVP (2~3주)

단일 MarketAnalystAgent가 6개 유스케이스를 모두 지원.

- Market Data MCP + Strategy MCP 활성
- 세션 저장
- Structured output (evidence, freshness, limitations)
- 가드레일 + 정책 테스트

### Phase 2 — Multi-agent (2~3주)

Phase 1에서 실제 사용 패턴과 실패 사례가 쌓인 후 분리.

- Coordinator + 3 specialist
- agents-as-tools (같은 프로세스)
- A2A 형태 계약 (미래 분리 대비)
- 멀티 에이전트 평가 스위트

### Phase 3 — Production hardening (2~3주)

- OpenTelemetry 트레이싱
- CI 회귀 eval + 정책 검사
- Rate limit, fallback, graceful degradation
- Audit logging, 프롬프트/도구 버전 관리

## 이슈 브레이크다운

### Phase 0

> **이슈 0-1: Agent용 PostgreSQL read view 생성**
>
> 배경: Agent는 bronze/silver/gold 테이블을 직접 조회하지 않는다. 전용 serving view를 통해서만 데이터에 접근한다.
>
> 할 일:
> 1. symbol_intraday_1m, symbol_signal_timeline, symbol_compare_metrics view 생성
> 2. read-only DB role 설정
> 3. 각 view의 컬럼 계약을 문서화
>
> 완료 기준:
> - [ ] staging에 view가 존재함
> - [ ] read-only role로만 조회 가능
> - [ ] 추적 중인 종목에 대해 샘플 쿼리가 기대 결과 반환

---

> **이슈 0-2: Market Data MCP 서버 구현**
>
> 배경: Agent가 호출하는 첫 번째 도구 세트. 위에서 만든 read view를 기반으로 한다.
>
> 할 일:
> 1. resolve_symbol, get_symbol_snapshot, get_intraday_bars, get_signal_timeline, compare_symbols, get_realtime_coverage 구현
> 2. 모든 쿼리는 parameterized query
> 3. generic SQL 실행 경로는 존재하지 않아야 함
>
> 완료 기준:
> - [ ] 6개 tool 구현 완료
> - [ ] SQL injection 불가능한 구조
> - [ ] 성공/빈 결과 케이스에 대한 단위 테스트

---

> **이슈 0-3: Strands POC agent + FastAPI streaming 엔드포인트**
>
> 배경: 우리 파이프라인은 실시간 주가를 serving.symbol_snapshot에 저장한다. Agent가 이 뷰를 tool로 읽어서 사용자 질문에 답하는 게 기본 패턴이다.
>
> 할 일:
> 1. strands-agents 설치 및 기본 agent 구성
> 2. Market Data MCP tool을 연결해 DB 데이터 기반 답변 확인
> 3. POST /agent/chat/stream SSE 엔드포인트 추가
> 4. 최소 10개 질문에 대해 tool 호출 → 데이터 기반 답변 동작 확인
>
> 완료 기준:
> - [ ] Agent가 tool을 자율 호출함 (호출 로그 확인)
> - [ ] 응답에 실제 가격/등락률이 포함됨 (환각 아님)
> - [ ] 스트리밍 응답이 SSE로 정상 전달됨
> - [ ] 요청/응답 로그에 session id와 tool call trace 포함

---

> **이슈 0-4: POC 평가셋 + Go/No-Go 판단**
>
> 배경: Strands 채택 여부를 실제 데이터 기반으로 판단해야 한다.
>
> 할 일:
> 1. 6개 유스케이스를 커버하는 20개 grounded prompt 작성
> 2. 합격 기준 정의: 데이터 근거, 신선도 공개, 정책 준수
> 3. 라이브 유사 데이터 스냅샷으로 결과 수집
> 4. Go/No-Go 메모 작성 (Strands 채택 근거 + 발견된 갭)
>
> 완료 기준:
> - [ ] 20개 프롬프트 + 합격/불합격 루브릭 존재
> - [ ] 최소 1개 데이터 스냅샷으로 결과 기록됨
> - [ ] Go/No-Go 문서가 갭을 명시적으로 나열함

### Phase 1

> **이슈 1-1: MarketAnalystAgent 프로덕션 빌드**
>
> 배경: Phase 0에서 검증된 POC를 6개 유스케이스를 모두 지원하는 프로덕션 에이전트로 확장한다.
>
> 할 일:
> 1. 6개 UC 전체에 대한 system prompt + tool 세트 구성
> 2. Structured output 적용 (evidence, freshness, limitations 포함)
> 3. 승인된 MCP tool만 사용하는지 확인
>
> 완료 기준:
> - [ ] explain, scan, compare, alert review, strategy-fit, coverage 질의 지원
> - [ ] 모든 응답에 evidence + freshness 포함
> - [ ] 각 intent별 integration test 통과

---

> **이슈 1-2: Strategy MCP 구현**
>
> 배경: 전략 적합도 점수와 후보 랭킹은 LLM 자유 해석이 아니라 결정론적 로직이어야 한다.
>
> 할 일:
> 1. score_strategy_fit, rank_strategy_candidates, summarize_signal_followthrough 구현
> 2. 입출력 Pydantic 검증
> 3. fixture 데이터 기반 안정 테스트
>
> 완료 기준:
> - [ ] 전략 스코어가 view 필드에 직접 연결되어 설명 가능
> - [ ] 같은 입력에 같은 랭킹 결과 보장

---

> **이슈 1-3: Agent 세션 PostgreSQL 저장**
>
> 배경: follow-up 질문("아까 그 종목 말고 다른 건?")을 지원하려면 대화 컨텍스트를 유지해야 한다.
>
> 할 일:
> 1. 세션 메타데이터 영속 (종목, 시간 프레임, 이전 답변 요약)
> 2. raw market payload는 저장하지 않음
> 3. 세션 조회 엔드포인트
>
> 완료 기준:
> - [ ] 요청 간 세션 컨텍스트 유지됨
> - [ ] 저장된 데이터에 대용량 market payload 없음

---

> **이슈 1-4: 금융 도메인 가드레일 + 정책 테스트**
>
> 배경: 금융 에이전트는 매매 지시를 하면 안 되고, 데이터 근거 없는 조언도 하면 안 된다.
>
> 할 일:
> 1. "지금 삼성전자 사야 돼?" 같은 요청을 분석으로 리프레이밍하는 로직
> 2. 모든 응답에 신선도 + 커버리지 공개 강제
> 3. write tool이 존재하지 않음을 구조적으로 보장
> 4. 15개 이상 금지/위험 프롬프트에 대한 정책 테스트
>
> 완료 기준:
> - [ ] 매매 지시 요청이 차단 또는 리프레이밍됨
> - [ ] 정책 테스트 15개 이상 통과

### Phase 2

> **이슈 2-1: Coordinator + 3 specialist agent 구현**
>
> 할 일:
> 1. CoordinatorAgent intent 라우팅 구현
> 2. PriceAgent, AnalysisAgent, StrategyAgent 각각 bounded prompt + tool allowlist
> 3. 혼합 질의 5개 이상 통합 테스트
>
> 완료 기준:
> - [ ] 단일 intent → 올바른 specialist로 라우팅됨
> - [ ] 혼합 intent → specialist 2개 이하 호출 후 병합

---

> **이슈 2-2: agents-as-tools + A2A 형태 계약**
>
> 할 일:
> 1. Coordinator가 specialist를 typed contract으로 호출
> 2. contract에 session_id, intent, symbols, timeframe, evidence 포함
> 3. 이 phase에서 네트워크 홉은 없음
>
> 완료 기준:
> - [ ] 계약 문서가 게시됨
> - [ ] 미래 A2A 분리 시 계약 변경 불필요

---

> **이슈 2-3: 멀티 에이전트 라우팅 예산 + 실패 처리**
>
> 할 일:
> 1. max specialist hop + tool-call 예산 강제
> 2. specialist 1개 실패 시 partial answer 반환
> 3. trace에 라우팅 결정 기록
>
> 완료 기준:
> - [ ] 무한 루프 불가능한 구조
> - [ ] 실패 시 사용자에게 안전한 응답 반환

---

> **이슈 2-4: 멀티 에이전트 평가 스위트**
>
> 할 일:
> 1. 25+ 프롬프트 추가
> 2. single-agent vs multi-agent 답변 품질 비교
> 3. 멀티 에이전트 롤아웃 추천을 증거 기반으로 작성
>
> 완료 기준:
> - [ ] 품질 향상이 증명된 intent 카테고리 식별됨
> - [ ] 실패 범주 문서화

### Phase 3

> **이슈 3-1: OpenTelemetry 트레이싱 + 대시보드**
>
> 완료 기준:
> - [ ] FastAPI → agent → tool span 포함 요청 trace
> - [ ] p50/p95 지연, 토큰/비용 메트릭 가시화

---

> **이슈 3-2: CI 회귀 eval + 정책 검사**
>
> 완료 기준:
> - [ ] 정의된 회귀 임계치 미달 시 릴리스 차단
> - [ ] 프롬프트/도구 버전이 실행별로 고정

---

> **이슈 3-3: Rate limit, fallback, graceful degradation**
>
> 완료 기준:
> - [ ] 유저/요청 rate limit 적용
> - [ ] 모델 타임아웃 시 bounded partial answer 반환

---

> **이슈 3-4: Audit logging + 릴리스 관리**
>
> 완료 기준:
> - [ ] 프롬프트 버전 배포 + 롤백 절차 문서화
> - [ ] 감사 로그에 user_id, session_id, tools used, policy outcome 기록

## 주의사항

- **Schema drift**: agent-facing view를 계약으로 다루고 버전 관리할 것
- **Stale confidence**: 41슬롯에 없는 종목은 항상 응답에 명시할 것
- **Agent overreach**: generic SQL tool, 자유형 조언, write tool 절대 추가하지 말 것
- **Research MCP**: 승인된 데이터 소스가 확보되면 활성화. 그 전에는 인터페이스만 유지

## 에스컬레이션 트리거

- 외부 뉴스/리서치 소스가 승인되면 → Research MCP 활성화
- 지연 또는 소유권 압력이 생기면 → specialist agent를 별도 A2A 서비스로 분리
- 워크플로우가 깊은 상태 관리/재시도/분기를 필요로 하면 → LangGraph 재평가

## Related Notes

- [[11-design-freeze-discussion-pack]]
- [[12-kis-realtime-ingress-design]]
- [[event-driven-stock-pipeline]]
