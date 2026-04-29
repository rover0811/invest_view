# invest_view docs

이 폴더는 현재 `invest_view`의 source-of-truth 설계 문서를 모아둔 공간입니다.

## 읽는 순서

1. [설계 확정 문서](design/11-design-freeze-discussion-pack.md)
   - v1 범위, 요구사항, ERD, 로드맵, 다이어그램을 한 번에 보는 기준 문서

2. [KIS 실시간 수집 설계](design/12-kis-realtime-ingress-design.md)
   - KIS 인증, approval key, subscription pool, WebSocket contract, normalized tick 모델

3. [이벤트 기반 파이프라인 설계 근거](design/event-driven-stock-pipeline.md)
   - 왜 Kafka, Flink, PostgreSQL, custom consumer 구조를 택했는지 설명하는 설계 근거 문서

## 다이어그램

- [D2 소스](diagrams/d2-sources/)
- [렌더된 SVG](diagrams/rendered/)

## 메모

- 이 폴더에는 BigQuery, Elasticsearch, paper trading 등 과거 범위를 포함한 historical 초안은 넣지 않았습니다.
- 구현은 설계 확정 문서를 먼저 보고, KIS 세부 사항은 KIS 설계 문서를 우선 기준으로 보면 됩니다.
