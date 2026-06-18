# Clean-slate re-ingest runbook (T15 final)

Status: **CONFIRMED RUNBOOK / DOCUMENTATION ONLY**. T15 does not execute prod commands. Actual execution is **[OPERATOR] non-blocking** and should be scheduled by the operator. Prefer weekday KST market hours (09:00-15:30) for live tick proof; outside market hours, zero live KIS ticks is normal and only synthetic smoke validation is possible.

No `docs/ops/` directory exists in this repo, so the finalized runbook remains at `.sisyphus/evidence/task-15-runbook-final.md`.

## What changed and why clean-slate re-ingest is needed

The old tick dedupe identity was derived from transport/offset-like keys, which allowed about 810k duplicate `bronze.tick_history` rows and let stale/out-of-order records overwrite current snapshots. The completed T9-T14 implementation changes the ingestion contract to deterministic `event_id` idempotency and event-time-aware persistence:

- `schemas/stock-ticks.avsc` now includes optional `event_id` (T10). Register it with `make schemas` before producer deploy.
- `services/tick_persistence/alembic/versions/0006_tick_event_time_contract.py` adds the event-time/idempotency DB contract (T11). Run `alembic upgrade head`.
- `bronze.tick_history` dedup is now `event_id`-based (T12).
- `serving.symbol_snapshot` uses conditional upsert so older ticks do not overwrite newer state (T13).
- `kis_ingestion` emits `event_id` and uses exponential reconnect backoff/fail-fast behavior (T9/T14).
- Both `kis_ingestion` and `tick_persistence` images must be rebuilt and pushed with the current commit hash tag.

The clean slate intentionally purges old duplicate-heavy bronze data and all downstream derived state (`silver`, `serving`, `gold`, Kafka topics, Flink state) so the rebuilt surface is consistent under the new `event_id` identity.

## Production guardrails

- Prod is homelab **k3s**, not Docker and not the Mac `kind` context.
- All prod cluster actions go through `ssh hyunsoo-cluster1 'sudo k3s kubectl ...'`.
- Services run in namespace `invest`; Postgres runs in namespace `postgres`.
- DB credentials come from secret `invest/invest-db-credentials` for user `invest`, db `invest_view`; never use `postgres/postgres`.
- Deployment is GitOps via separate repo `~/AnyProjects/homelab-infra`. Standard path: **build+push images -> bump image tags in homelab-infra -> git push -> Flux auto-deploy**.
- Trust Flux auto-deploy. Do **not** use manual `kubectl set image` as the standard deployment path.
- Never force power off the homelab. If shutdown is required: `ssh hyunsoo-cluster1 'sudo shutdown -h now'`.

## State reset scope

| Layer/state | Clean-slate action |
| --- | --- |
| `bronze.tick_history` | Truncate duplicate-heavy old rows; rebuild only with `event_id` identity. |
| `silver.symbol_5m_metrics` | Truncate derived bars so old aggregates cannot survive a bronze wipe. |
| `serving.symbol_snapshot` | Truncate and repopulate through conditional event-time upsert. |
| `gold.pattern_events` | Truncate pattern rows derived from old duplicate/stale inputs. |
| Kafka `stock-ticks` | Delete/recreate topic to establish a clean ingest boundary. |
| Kafka `stock-patterns` | Delete/recreate so old pattern messages cannot refill `gold`. |
| Flink checkpoint/HA state | Delete checkpoint PVC and cold-start detectors; expect warmup for pattern outputs. |

## Final 8-step sequence

### Step 1 — Build and push fixed images from the Mac (amd64, tag = commit hash)

Run on the Mac repo checkout. The homelab has no Docker; prod nodes are amd64, so use buildx cross-build. This step does not mutate the cluster.

```bash
gh auth token | docker login ghcr.io -u rover0811 --password-stdin
TAG=$(git rev-parse --short HEAD)

docker buildx build --builder omo-amd64 --platform linux/amd64 \
  -f services/kis_ingestion/Dockerfile \
  -t ghcr.io/rover0811/kis_ingestion:$TAG --push .

docker buildx build --builder omo-amd64 --platform linux/amd64 \
  -f services/tick_persistence/Dockerfile \
  -t ghcr.io/rover0811/tick_persistence:$TAG --push .
```

