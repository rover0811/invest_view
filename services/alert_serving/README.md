# alert-serving

FastAPI 기반 서비스. Track C (Serving / Product).

## 역할

- `stock-alerts` / `stock-patterns` Kafka consumer
- PostgreSQL serving 스키마에 notification 저장 (후속 PR)
- WebSocket 사용자 push (`/ws/alerts/{user_id}`)
- Agent가 호출할 read API (`/alerts`, `/patterns`)

## 현재 PR 스코프

| 산출물 | 상태 |
| --- | --- |
| FastAPI app skeleton (`/health`, `/alerts`, `/patterns`, `/ws/alerts/{user_id}`) | 본 PR |
| Connection manager (in-memory WebSocket fan-out) | 본 PR |
| Agent 측 read client (`agent_trading.alert_client.AlertClient`) | 본 PR |
| Kafka consumer → DB → WebSocket push 연결 | 후속 PR |
| serving schema migration | 후속 PR |

## 디자인 근거

- `1. Projects/두드림/05-sequence-agent-trade.md`
- `1. Projects/두드림/06-sequence-user-analysis.md`
- `1. Projects/두드림/11-design-freeze-discussion-pack.md` §6 Track C
