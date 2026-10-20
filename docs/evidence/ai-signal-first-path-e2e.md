# AI signal first path E2E 검증 (kind)

- 실행 일시: 2026-10-20 02:10-02:20 KST (kind context `kind-invest-flink`, branch `feat/ai-signal-first-path`, 미커밋 코드)
- 결과: 뉴스 수집부터 REST 조회까지 모든 단계에 실제 데이터가 흘렀고, 000660 시그널이 `SHOWN` 상태로 조회되었습니다.
- LLM 호출 수: 총 4회입니다 (문서 판단 3회, 시그널 생성 1회, 모두 OpenRouter `nvidia/nemotron-3-super-120b-a12b:free`).

## 실행 환경

- Python 컴포넌트는 amd64 이미지로 빌드한 뒤 `docker run --platform linux/amd64 --network container:invest-flink-control-plane`으로 kind 노드의 네트워크 네임스페이스에 붙여 실행했습니다. Kafka가 광고하는 클러스터 내부 호스트명은 bind mount한 hosts 파일로 ClusterIP와 broker pod IP에 매핑했습니다.
- 이미지: `ai-signal:e2e`, `kis_ingestion:e2e` (현재 작업 트리 기준), `stream-detection-java:pricemove2` (NXT 필터 변경 반영, `kind load` 후 FlinkDeployment `price-move-state`의 image를 patch).

## 단계별 결과

| 단계 | 명령 또는 방법 | 결과 |
| --- | --- | --- |
| a. 마이그레이션, 시장 데이터 | 컨테이너에서 `alembic upgrade head` | `0001`에서 `0003_serving_signal_history`까지 적용했습니다. `gold.symbol_beta` 최신 as_of 2026-10-19 (40종목), `reference.index_value` 최신 2026-10-19라서 market_data 재실행은 생략했습니다. |
| b. 뉴스 poller | `build_pollers(...)`로 만든 news poller의 `run_once`를 000660 고정 SymbolSource로 1회 호출 (장 시간 검사 우회) | KIS 뉴스 제목 118건을 `news-raw`에 발행했습니다 (EGW00201 rate limit 재시도는 정상 처리). |
| c. document_processor | LLM 호출 수를 제한하려고 consumer group을 latest로 맞춘 뒤, 발행된 실제 레코드 3건(p0 offset 41, 16, 20)만 원본 바이트 그대로 재발행 | `bronze.news_title_raw` 3, `silver.document` 3, `silver.document_judgement` 3 (모두 direction 1, SYMBOL, 0.90에서 0.95), NEW_DOCUMENT 요청 3건은 move가 없어서 signal_worker가 건너뛰었습니다. |
| d. PriceMoveStateJob | `stock-ticks`에 KRX Avro tick 2건 주입 (business_date 20261020, 09:30 1,294,000원, 10:00 1,317,000원, 전일 종가 1,291,000원) | `price-moves`에 DAILY move 1건 (move_id `82c34cdf-66d5-5f61-8c1c-f6b4b8a725a3`, +2.014%, version 1) |
| e. move_sink, signal_worker, serving_sink | 컨테이너 5개(outbox_relay, document_processor, move_sink, signal_worker, serving_sink) | `gold.move` 1, `gold.signal` 1 (SHOWN, probability 0.88, `signal_generation/v1`), `gold.signal_cause` 2, `serving.symbol_signal` 1, `serving.symbol_signal_cause` 2 |
| f. alert_service REST | `kubectl port-forward svc/postgres 15432:5432` 후 `uv run python _dev_serve.py` | 두 API 모두 HTTP 200 |

move 날짜는 2026-10-20으로 정했습니다. 뉴스 후보 구간(2026-10-19 15:30에서 2026-10-20 20:00 KST)에 사용한 뉴스 3건(10-19 15:33, 16:54, 17:20 게시)이 모두 포함되기 때문입니다.

## 최종 REST 응답

`GET /api/stocks/000660/signal`

