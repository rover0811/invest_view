#!/usr/bin/env bash
# deploy.sh — Build, load, and deploy kis_ingestion + alert_service to local kind.
# Idempotent: re-running refreshes secrets from .env, rebuilds images, reloads kind, and restarts workloads.

set -euo pipefail

CLUSTER_NAME="invest-flink"
COMPOSE_FILE="docker-compose.dev.yml"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

log() { echo "[deploy] $*"; }

cd "${REPO_ROOT}"

log "checking kind↔compose bridge..."
if ! docker network inspect invest_view_default -f '{{range .Containers}}{{.Name}} {{end}}' | grep -q invest-flink-control-plane; then
  log "bridge missing; running setup-kind.sh..."
  bash services/stream_detection_java/scripts/setup-kind.sh
fi

log "checking compose infrastructure..."
if ! docker compose -f "${COMPOSE_FILE}" exec -T postgres pg_isready -U postgres -d invest_view; then
  log "postgres not ready; starting required infrastructure..."
  docker compose -f "${COMPOSE_FILE}" up -d kafka schema-registry postgres topic-init
fi

if ! docker compose -f "${COMPOSE_FILE}" exec -T kafka kafka-topics --bootstrap-server kafka:29092 --list | grep -Eq 'stock-(ticks|alerts)'; then
  log "kafka topics not ready; starting topic-init..."
  docker compose -f "${COMPOSE_FILE}" up -d kafka schema-registry postgres topic-init
fi

log "confirming FlinkDeployment stream-detection remains RUNNING..."
kubectl get flinkdeployment stream-detection -o jsonpath='{.status.jobStatus.state}' | grep -q '^RUNNING$'

log "refreshing Kubernetes secrets from .env..."
KIS_APP_KEY="$(grep '^KIS_APP_KEY=' .env | cut -d= -f2-)"
KIS_APP_SECRET="$(grep '^KIS_APP_SECRET=' .env | cut -d= -f2-)"
test -n "${KIS_APP_KEY}"
test -n "${KIS_APP_SECRET}"

kubectl create secret generic kis-credentials \
  --from-literal=KIS_APP_KEY="${KIS_APP_KEY}" \
  --from-literal=KIS_APP_SECRET="${KIS_APP_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic alert-service-secrets \
  --from-literal=ALERT_SERVICE_DATABASE_URL='postgresql+asyncpg://postgres:postgres@postgres:5432/invest_view' \
  --from-literal=ALERT_SERVICE_JWT_SECRET='dev-secret-change-me' \
  --dry-run=client -o yaml | kubectl apply -f -

log "building Docker images..."
docker build -f services/kis_ingestion/Dockerfile -t kis_ingestion:qa .
docker build -f services/alert_service/Dockerfile -t alert_service:qa .

log "loading Docker images into kind cluster ${CLUSTER_NAME}..."
kind load docker-image kis_ingestion:qa --name "${CLUSTER_NAME}"
kind load docker-image alert_service:qa --name "${CLUSTER_NAME}"
docker exec invest-flink-control-plane crictl images | grep -E 'kis_ingestion|alert_service'

log "applying Kubernetes manifests..."
kubectl apply -f infra/k8s/alert-service-configmap.yaml
kubectl apply -f infra/k8s/alert-service-service.yaml
kubectl apply -f infra/k8s/alert-service-deployment.yaml
kubectl apply -f infra/k8s/kis-ingestion-deployment.yaml

log "restarting app deployments so same-tag local images are picked up..."
kubectl rollout restart deployment/alert-service deployment/kis-ingestion

log "waiting for deployments to become available..."
kubectl wait --for=condition=available deployment/alert-service deployment/kis-ingestion --timeout=300s

log "deploy complete."
log "Port-forward for verification: kubectl port-forward svc/alert-service 8000:8000"
