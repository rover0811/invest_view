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

## 10-14 리플레이 실행

2026-10-21 02:50 KST부터 04:06 KST까지 로컬 kind 클러스터(`kind-invest-flink`)에서 000660의 2026-10-14 하루치 리플레이를 실행하고 프론트엔드용 export를 만들었습니다. 브랜치는 `feat/ai-signal-replay`이며 코드는 수정하지 않았습니다.

### 실행 단계

1. 현재 작업 트리로 `ai-signal:e2e` amd64 이미지를 다시 빌드했습니다.
2. `/tmp/e2e_hosts`에 적힌 broker pod IP(10.244.0.14)와 Kafka, Schema Registry, Postgres의 ClusterIP가 현재 값과 일치하는 것을 확인했습니다. 파일은 수정하지 않았습니다.
3. Flink `price-move-state`가 RUNNING, STABLE 상태이고 JobManager와 TaskManager pod가 모두 Ready인 것을 확인했습니다.
4. `ai-signal-document_processor`, `ai-signal-move_sink`, `ai-signal-signal_worker`, `ai-signal-serving_sink` 그룹의 offset을 news-raw, price-moves, signal-requests, signal-updates 토픽에서 latest로 초기화했습니다.
5. outbox_relay, document_processor, move_sink, signal_worker, serving_sink 러너를 각각 별도 컨테이너로 띄웠습니다. LLM은 Gemini이며 primary는 gemini-3.5-flash, secondary는 gemini-3.5-flash-lite입니다.
6. driver 컨테이너에서 `python -m ai_signal.replay run --max-llm-calls 400`을 실행했습니다. 570개 이벤트 중 563번째에서 호출 상한에 걸려 멈췄고, 상한을 450으로 올려 다시 실행하자 상태 파일에서 이어서 570번째 이벤트까지 끝났습니다.
7. `python -m ai_signal.replay export`로 `frontend/public/replay/000660-2026-10-14.json`을 생성했습니다.
8. 리플레이 컨테이너는 모두 정리했고, alert_service(8000), vite(5173), port-forward(15432)는 그대로 두었습니다.

### 결과

| 항목 | 값 |
| --- | --- |
| 처리한 이벤트 | 570건 (tick과 news) |
| LLM 호출 | 429회 |
| 분봉 bars | 381개 |
| 상태 구간 states | 3개 |
| 문서 documents | 186건 (judged 180건, 미판정 6건) |
| 시그널 signals | 58건 (SHOWN 42건, HIDDEN 16건) |
| SHOWN 시그널의 결정 모델 | gemini-3.5-flash-lite 41건, gemini-3.5-flash 1건 |
| `check_no_lookahead` | 통과 |
| export 파일 크기 | 약 289KB |

### 발견한 문제

1. 이미지의 기본 entrypoint(`docker-entrypoint.sh`)는 alembic 마이그레이션 뒤에 인자와 관계없이 `python -m ai_signal all`을 실행합니다. 처음에는 러너 이름을 인자로 넘겼지만 모든 러너가 함께 떴고, 그중 market_data가 KIS 자격 증명이 없어 컨테이너가 종료되었습니다. 러너별로 실행하려면 `--entrypoint python`을 지정하고 `-m ai_signal <runner>`를 넘겨야 합니다.
2. Gemini가 429 Too Many Requests를 반환한 로그가 document_processor에서 170줄, signal_worker에서 57줄 기록되었습니다. client 재시도로 모두 처리되었고 러너에서 ERROR 로그는 나오지 않았으므로 동시성 설정은 바꾸지 않았습니다.
3. 하루치 리플레이에는 LLM 호출이 400회를 넘게 필요했습니다. 같은 규모의 날짜를 처리할 때는 `--max-llm-calls`를 450 이상으로 주는 편이 좋습니다.
4. 미판정 문서 6건에는 `fail_reason`이 비어 있습니다. 판정이 끝나기 전에 export가 집계했는지, 다른 원인인지는 확인하지 않았습니다.

## 10-14 리플레이 재실행 (기사 성격 반영)

2026-10-21 17:20 KST부터 18:05 KST까지 로컬 kind 클러스터(`kind-invest-flink`)에서 같은 날짜의 리플레이를 다시 실행했습니다. 브랜치는 `feat/ai-signal-article-character`이며, 판정 프롬프트 v4(기사 성격 `info_type` 포함), 기사 성격에 따른 재생성 조건, 새 LLM 라우팅이 적용된 코드를 사용했고 코드는 수정하지 않았습니다.

### 실행 단계

1. 현재 작업 트리로 `ai-signal:e2e` 이미지를 다시 빌드하고 `alembic upgrade head`로 0006(`judgement_character`)까지 반영했습니다. alembic은 `/app/services/ai_signal`을 작업 디렉터리로 지정해야 실행됩니다.
2. 000660의 serving 시그널과 이력, 10-14의 gold move와 시그널, 10-13 15:30부터 10-14 20:00까지 발행된 문서 189건과 그 판정, 엔티티, revision, KIS 뉴스, bronze 원문을 FK 순서대로 삭제했습니다. 이전 상태 파일은 `.bak`으로 옮겼습니다.
3. Flink `price-move-state`의 FlinkDeployment와 checkpoint PVC를 삭제한 뒤 다시 만들었고, RUNNING과 STABLE 상태를 확인했습니다.
4. 네 개 consumer group의 offset을 latest로 초기화하고 러너 다섯 개를 띄웠습니다. 판정 라우트는 gemini-3.5-flash-lite, gemini-3-flash-preview, OpenRouter nemotron 순서이고, 생성 라우트는 gemini-3.5-flash, gemini-3.5-flash-lite, OpenRouter nemotron 순서입니다.
5. driver를 `--max-llm-calls 450`으로 실행했고, 상한에 걸리지 않고 570개 이벤트를 모두 처리했습니다. 이어서 export를 생성했습니다.

### 결과

| 항목 | 이전 실행 | 재실행 |
| --- | --- | --- |
| LLM 호출 | 429회 | 193회 |
| 문서 documents | 186건 (judged 180건) | 189건 (모두 judged) |
| 상태 구간 states | 3개 | 3개 |
| 시그널 signals | 58건 | 6건 (SHOWN 5건, HIDDEN 1건) |
| 시그널 trigger | 확인하지 않음 | PRICE_STATE_CHANGE 3건, NEW_DOCUMENT 3건 |
| `check_no_lookahead` | 통과 | 통과 |
| export 파일 크기 | 약 289KB | 약 220KB |

판정 문서의 기사 성격은 PRICE_RECAP 75건, OPINION 44건, NEW_FACT 31건, OTHER 21건, FLOW 18건입니다. 판정은 188건이 gemini-3.5-flash-lite에서 끝났고, 1건은 primary가 TIMEOUT으로 실패해 gemini-3-flash-preview로 넘어갔습니다. 시그널 생성 5건은 모두 gemini-3.5-flash에서 끝났으며 OpenRouter까지 넘어간 호출은 없었습니다. 시세 정리 기사가 새 시그널을 만들지 않게 되면서 시그널 버전이 58건에서 6건으로 줄었습니다.

### 발견한 문제

1. zsh에서는 문자열 변수에 담은 `docker run` 명령이 단어 단위로 나뉘지 않아 러너 실행이 실패했습니다. 실행 스크립트를 bash 배열로 바꿔 해결했습니다.
2. 이전 기록의 미판정 문서 6건 문제는 이번 실행에서 재현되지 않았습니다.
