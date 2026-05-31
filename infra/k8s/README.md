# infra/k8s — kis_ingestion + alert_service on kind

Plain raw Kubernetes manifests for running `kis_ingestion` and `alert_service` on the
existing `kind` cluster (`invest-flink`, context `kind-invest-flink`) alongside the Flink
`stream-detection` job. Infrastructure (Kafka, Schema Registry, Postgres) keeps running on
Docker Compose; pods reach it over the kind↔compose network bridge.

## Manifests

| File | Kind | Notes |
|------|------|-------|
| `kis-ingestion-deployment.yaml` | Deployment | `kis_ingestion:qa`, inline env, secrets via `kis-credentials`, no probe (Decision E) |
| `alert-service-deployment.yaml` | Deployment | `alert_service:qa`, `alembic-migrate` initContainer, `/health` readiness+liveness probes |
| `alert-service-service.yaml` | Service (ClusterIP) | exposes `:8000` for port-forward in tests |
| `alert-service-configmap.yaml` | ConfigMap `alert-service-config` | non-secret `ALERT_SERVICE_*` config |

Both images use `imagePullPolicy: Never` — they must be `kind load`ed first (see B2 / `deploy.sh`).

## Secrets (referenced, NOT committed)

These manifests reference two Secrets by name — they are **not** committed with real values.
Bootstrap them once from the root `.env` (KIS_APP_KEY / KIS_APP_SECRET):

| Secret | Keys |
|--------|------|
| `kis-credentials` | `KIS_APP_KEY`, `KIS_APP_SECRET` |
| `alert-service-secrets` | `ALERT_SERVICE_DATABASE_URL`, `ALERT_SERVICE_JWT_SECRET` |

```sh
# Bootstrap secrets (run once, values from root .env — NOT committed)
kubectl create secret generic kis-credentials \
  --from-literal=KIS_APP_KEY="<from .env>" \
  --from-literal=KIS_APP_SECRET="<from .env>" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic alert-service-secrets \
  --from-literal=ALERT_SERVICE_DATABASE_URL='postgresql+asyncpg://postgres:postgres@postgres:5432/invest_view' \
  --from-literal=ALERT_SERVICE_JWT_SECRET='dev-secret-change-me' \
  --dry-run=client -o yaml | kubectl apply -f -
```

## Deploy

```sh
# Build + load + deploy (see B2 / deploy.sh)
# 1. Build images: kis_ingestion:qa, alert_service:qa
# 2. kind load docker-image kis_ingestion:qa  --name invest-flink
#    kind load docker-image alert_service:qa  --name invest-flink
# 3. Apply config + workloads:
kubectl apply -f infra/k8s/alert-service-configmap.yaml
kubectl apply -f infra/k8s/kis-ingestion-deployment.yaml
kubectl apply -f infra/k8s/alert-service-deployment.yaml
kubectl apply -f infra/k8s/alert-service-service.yaml

# Port-forward for tests:
kubectl port-forward svc/alert-service 8000:8000
```

## NOTE — network bridge

Pods reach the Compose infrastructure (`kafka:29092`, `postgres:5432`,
`schema-registry:8081`) over the kind↔compose Docker bridge network
`invest_view_default`. If the bridge is missing (e.g. after `docker compose down`),
re-establish it with the idempotent setup script:

```sh
bash services/stream_detection_java/scripts/setup-kind.sh
```