```json
{"symbol":"000660","move_id":"82c34cdf-66d5-5f61-8c1c-f6b4b8a725a3","move_version":1,"version":1,
 "move_window_end":"2026-10-20T01:00:00Z","status":"SHOWN","headline_label":"영업익 기록 경신",
 "generation_method":"llm","updated_at":"2026-10-19T17:17:47.771000Z",
 "causes":[
  {"rank":1,"explanation":"SK하이닉스의 영업익이 역대 6번째 신기록을 경신하면서 실적 개선 기대감이 높아졌고, 이에 따라 주가가 상승했어요.","source_count":1},
  {"rank":2,"explanation":"SK하이닉스가 소부장 기업에 2조원 규모 금융 지원을 발표하면서 상생 협력 강화 기대감이 커졌고, 이에 따라 주가가 상승했어요.","source_count":2}]}
```

`GET /api/stocks/000660/signal/history?date=2026-10-20`는 위와 같은 항목 1건을 담은 배열을 반환했습니다.

## 발견한 문제

1. `services/ai_signal/Dockerfile`의 PATH가 `/app/services/ai_signal/.venv/bin`을 가리켰지만, workspace에서 `uv sync --package`를 실행하면 venv는 `/app/.venv`에 만들어집니다. 그래서 컨테이너의 `python`에서 `confluent_kafka`를 import하지 못했습니다. PATH를 `/app/.venv/bin`으로 고쳤고, 재빌드한 이미지로 이번 실행 전체를 수행했습니다 (`tests/test_main.py`, `tests/test_config.py` 8 passed).
2. 배포되어 있던 `stream-detection-java:pricemove1` 이미지는 `PriceMoveStateFunction.java`의 NXT 필터 변경보다 먼저 빌드된 이미지였습니다. `pricemove2`로 재빌드해서 반영했고, `k8s/flinkdeployment-price-move.yaml`의 image 태그도 `pricemove2`로 고쳤습니다.
3. `gold.move`의 분해 컬럼(contrib_*, beta_as_of, dominant_layer)이 비어 있습니다. 분해는 move 당일(2026-10-20)의 지수와 종가 데이터가 있어야 계산되는데, 장 시작 전에 주입한 합성 move라서 당일 데이터가 없습니다. 코드 결함이 아니라 데이터 조건 때문입니다.
4. `news-raw`에는 이전 작업에서 남은 레코드 41건이 현재 Schema Registry에 없는 schema id 4로 직렬화되어 있습니다. consumer frame이 디코딩 실패로 건너뛰므로 동작에는 지장이 없습니다.
5. 스키마 레지스트리 pod 안에서 `kafka-avro-console-consumer`를 실행하면 메모리 부족으로 종료되었습니다(exit 137). 그래서 토픽 확인은 amd64 Python 컨테이너로 대신했습니다.

## 2차 실행 (계약 변경 후)

- 실행 일시: 2026-10-21 01:15-01:21 KST (같은 kind context, 같은 branch, 미커밋 코드)
- 변경 사항: migration 0004(serving의 symbol_name, cause_source 테이블, `gold.signal`과 `silver.document_judgement`의 `llm_attempts` JSONB), `signal-updates` 스키마의 symbol_name과 원인별 source_document_ids, 시그널 생성 프롬프트 기본값 v2와 문구 검증기, 보조 모델 `nvidia/nemotron-3.5-super-vl:free`, alert_service REST의 조립된 `headline`, `symbol_name`, `source_document_ids`를 반영한 상태에서 검증했습니다.
- 결과: 기존 000660 DAILY move(`82c34cdf-66d5-5f61-8c1c-f6b4b8a725a3`)에 대해 시그널 version 2가 `SHOWN` 상태로 생성되었고, 새 필드가 REST 응답까지 전달되었습니다.
- LLM 호출 수: 총 2회입니다. primary 모델 `nvidia/nemotron-3-super-120b-a12b:free`는 이번에도 OpenRouter에서 HTTP 404를 반환했고, fallback이 secondary 단계로 넘어가서 `nvidia/nemotron-3.5-super-vl:free`가 HTTP 200으로 결과를 결정했습니다. 실행 전후로 확인한 OpenRouter 키의 `limit_remaining`은 100이었습니다.

### 단계별 결과

