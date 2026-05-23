#!/usr/bin/env bash
set -euo pipefail

REGISTRY_URL="${SCHEMA_REGISTRY_URL:-http://localhost:8081}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

run_register() {
    local subject="$1"
    local schema_file="$2"
    (
        cd "$REPO_ROOT/services/kis_ingestion"
        uv run python "$REPO_ROOT/scripts/register_schemas.py" \
            --registry-url "$REGISTRY_URL" \
            --subject "$subject" \
            --schema-file "$REPO_ROOT/$schema_file"
    )
}

run_register "stock-ticks-value" "schemas/stock-ticks.avsc"
run_register "stock-alerts-value" "schemas/stock-alerts.avsc"
