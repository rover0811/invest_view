#!/usr/bin/env bash
# teardown.sh — Tear down the FlinkDeployment and optionally the kind cluster.
# Usage:
#   bash teardown.sh           # delete FlinkDeployment only
#   bash teardown.sh --cluster # also delete the kind cluster

set -euo pipefail

CLUSTER_NAME="invest-flink"
DEPLOYMENT_NAME="stream-detection"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[teardown] $*"; }

# 1. Stop port-forward if running
PF_PID_FILE="${SCRIPT_DIR}/.port-forward.pid"
if [[ -f "${PF_PID_FILE}" ]]; then
  PID="$(cat "${PF_PID_FILE}")"
  kill "${PID}" 2>/dev/null || true
  rm -f "${PF_PID_FILE}"
  log "stopped port-forward PID=${PID}"
fi

# 2. Delete the FlinkDeployment (idempotent — --ignore-not-found)
log "deleting FlinkDeployment ${DEPLOYMENT_NAME}..."
kubectl delete flinkdeployment "${DEPLOYMENT_NAME}" --ignore-not-found

# 3. Optional: delete the kind cluster entirely
if [[ "${1:-}" == "--cluster" ]]; then
  log "deleting kind cluster ${CLUSTER_NAME}..."
  kind delete cluster --name "${CLUSTER_NAME}"
fi

log "teardown complete."
