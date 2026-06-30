# Agent Eval Harness — 협업자 환경 구축 가이드

이 문서는 [agent 평가 하니스 설계](../design/19-agent-harness-eval-design.md)를 **별도 머신에서 개발/실행하는 협업자**를 위한 환경 셋업 절차다. 두 역할로 나뉜다.

- **[OWNER]** = 레포/홈서버 소유자(`rover0811`)가 1회 수행 (GCP IAM 초대, read-only DB role 발급).
- **[DEV]** = 협업자가 자기 머신(M1 맥북)에서 수행.

## 핵심 모델: "배포"가 아니라 "원격 클라이언트 실행"

eval 하니스는 **k8s에 배포되지 않는다.** 협업자 머신에서 도는 **평범한 로컬 파이썬 프로세스**이며, 두 원격 서비스를 *클라이언트로서* 호출할 뿐이다.

```
협업자 머신 (python 프로세스 1개)
  ├─ build_market_analyst_agent(config)        ← 로컬 함수 (uv sync면 끝)
  ├─ Strands Agent → Gemini 추론   ──HTTPS──▶  GCP Vertex AI (us-central1)
  └─ agent tool → DB 쿼리          ──TCP 5432──▶ homelab Postgres (read-only, Tailnet)
```

FastAPI·Kafka·uvicorn·k8s는 하니스에 **불필요**하다. 따라서 협업자 머신에 "배포"할 것은 없다.

## 두 가지 작업 모드

| 모드 | 필요 조건 | 무엇을 하나 |
|---|---|---|
| **LOCAL (시크릿 0개)** | uv + Docker | 센서 9종 개발, `pytest -m "not qa"` 오프라인 게이트, 리플레이 모드 runner. Wave 1~3의 **대부분**. |
| **REMOTE (시크릿)** | + GCP ADC + homelab DB | 실 agent 실행/녹화(Task 4b), canonical retrieval(4e), 라벨 검증(4d), baseline 산출. Wave 1.5 한정. |

> 센서 코딩은 LOCAL만으로 끝난다. 시크릿은 **실제 baseline을 뽑을 때만** 필요하다.

---

## 0. 한 줄 점검: doctor 스크립트

협업자는 셋업 중 언제든 아래로 현재 상태를 확인한다.

```bash
cd services/alert_service
uv run python ../../scripts/eval_env_doctor.py            # LOCAL 필수 + REMOTE advisory
uv run python ../../scripts/eval_env_doctor.py --strict   # REMOTE까지 필수로 검사
```

LOCAL 체크가 모두 OK면 센서 개발이 가능하고, REMOTE가 SKIP이어도 exit 0이다.

---

## 1. [DEV] LOCAL 환경 (시크릿 불필요)

```bash
# 1) 레포 클론 + 의존성
git clone <repo-url> invest_view && cd invest_view/services/alert_service
uv sync

# 2) Docker Desktop 실행 (testcontainers가 ephemeral Postgres를 띄움 — prod 자격증명 불필요)
#    macOS: Docker Desktop 앱 시작 후
docker info >/dev/null && echo "docker OK"

# 3) 오프라인 게이트 — 시크릿 0개로 통과해야 함
uv run pytest -m "not qa"
```

`pytest -m "not qa"`는 환경변수가 전혀 없어도 수집·실행된다(검증됨: `env -i`에서 178개 수집). 시크릿 의존 테스트(`-m qa`)는 자격증명이 없으면 **자동 skip**된다.

오프라인 작업에 쓰는 기존 패턴(설계 문서가 재사용을 지시):
- `FakeAgent` — `tests/test_agent_stream_api.py` (Gemini 대체)
- `FakeSessionFactory` / `_FakeSession` — `tests/test_repository_agent.py` (DB 대체)
- `testcontainers` `postgres:16-alpine` — `tests/conftest.py` (Docker만 필요)

---

## 2. GCP Vertex AI (Gemini) — 키 전달 없음

이 프로젝트는 **API 키 방식이 아니라 Vertex AI + ADC**다 (`agent/model.py`의 `genai.Client(vertexai=True, ...)`). 따라서 협업자에게 키 파일을 넘기지 않는다. 대신 **OWNER가 IAM으로 초대 + DEV가 본인 계정으로 인증**한다.

| 항목 | 값 |
|---|---|
| GCP 프로젝트 | `stock-agent-491104` |
| 리전 (운영 동일) | `us-central1` ⚠️ (gcloud 기본값 `asia-northeast3` 아님) |
| 모델 | `gemini-2.5-flash` |

### 2-1. [OWNER] 협업자를 Vertex 사용자로 초대 (1회)

```bash
gcloud projects add-iam-policy-binding stock-agent-491104 \
  --member="user:<협업자-구글계정>@gmail.com" \
  --role="roles/aiplatform.user"
```

> `roles/aiplatform.user`면 Vertex 추론 호출에 충분하다. owner/editor를 주지 말 것.

### 2-2. [DEV] 본인 계정으로 ADC 인증

```bash
gcloud auth application-default login
gcloud config set project stock-agent-491104
```

`.env`에는 시크릿이 아니라 식별자만 들어간다 (아래 5절 참조).

---

## 3. homelab read-only DB role — [OWNER] 1회 발급

