#!/usr/bin/env bash
# deploy.sh — Build, image, load, and deploy the stream-detection-java FlinkDeployment.
# Idempotent: re-running rebuilds + replaces the existing deployment.

set -euo pipefail

CLUSTER_NAME="invest-flink"
IMAGE_TAG="rules3"
IMAGE_NAME="stream-detection-java:${IMAGE_TAG}"
DEPLOYMENT_NAME="stream-detection"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PORT_FORWARD_LOCAL=8083
PORT_FORWARD_REMOTE=8081

log() { echo "[deploy] $*"; }

# 1. Maven package (skipTests for speed; tests run separately via mvn test)
log "building fat JAR via mvn package..."
mvn -B -f "${MODULE_ROOT}/pom.xml" clean package -DskipTests -q

# 2. Docker build
log "building Docker image ${IMAGE_NAME}..."
docker build -t "${IMAGE_NAME}" "${MODULE_ROOT}"

# 3. Load image into kind
log "loading ${IMAGE_NAME} into kind cluster ${CLUSTER_NAME}..."
kind load docker-image "${IMAGE_NAME}" --name "${CLUSTER_NAME}"

# 4. Apply FlinkDeployment (idempotent: kubectl apply replaces if exists)
log "applying FlinkDeployment..."
kubectl apply -f "${MODULE_ROOT}/k8s/flinkdeployment.yaml"

# 5. Force restart if it was already running (so it picks up the new image)
NONCE="$(date +%s)"
kubectl patch flinkdeployment "${DEPLOYMENT_NAME}" --type merge -p "{\"spec\":{\"restartNonce\":${NONCE}}}" 2>/dev/null || true

# 6. Wait until the job reports RUNNING (timeout: 10 minutes for cold start with image pull)
log "waiting for FlinkDeployment ${DEPLOYMENT_NAME} to reach state=RUNNING (up to 10 min)..."
kubectl wait --for=jsonpath='{.status.jobStatus.state}'=RUNNING \
  "flinkdeployment/${DEPLOYMENT_NAME}" --timeout=600s

# 7. Kill any existing port-forward for our local port, then start a new one
PF_PID_FILE="${MODULE_ROOT}/scripts/.port-forward.pid"
if [[ -f "${PF_PID_FILE}" ]]; then
  OLD_PID="$(cat "${PF_PID_FILE}")"
  kill "${OLD_PID}" 2>/dev/null || true
  rm -f "${PF_PID_FILE}"
fi

log "starting Flink UI port-forward localhost:${PORT_FORWARD_LOCAL} → svc/${DEPLOYMENT_NAME}-rest:${PORT_FORWARD_REMOTE}..."
nohup kubectl port-forward "svc/${DEPLOYMENT_NAME}-rest" "${PORT_FORWARD_LOCAL}:${PORT_FORWARD_REMOTE}" \
  > "${MODULE_ROOT}/scripts/.port-forward.log" 2>&1 &
echo $! > "${PF_PID_FILE}"
sleep 2

log "deploy complete."
log "Flink UI: http://localhost:${PORT_FORWARD_LOCAL}"
log "Port-forward PID: $(cat "${PF_PID_FILE}")"
log "Stop port-forward: kill \$(cat ${PF_PID_FILE})"
