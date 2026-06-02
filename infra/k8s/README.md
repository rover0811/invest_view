# infra/k8s — application services on kind

Plain raw Kubernetes manifests for running `kis_ingestion`, `alert_service`, `tick_persistence`, and `event_pattern_persistence` on the
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
| `tick-persistence-deployment.yaml` | Deployment | `tick_persistence:qa`, `alembic-migrate` and dependency-wait initContainers |
| `tick-persistence-configmap.yaml` | ConfigMap `tick-persistence-config` | non-secret `TICK_PERSISTENCE_*` config |
| `event-pattern-persistence-deployment.yaml` | Deployment | `event_pattern_persistence:qa`, `alembic-migrate` initContainer |
| `event-pattern-persistence-configmap.yaml` | ConfigMap `event-pattern-persistence-config` | non-secret `EVENT_PATTERN_PERSISTENCE_*` config |

Both images use `imagePullPolicy: Never` — they must be `kind load`ed first (`make images`).

## Secrets (referenced, NOT committed)

These manifests reference four Secrets by name — they are **not** committed with real values.
`make secrets` bootstraps them from the root `.env` (KIS_APP_KEY / KIS_APP_SECRET):

| Secret | Keys |
|--------|------|
| `kis-credentials` | `KIS_APP_KEY`, `KIS_APP_SECRET` |
| `alert-service-secrets` | `ALERT_SERVICE_DATABASE_URL`, `ALERT_SERVICE_JWT_SECRET` |
| `tick-persistence-secrets` | `TICK_PERSISTENCE_DATABASE_URL` |
| `event-pattern-persistence-secrets` | `EVENT_PATTERN_PERSISTENCE_DATABASE_URL` |

## Deploy

```sh
make images   # build kis_ingestion:qa + alert_service:qa + tick_persistence:qa + event_pattern_persistence:qa and kind-load them
make apps     # create/refresh Secrets, then apply config + workloads for all 4 services

# Port-forward for tests:
make pf-alert   # kubectl port-forward svc/alert-service 8000:8000
```