| 단계 | 명령 또는 방법 | 결과 |
| --- | --- | --- |
| a. 이미지, 마이그레이션 | 현재 작업 트리로 `ai-signal:e2e`(amd64)를 재빌드한 뒤 컨테이너에서 `alembic upgrade head` | `0003_serving_signal_history`에서 `0004_serving_headline_sources`로 올라갔습니다. |
| b. 컨테이너 기동 | outbox_relay, signal_worker, serving_sink 3개 (document_processor와 move_sink는 새 문서와 새 move가 필요 없어서 띄우지 않았습니다) | 기동 직후 signal-requests에 밀린 메시지가 없어서 처리 이력이 없었습니다. |
| c. 재생성 요청 | 컨테이너 안에서 `AvroValueSerializer`(등록된 schema id 8 사용)로 `signal-requests`에 `{symbol: "000660", reason: NEW_DOCUMENT}` 1건을 발행 | prompt_version이 바뀌어 input_hash가 달라졌으므로(`59cc317b465e`에서 `339005ae7cfc`로 변경) 새 버전이 생성되었습니다. |
| d. signal_worker | 위 요청 처리 | `gold.signal` version 2: status `SHOWN`, decided_by `llm`, model `nvidia/nemotron-3.5-super-vl:free`, `signal_generation/v2`, probability 0.75, move_version 1. `llm_attempts`에는 2건이 기록되었습니다 (primary: error `HTTP`, raw_content 없음 / secondary: error 없음, raw_content 419자). |
| e. 원인과 출처 | DB 조회 | `gold.signal_cause` 2건, `gold.signal_cause_source`는 version 2 기준 3건(rank 1에 문서 1건, rank 2에 문서 2건)입니다. version 1 행 3건도 함께 남아 있습니다. |
| f. outbox_relay, serving_sink | 로그와 DB 조회 | outbox relay가 이벤트 1건을 보냈고, serving sink가 `move_version=1 version=2 applied=True`로 반영했습니다. `serving.symbol_signal`은 symbol_name `SK하이닉스`, move_version 1, version 2로 갱신되었고, `serving.symbol_signal_cause_source` 3건, `serving.symbol_signal_history_cause_source` 3건(version 2)이 들어갔습니다. |
| g. alert_service REST | 기존 `_dev_serve.py` 프로세스를 종료하고 같은 환경 변수로 새 코드를 다시 띄움 (`/tmp/alert-dev.log`) | 두 API 모두 HTTP 200을 반환했습니다. |

### 최종 REST 응답

`GET /api/stocks/000660/signal`

```json
{"symbol":"000660","symbol_name":"SK하이닉스","move_id":"82c34cdf-66d5-5f61-8c1c-f6b4b8a725a3","move_version":1,"version":2,
 "move_window_end":"2026-10-20T01:00:00Z","status":"SHOWN","headline_label":"영업이익 6번째 신기록",
 "headline":"영업이익 6번째 신기록에도 SK하이닉스 보합","generation_method":"llm","updated_at":"2026-10-20T16:19:15.734000Z",
 "causes":[
  {"rank":1,"explanation":"영업이익이 76조9천억원으로 6번째 신기록을 썼다는 소식에 4분기 실적 기대감이 커지면서 매수세가 몰렸어요.","source_count":1,
   "source_document_ids":["dc248f76-ec69-5d3d-9f1d-82935efa41e1"]},
  {"rank":2,"explanation":"소부장 기업에 2조원 규모 금융 지원을 하겠다는 소식이 전해지면서 상생 노력에 대한 기대감이 커졌어요.","source_count":2,
   "source_document_ids":["9975877c-a40e-5fe6-9e6a-53f4bac023d9","bc02eba0-62fd-57f9-ba3d-a3b0b0a760c7"]}]}
```

`GET /api/stocks/000660/signal/history?date=2026-10-20`는 version 1과 version 2를 담은 배열(2건, 모두 `SHOWN`)을 반환했습니다. 각 항목의 `headline`은 설계 문서 22에 정한 대로 null입니다.

### 발견한 문제

1. 조립된 헤드라인이 "보합"으로 나왔습니다. `serving.symbol_snapshot`의 000660 행이 business_date 20261019 기준이고 등락률이 0.0이기 때문입니다. move 자체는 합성 tick으로 만든 +2.014% 상승이지만 snapshot에는 그 tick이 반영되지 않았습니다. 헤드라인 조립 코드는 snapshot 값대로 정상 동작했으므로 코드 결함이 아니라 데이터 조건 때문입니다.
2. primary 모델은 2026-10-20 실행 이후 계속 provider 404를 반환합니다. 이번 실행에서는 fallback이 의도대로 secondary 모델로 넘어갔지만, primary 모델을 계속 유지할지는 별도로 판단해야 합니다.
3. 이번 실행에서 코드를 수정한 부분은 없습니다.
