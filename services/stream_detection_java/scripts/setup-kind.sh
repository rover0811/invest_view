#!/usr/bin/env bash
# setup-kind.sh — One-shot bootstrap of the kind cluster + Flink Operator + Schema Registry subjects.
# Idempotent: re-running is safe and skips already-existing resources.

set -euo pipefail

CLUSTER_NAME="invest-flink"
OPERATOR_VERSION="1.14.0"
OPERATOR_NAMESPACE="default"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
COMPOSE_NETWORK="invest_view_default"

log() { echo "[setup-kind] $*"; }

# 1. Prerequisite checks (fail fast if a required tool is missing)
log "checking prerequisites..."
for cmd in docker kind helm kubectl mvn java; do
  command -v "${cmd}" >/dev/null 2>&1 || { echo "ERROR: ${cmd} not found in PATH" >&2; exit 1; }
done

# JAVA_HOME must point at JDK 17 (per pom enforcer rule)
JAVA_VERSION="$(java -version 2>&1 | head -1 | awk -F'"' '{print $2}' | cut -d. -f1)"
if [[ "${JAVA_VERSION}" != "17" ]]; then
  echo "ERROR: JAVA_HOME must point at JDK 17 (found: ${JAVA_VERSION}). Set JAVA_HOME=\$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home" >&2
  exit 1
fi

# 2. Kind cluster (idempotent: skip if exists)
if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  log "kind cluster '${CLUSTER_NAME}' already exists — skipping create"
else
  log "creating kind cluster '${CLUSTER_NAME}'..."
  kind create cluster --name "${CLUSTER_NAME}"
fi

# Ensure kubectl context points at this cluster
kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null

# 3. Connect kind control-plane container to the docker-compose network (Wave 0.4 finding)
# This lets pods reach the compose Kafka/SR by their compose service names (kafka:29092, schema-registry:8081)
# instead of host.docker.internal (which advertises wrong listeners).
CONTROL_PLANE="${CLUSTER_NAME}-control-plane"
if docker network inspect "${COMPOSE_NETWORK}" >/dev/null 2>&1; then
  if docker network inspect "${COMPOSE_NETWORK}" -f '{{range .Containers}}{{.Name}} {{end}}' | tr ' ' '\n' | grep -qx "${CONTROL_PLANE}"; then
    log "kind control-plane already attached to ${COMPOSE_NETWORK} — skipping"
  else
    log "attaching ${CONTROL_PLANE} to ${COMPOSE_NETWORK}..."
    docker network connect "${COMPOSE_NETWORK}" "${CONTROL_PLANE}"
  fi
else
  log "WARNING: docker network '${COMPOSE_NETWORK}' not found — docker-compose may not be running. Kafka/SR will be unreachable."
fi

# 4. Flink Kubernetes Operator (Helm chart 1.14.0 — verified compatible with Flink 1.18)
helm repo list 2>/dev/null | grep -q "^flink-operator-repo" \
  || helm repo add flink-operator-repo "https://archive.apache.org/dist/flink/flink-kubernetes-operator-${OPERATOR_VERSION}/"
helm repo update flink-operator-repo >/dev/null

if helm list -n "${OPERATOR_NAMESPACE}" -q 2>/dev/null | grep -qx "flink-kubernetes-operator"; then
  log "flink-kubernetes-operator already installed — skipping"
else
  log "installing flink-kubernetes-operator ${OPERATOR_VERSION}..."
  # cert-manager is a prerequisite of the operator's admission webhook
  if ! kubectl get crd certificates.cert-manager.io >/dev/null 2>&1; then
    log "installing cert-manager (Operator webhook prerequisite)..."
    kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/v1.13.3/cert-manager.yaml"
    kubectl wait --for=condition=Available --timeout=120s -n cert-manager deployment/cert-manager-webhook
  fi
  helm install flink-kubernetes-operator flink-operator-repo/flink-kubernetes-operator \
    --version "${OPERATOR_VERSION}" \
    -n "${OPERATOR_NAMESPACE}"
fi

kubectl wait --for=condition=Available --timeout=180s deployment/flink-kubernetes-operator

# 5. Pre-register Avro schemas (idempotent — register_all_schemas.sh handles existing subjects)
if [[ -x "${REPO_ROOT}/scripts/register_all_schemas.sh" ]]; then
  log "registering Avro schemas via repo-root script..."
  ( cd "${REPO_ROOT}" && bash scripts/register_all_schemas.sh )
else
  log "WARNING: ${REPO_ROOT}/scripts/register_all_schemas.sh not found or not executable"
fi

log "setup complete. Next: bash ${SCRIPT_DIR}/deploy.sh"