Homelab read-only sanity check:

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n invest get deploy kis-ingestion tick-persistence -o wide'
```

Rollback note: no prod state changes yet. If an image is wrong, build and push a new commit-hash tag; do not mutate or retag an existing prod tag.

### Step 2 — Register evolved Avro schema with `make schemas`

Register `schemas/stock-ticks.avsc` with optional `event_id` before deploying producers. Keep the homelab port-forward and Mac tunnel open while running `make schemas`.

Terminal A, homelab k3s port-forward:

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n invest port-forward svc/schema-registry 18081:8081 --address 127.0.0.1'
```

Terminal B, Mac-to-homelab tunnel:

```bash
ssh -N -L 18081:127.0.0.1:18081 hyunsoo-cluster1
```

Terminal C, Mac repo checkout:

```bash
SCHEMA_REGISTRY_URL=http://localhost:18081 make schemas
```

Confirm latest registered subject includes `event_id`:

```bash
curl -s http://localhost:18081/subjects/stock-ticks-value/versions/latest | python -m json.tool
```

Rollback note: Schema Registry versions are append-only. If registration is wrong or incompatible, stop before deploying producers, fix `schemas/stock-ticks.avsc`, and register a later compatible version. Do not disable compatibility as a workaround.

### Step 3 — Bump both image tags in homelab-infra and trust Flux auto-deploy

Run in the separate GitOps repo on the Mac:

```bash
cd ~/AnyProjects/homelab-infra
TAG=<commit-hash-from-step-1>

$EDITOR infrastructure/invest/services/deployments.yaml
git diff -- infrastructure/invest/services/deployments.yaml
git status
git add infrastructure/invest/services/deployments.yaml
git commit -m "invest: deploy event-id price staleness fix $TAG"
git push
```

Required edits in `infrastructure/invest/services/deployments.yaml`:

- `ghcr.io/rover0811/tick_persistence:$TAG` for `tick-persistence` and its `alembic-migrate` initContainer, if present.
- `ghcr.io/rover0811/kis_ingestion:$TAG` for `kis-ingestion`.

Ordering note: the compatibility-safe target is consumers first. If applying manually by separate commits ever matters, deploy `tick_persistence` before `kis_ingestion` so the event_id-aware consumer is ready before the event_id-producing KIS pod starts. Standard path is still one homelab-infra commit plus Flux auto-deploy; the schema is already registered in Step 2.

Trust Flux auto-reconcile (~10 minutes), or request an immediate reconcile:

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n flux-system annotate kustomization invest-services reconcile.fluxcd.io/requestedAt="$(date -Is)" --overwrite; \
  sudo k3s kubectl -n invest rollout status deploy/tick-persistence --timeout=600s; \
  sudo k3s kubectl -n invest rollout status deploy/kis-ingestion --timeout=600s; \
  sudo k3s kubectl -n invest get deploy kis-ingestion tick-persistence -o wide'
```

Rollback note: revert the homelab-infra tag commit and push; Flux will roll back to the previous declared image tags. Do **not** use manual `kubectl set image` as the standard rollback path.

### Step 4 — Run/confirm Alembic migration `0006_tick_event_time_contract`

Run the migration explicitly after the fixed `tick_persistence` image is deployed. Real artifact: `services/tick_persistence/alembic/versions/0006_tick_event_time_contract.py`.

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n invest exec deploy/tick-persistence -c tick-persistence -- \
  sh -lc "cd /app/services/tick_persistence && alembic upgrade head"'
```

Confirm `event_id`/event-time columns and current Alembic head using the approved DB secret pattern:

