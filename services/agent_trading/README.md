# agent-trading

LLM agent (Track B+C 경계). 사용자의 종목 분석 요청을 받으면 alert-serving / research MCP를 호출해 컨텍스트를 모은 뒤 응답한다.

## 현재 PR 스코프

| 산출물 | 상태 |
| --- | --- |
| `AlertClient` (alert-serving HTTP read client) | 본 PR |
| `AgentContextBuilder` (alerts + patterns 묶어서 prompt context로 정리) | 본 PR |
| LLM 호출 (실제 모델 연결) | 후속 PR |
| MCP research tool 어댑터 | 후속 PR |

## 디자인 근거

- `1. Projects/두드림/05-sequence-agent-trade.md`
- `1. Projects/두드림/06-sequence-user-analysis.md`
- `1. Projects/두드림/10-c4-component-agent-trading.md`