canonical retrieval(4e)·라벨 검증(4d)이 prod의 `reference.*` / `serving.*`를 **읽어야** 한다. 협업자에게 기존 `invest` 계정을 공유하지 말고, **SELECT 전용 role**을 새로 발급한다.

SQL: [`scripts/sql/eval_readonly_role.sql`](../../scripts/sql/eval_readonly_role.sql) — `db_guard.py` allowlist와 동일한 8개 테이블(reference 4 + serving 4)에만 SELECT 부여. (드라이런으로 8테이블 정확 검증됨.)

> ⚠️ **반드시 superuser `postgres`로 실행한다.** `invest` 계정은 `CREATEROLE`이 없어 실패한다 (`permission denied to create role`).

```bash
# [OWNER] homelab에서 superuser로 적용. <STRONG_PW>를 강한 비밀번호로 교체.
ssh hyunsoo-cluster1 'SP=$(sudo k3s kubectl -n postgres get secret postgres-credentials \
    -o jsonpath="{.data.postgres-password}" | base64 -d); \
  sudo k3s kubectl -n postgres exec -i statefulset/postgresql -- \
    env PGPASSWORD=$SP psql -U postgres -d invest_view \
    -v role_pw="'"'"'<STRONG_PW>'"'"'" -f -' < scripts/sql/eval_readonly_role.sql
```

성공 시 `granted tables: | 8`이 출력된다.

### 3-1. 네트워크: Tailnet 멤버십 (자격증명보다 강한 1차 방어선)

homelab은 Tailscale(`100.89.9.31`) 뒤에 있다. 접속 문자열만으로는 닿지 않으며, **협업자가 Tailnet에 들어와야** 5432에 TCP로 닿는다.

- **[OWNER]** Tailscale admin 콘솔에서 협업자를 tailnet에 초대.
- **[DEV]** Tailscale 설치 후 로그인 → `100.89.9.31` 도달 확인.

> read-only role 비밀번호는 Tailnet 안에서만 의미가 있다. 즉 "접속정보 유출"보다 "Tailnet 멤버십"이 먼저 막는다.

### 3-2. 비밀번호 전달

`<STRONG_PW>`(접속 문자열 1개)만 협업자에게 안전하게 전달한다. `.env` 전체를 넘기지 않는다 — JWT/KIS 등은 하니스에 불필요하다. (1Password/Bitwarden secret share, 또는 일회성 채널 권장.)

---

## 4. [DEV] REMOTE 연결 확인

```bash
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"

export ALERT_SERVICE_DATABASE_URL='postgresql+asyncpg://eval_readonly:<STRONG_PW>@100.89.9.31:5432/invest_view'
export ALERT_SERVICE_GCP_PROJECT=stock-agent-491104
export ALERT_SERVICE_GCP_LOCATION=us-central1

uv run python ../../scripts/eval_env_doctor.py --strict   # 이제 REMOTE까지 모두 OK여야 함
```

---

## 5. [DEV] `.env` (선택 — export 대신 파일로)

`services/alert_service/.env` (gitignore됨). **필요한 최소만** 둔다.

```dotenv
ALERT_SERVICE_DATABASE_URL=postgresql+asyncpg://eval_readonly:<STRONG_PW>@100.89.9.31:5432/invest_view
ALERT_SERVICE_GCP_PROJECT=stock-agent-491104
ALERT_SERVICE_GCP_LOCATION=us-central1
ALERT_SERVICE_GEMINI_MODEL_ID=gemini-2.5-flash
```

> `config.py`는 `env_file=None`이라 `.env`를 자동 로드하지 않는다. 셸 export(direnv/dotenv)로 환경에 주입하거나, 실행 스크립트가 명시적으로 읽게 한다. JWT_SECRET·KAFKA·KIS 키는 **하니스에 불필요**하므로 넣지 않는다.

---

## 6. 함정 모음

| 함정 | 증상 | 해결 |
|---|---|---|
| GCP 리전 불일치 | Gemini 404/모델 없음 | `ALERT_SERVICE_GCP_LOCATION=us-central1` (gcloud 기본 `asia-northeast3` 아님) |
| DB role을 `invest`로 생성 | `permission denied to create role` | superuser `postgres`로 실행 (3절) |
| Tailnet 미가입 | `100.89.9.31:5432` 연결 타임아웃 | Tailscale 초대/로그인 먼저 |
| Docker 미실행 | testcontainers/qa 테스트 실패 | Docker Desktop 실행 |
| 장외 UC-A 빈 데이터 | 실시간 시세 fact 없음 | 장중(평일 09:00~15:30 KST) 실행; `serving.symbol_daily_ohlc` 등은 장중 생성됨 |
| ADC만 하고 IAM 누락 | `403 PERMISSION_DENIED` | OWNER가 `roles/aiplatform.user` 부여 (2-1) |

## 참고

- 설계: [`docs/design/19-agent-harness-eval-design.md`](../design/19-agent-harness-eval-design.md)
- 홈서버 운영 함정: [`CLAUDE.md`](../../CLAUDE.md)
- doctor: [`scripts/eval_env_doctor.py`](../../scripts/eval_env_doctor.py)
- read-only role SQL: [`scripts/sql/eval_readonly_role.sql`](../../scripts/sql/eval_readonly_role.sql)