```bash
ssh hyunsoo-cluster1 'P=$(sudo k3s kubectl -n invest get secret invest-db-credentials -o jsonpath="{.data.POSTGRES_PASSWORD}" | base64 -d); \
  sudo k3s kubectl -n postgres exec statefulset/postgresql -- env PGPASSWORD=$P psql -v ON_ERROR_STOP=1 -U invest -d invest_view \
  -c "SELECT version_num FROM alembic_version;" \
  -c "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns WHERE table_schema IN ('\''bronze'\'','\''serving'\'') AND table_name IN ('\''tick_history'\'','\''symbol_snapshot'\'') AND column_name IN ('\''event_id'\'','\''event_ts'\'','\''last_event_ts'\'','\''persisted_at'\'') ORDER BY table_schema, table_name, column_name;"'
```

Rollback note: before Step 6, rollback is to stop rollout, revert the homelab-infra tag commit, and run the migration downgrade only if the migration's downgrade path has been reviewed. After Step 6, old duplicate data is intentionally destroyed and rollback requires backups.

### Step 5 — Stop services for maintenance

Suspend Flux for the affected kustomizations so operator-controlled scale-down is not immediately reverted mid-wipe:

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n flux-system patch kustomization invest-services --type=merge -p "{\"spec\":{\"suspend\":true}}"; \
  sudo k3s kubectl -n flux-system patch kustomization invest-flink --type=merge -p "{\"spec\":{\"suspend\":true}}"'
```

Stop producer/consumers and suspend Flink:

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n invest scale deploy/kis-ingestion deploy/tick-persistence deploy/event-pattern-persistence --replicas=0; \
  sudo k3s kubectl -n invest patch flinkdeployment stream-detection --type=merge -p "{\"spec\":{\"job\":{\"state\":\"suspended\"}}}"; \
  sudo k3s kubectl -n invest wait --for=delete pod -l app=kis-ingestion --timeout=180s || true; \
  sudo k3s kubectl -n invest wait --for=delete pod -l app=tick-persistence --timeout=180s || true; \
  sudo k3s kubectl -n invest wait --for=delete pod -l app=event-pattern-persistence --timeout=180s || true; \
  sudo k3s kubectl -n invest get pods'
```

Rollback note: if the stop fails before destructive cleanup, resume Flux and let declared workloads return:

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n flux-system patch kustomization invest-services --type=merge -p "{\"spec\":{\"suspend\":false}}"; \
  sudo k3s kubectl -n flux-system patch kustomization invest-flink --type=merge -p "{\"spec\":{\"suspend\":false}}"'
```

### Step 6 — Wipe bronze/silver/serving/gold, reset Kafka topics, wipe Flink state

This is the destructive clean-slate point.

Database wipe using `invest-db-credentials`:

> **⚠️ 가드레일 — `silver.symbol_daily_ohlc` 절대 TRUNCATE 금지**
>
> `silver.symbol_daily_ohlc`(일/주/월봉)는 **tick 스트림과 무관한 별도 테이블**이다. Flink나 tick_persistence가 재기동해도 자동 복구되지 않는다. KIS REST 백필(`backfill-daily-ohlc` CronJob)만이 이 테이블을 채운다.
>
> 아래 TRUNCATE 목록에 `silver.symbol_daily_ohlc`가 없는 것은 의도적이다. 임의로 추가하지 말 것.
>
> **비워진 경우 복구 방법:**
> - 자동: `backfill-daily-ohlc` CronJob이 다음 거래일 16:00 KST에 자동 실행된다.
> - 즉시: `kubectl -n invest create job --from=cronjob/backfill-daily-ohlc backfill-manual-$(date +%Y%m%d)`
>
> **복구 확인 쿼리:**
> ```sql
> SELECT interval, count(*) FROM silver.symbol_daily_ohlc GROUP BY 1;
> ```
> `d` / `w` / `m` 각각 행이 존재해야 정상이다.

```bash
ssh hyunsoo-cluster1 'P=$(sudo k3s kubectl -n invest get secret invest-db-credentials -o jsonpath="{.data.POSTGRES_PASSWORD}" | base64 -d); \
  sudo k3s kubectl -n postgres exec statefulset/postgresql -- env PGPASSWORD=$P psql -v ON_ERROR_STOP=1 -U invest -d invest_view \
  -c "TRUNCATE TABLE gold.pattern_events, serving.symbol_snapshot, silver.symbol_5m_metrics, bronze.tick_history RESTART IDENTITY CASCADE;"'
