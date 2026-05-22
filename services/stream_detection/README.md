# stream-detection

PyFlink job that consumes `stock-ticks` and emits derived events:

- 5분 슬라이딩 윈도우 집계 (Phase 2.1)
- 급등/급락 alert 발행 → `stock-alerts` (Phase 2.2, PR 후속)
- 골든/데드크로스 등 패턴 발행 → `stock-patterns` (Phase 2.3, PR 후속)

## Scope

| 산출물 | 상태 |
| --- | --- |
| `stock-ticks` consumer (Kafka source) | skeleton |
| 5분 sliding window 집계 (price, return, volume) | skeleton |
| Avro deserializer 연동 | skeleton |
| alert/pattern emitter | 후속 PR |

## 로컬 실행 (예정)

```bash
uv sync
uv run python -m stream_detection.job --config config.yaml
```

## 디자인 참고

- `1. Projects/두드림/event-driven-stock-pipeline.md`
- `1. Projects/두드림/11-design-freeze-discussion-pack.md` §3 토픽/스키마, §6 역할분담 Track B
- `1. Projects/두드림/12-kis-realtime-ingress-design.md` raw contract
