# Build context MUST be the repo root: docker build -f services/kis_ingestion/Dockerfile -t kis_ingestion:qa .
#
# INVARIANT (do not "simplify" by uv-installing the package): container.py:45 derives the
# Avro schema path as 5 .parent hops from itself. The layout below puts container.py at
# /app/services/kis_ingestion/src/kis_ingestion/container.py (5 parents == /app) so the
# schema resolves to /app/schemas/stock-ticks.avsc. Installing kis_ingestion into
# site-packages/.venv would move __file__ and silently break that resolution at runtime,
# so we install ONLY third-party deps and import the package from source via PYTHONPATH.

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

# OPENSSL_armcap=0 forces OpenSSL's software crypto path. The cryptography wheel
# (pulled in transitively by confluent_kafka.schema_registry) SIGILLs at import on
# this aarch64 host because OpenSSL's ARM CPU-feature auto-probe emits an instruction
# the VM traps; without this, `python -m kis_ingestion` crashes on startup. No-op on x86.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1 \
    OPENSSL_armcap=0 \
    PYTHONPATH=/app/services/kis_ingestion/src

WORKDIR /app

RUN uv pip install --system \
    "pydantic" \
    "httpx" \
    "websockets" \
    "pydantic-settings" \
    "confluent-kafka[avro,schemaregistry]>=2.3" \
    "fastavro"

COPY schemas/ /app/schemas/

COPY services/kis_ingestion/ /app/services/kis_ingestion/

CMD ["python", "-m", "kis_ingestion"]