```

Reset Kafka topics and let Flux recreate the Strimzi `KafkaTopic` resources:

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n invest delete kafkatopic stock-ticks stock-patterns; \
  sudo k3s kubectl -n invest wait --for=delete kafkatopic/stock-ticks --timeout=300s || true; \
  sudo k3s kubectl -n invest wait --for=delete kafkatopic/stock-patterns --timeout=300s || true; \
  sudo k3s kubectl -n flux-system annotate kustomization invest-kafka reconcile.fluxcd.io/requestedAt="$(date -Is)" --overwrite; \
  sudo k3s kubectl -n invest wait kafkatopic/stock-ticks --for=condition=Ready --timeout=300s; \
  sudo k3s kubectl -n invest wait kafkatopic/stock-patterns --for=condition=Ready --timeout=300s'
```

Wipe Flink checkpoint/HA state. `invest-flink` remains suspended until Step 7 so Flink does not restart against half-reset state.

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n invest delete pvc flink-checkpoint-storage; \
  sudo k3s kubectl -n invest wait --for=delete pvc/flink-checkpoint-storage --timeout=300s || true'
```

Rollback note: rollback after this step is operational recovery from database/Kafka/PVC backups only. Do not force power-cycle. If topic or PVC recreation fails, fix the operator/manifest issue and resume Flux only when state is coherent.

### Step 7 — Resume services and resubscribe/re-ingest

Resume Flux and request reconcile. This lets GitOps restore declared replicas, recreate the Flink checkpoint PVC, and restart subscriptions.

```bash
ssh hyunsoo-cluster1 'sudo k3s kubectl -n flux-system patch kustomization invest-services --type=merge -p "{\"spec\":{\"suspend\":false}}"; \
  sudo k3s kubectl -n flux-system patch kustomization invest-flink --type=merge -p "{\"spec\":{\"suspend\":false}}"; \
  sudo k3s kubectl -n flux-system annotate kustomization invest-services reconcile.fluxcd.io/requestedAt="$(date -Is)" --overwrite; \
  sudo k3s kubectl -n flux-system annotate kustomization invest-flink reconcile.fluxcd.io/requestedAt="$(date -Is)" --overwrite; \
  sudo k3s kubectl -n invest wait pvc/flink-checkpoint-storage --for=jsonpath="{.status.phase}"=Bound --timeout=300s; \
  sudo k3s kubectl -n invest rollout status deploy/tick-persistence --timeout=600s; \
  sudo k3s kubectl -n invest rollout status deploy/kis-ingestion --timeout=600s; \
  sudo k3s kubectl -n invest rollout status deploy/event-pattern-persistence --timeout=600s; \
  sudo k3s kubectl -n invest get flinkdeployment stream-detection; \
  sudo k3s kubectl -n invest logs deploy/kis-ingestion --tail=100'
```

Rollback note: if new pods fail, revert the homelab-infra image tag commit and push so Flux rolls back. If the market is closed, lack of live ticks is normal; wait for weekday KST market hours for live proof.

### Step 8 — Consistency verification (A/B/C)

Use `persisted_at` for ingest freshness and `event_id` for identity. Evidence A must show `duplicate_rows = 0` after clean-slate re-ingest.

Evidence A — total vs distinct `event_id` rows:

```bash
ssh hyunsoo-cluster1 'P=$(sudo k3s kubectl -n invest get secret invest-db-credentials -o jsonpath="{.data.POSTGRES_PASSWORD}" | base64 -d); \
  sudo k3s kubectl -n postgres exec statefulset/postgresql -- env PGPASSWORD=$P psql -U invest -d invest_view \
  -c "SELECT count(*) AS total_rows, count(DISTINCT event_id) AS distinct_event_ids, count(*) - count(DISTINCT event_id) AS duplicate_rows FROM bronze.tick_history;"'
