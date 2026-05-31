# infra/k8s — kis_ingestion + alert_service on kind

Plain raw Kubernetes manifests for running `kis_ingestion` and `alert_service` on the
`kind` cluster (`invest-flink`, context `kind-invest-flink`) alongside the Flink
`stream-detection` jobs. Infrastructure (Strimzi Kafka, in-cluster Schema Registry, PVC
Postgres) runs in the same cluster; pods reach it via in-cluster DNS
(`invest-kafka-kafka-bootstrap.kafka.svc:9092`, `schema-registry:8081`, `postgres:5432`).
There is no Docker Compose and no kind↔compose network bridge. Operate everything through
the top-level `Makefile` (`make help`).

## Manifests

| File | Kind | Notes |
|------|------|-------|
| `kis-ingestion-deployment.yaml` | Deployment | `kis_ingestion:qa`, inline env, secrets via `kis-credentials`, no probe (Decision E) |
| `alert-service-deployment.yaml` | Deployment | `alert_service:qa`, `alembic-migrate` initContainer, `/health` readiness+liveness probes |
| `alert-service-service.yaml` | Service (ClusterIP) | exposes `:8000` for port-forward in tests |
| `alert-service-configmap.yaml` | ConfigMap `alert-service-config` | non-secret `ALERT_SERVICE_*` config |

Both images use `imagePullPolicy: Never` — they must be `kind load`ed first (`make images`).

## Secrets (referenced, NOT committed)

These manifests reference two Secrets by name — they are **not** committed with real values.
`make secrets` bootstraps them from the root `.env` (KIS_APP_KEY / KIS_APP_SECRET):

| Secret | Keys |
|--------|------|
| `kis-credentials` | `KIS_APP_KEY`, `KIS_APP_SECRET` |
| `alert-service-secrets` | `ALERT_SERVICE_DATABASE_URL`, `ALERT_SERVICE_JWT_SECRET` |

## Deploy

```sh
make images   # build kis_ingestion:qa + alert_service:qa and kind-load them
make apps     # create/refresh Secrets, then apply config + workloads

# Port-forward for tests:
make pf-alert   # kubectl port-forward svc/alert-service 8000:8000
```
