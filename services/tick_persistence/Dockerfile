# Build context MUST be the repo root:
#   docker build -f services/tick_persistence/Dockerfile -t tick_persistence:qa .
#
# tick_persistence is EXCLUDED from the root uv workspace (root pyproject.toml
# [tool.uv.workspace] exclude). It therefore carries its OWN isolated uv.lock and is
# installed in isolation: we COPY the service's own pyproject.toml + uv.lock (NOT the
# root lock, which does not contain this package's deps) and run an isolated
# `uv sync --frozen --package tick-persistence --no-install-workspace`. That installs
# only third-party deps; the tick_persistence package itself is run from source via
# PYTHONPATH (parity with alert_service / kis_ingestion).
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /bin/uv

# OPENSSL_armcap=0 forces OpenSSL's software crypto path. The cryptography wheel
# (pulled in transitively by confluent_kafka.schema_registry) SIGILLs at import on
# this aarch64 host because OpenSSL's ARM CPU-feature auto-probe emits an instruction
# the VM traps; without this, `python -m tick_persistence` crashes on startup.
# No-op on x86/production. Parity with alert_service / kis_ingestion.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    OPENSSL_armcap=0

WORKDIR /app

COPY services/tick_persistence/pyproject.toml services/tick_persistence/uv.lock ./services/tick_persistence/

RUN cd services/tick_persistence && uv sync --frozen --package tick-persistence --no-install-workspace --no-dev

COPY schemas/ ./schemas/
COPY services/tick_persistence/ ./services/tick_persistence/

ENV PATH="/app/services/tick_persistence/.venv/bin:$PATH" \
    PYTHONPATH=/app/services/tick_persistence/src \
    TICK_PERSISTENCE_AVRO_SCHEMA_PATH=/app/schemas/stock-ticks.avsc

RUN chmod +x /app/services/tick_persistence/docker-entrypoint.sh

ENTRYPOINT ["/app/services/tick_persistence/docker-entrypoint.sh"]