```

Evidence B — snapshot freshness via `last_event_ts` against latest bronze event per symbol. `snapshot_lag` should be zero/near-zero for symbols receiving post-reset ticks.

```bash
ssh hyunsoo-cluster1 'P=$(sudo k3s kubectl -n invest get secret invest-db-credentials -o jsonpath="{.data.POSTGRES_PASSWORD}" | base64 -d); \
  sudo k3s kubectl -n postgres exec statefulset/postgresql -- env PGPASSWORD=$P psql -U invest -d invest_view \
  -c "WITH latest AS (SELECT symbol, max(event_ts) AS max_event_ts FROM bronze.tick_history GROUP BY symbol) SELECT s.symbol, s.last_price, s.last_event_ts, l.max_event_ts, l.max_event_ts - s.last_event_ts AS snapshot_lag, now() - s.updated_at AS updated_staleness FROM serving.symbol_snapshot s JOIN latest l USING (symbol) ORDER BY snapshot_lag DESC NULLS LAST, updated_staleness DESC NULLS LAST LIMIT 20;"'
```

Evidence C — max `persisted_at` per symbol. During market hours, `max_persisted_at` should advance; outside market hours it may remain unchanged without indicating failure.

```bash
ssh hyunsoo-cluster1 'P=$(sudo k3s kubectl -n invest get secret invest-db-credentials -o jsonpath="{.data.POSTGRES_PASSWORD}" | base64 -d); \
  sudo k3s kubectl -n postgres exec statefulset/postgresql -- env PGPASSWORD=$P psql -U invest -d invest_view \
  -c "SELECT symbol, count(*) AS rows, max(persisted_at) AS max_persisted_at FROM bronze.tick_history GROUP BY symbol ORDER BY max_persisted_at DESC NULLS LAST, symbol LIMIT 50;"'
```

Additional consistency checks:

```bash
ssh hyunsoo-cluster1 'P=$(sudo k3s kubectl -n invest get secret invest-db-credentials -o jsonpath="{.data.POSTGRES_PASSWORD}" | base64 -d); \
  sudo k3s kubectl -n postgres exec statefulset/postgresql -- env PGPASSWORD=$P psql -U invest -d invest_view \
  -c "SELECT count(*) AS ohlc_violations FROM silver.symbol_5m_metrics WHERE NOT (low <= open AND open <= high AND low <= close AND close <= high);" \
  -c "SELECT pattern_type, count(*) FROM gold.pattern_events WHERE triggered_at > now() - interval '\''1 hour'\'' GROUP BY 1;"'
```

Rollback note: if verification fails after the destructive wipe, do not assume code rollback alone fixes state. Keep only consistent writers running; otherwise scale down the faulty workload, preserve logs/query evidence, revert image tags through homelab-infra if needed, and plan a second clean reset.

## Completion checklist

- [ ] Both images built/pushed from Mac buildx amd64 with tag = current commit hash: `kis_ingestion`, `tick_persistence`.
- [ ] `make schemas` registered `schemas/stock-ticks.avsc` containing optional `event_id` before producer deploy.
- [ ] `homelab-infra/infrastructure/invest/services/deployments.yaml` tag bump committed and pushed; Flux auto-deploy trusted.
- [ ] Migration `0006_tick_event_time_contract` applied with `alembic upgrade head`.
- [ ] Services stopped, then bronze/silver/serving/gold wiped, Kafka `stock-ticks`/`stock-patterns` reset, Flink checkpoint state wiped.
- [ ] Services resumed through Flux and KIS resubscribed/re-ingested.
- [ ] A/B/C verification captured: total vs distinct `event_id`, snapshot freshness via `last_event_ts`, max `persisted_at` per symbol.
- [ ] Actual prod execution remains **[OPERATOR] non-blocking** and is scheduled for market-hours live proof when needed.
