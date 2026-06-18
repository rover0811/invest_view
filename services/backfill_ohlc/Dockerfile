# Build context MUST be the repo root:
#   docker build -f services/backfill_ohlc/Dockerfile -t backfill_ohlc:<tag> .
#
# Dedicated image: scripts/backfill_daily_ohlc.py imports across BOTH service src
# trees at once (kis_ingestion.token_manager AND tick_persistence.db.session /
# tick_persistence.kis.daily_client) plus the script in scripts/. Neither prod image
# carries the other service's src or scripts/, so this repo-root-context image COPYs
# both src trees and runs from source via PYTHONPATH (parity with the sibling images).
# kis_ingestion is a namespace package (no __init__.py), so importing token_manager
# never pulls its confluent-kafka/fastavro/websockets siblings and never imports
# kis_ingestion.container -- the Avro schemas/ tree is therefore not needed here.
#
# CMD (not ENTRYPOINT) keeps the default run (KIS_WATCH_SYMBOLS, 10yr, d,w,m) fully
# overridable: the CronJob supplies its own command+args; smoke tests override directly.

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

# OPENSSL_armcap=0 forces OpenSSL's software crypto path; documented no-op on x86
# production, retained for parity with kis_ingestion / tick_persistence so an arm64
# dev host never SIGILLs if a transitive TLS dep probes ARM CPU features at import.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1 \
    OPENSSL_armcap=0 \
    PYTHONPATH=/app/services/tick_persistence/src:/app/services/kis_ingestion/src

WORKDIR /app

RUN uv pip install --system \
    "httpx" \
    "sqlalchemy[asyncio]>=2.0" \
    "asyncpg" \
    "pydantic>=2" \
    "pydantic-settings"

COPY services/tick_persistence/src/ /app/services/tick_persistence/src/
COPY services/kis_ingestion/src/ /app/services/kis_ingestion/src/
COPY scripts/backfill_daily_ohlc.py /app/scripts/backfill_daily_ohlc.py

CMD ["python", "/app/scripts/backfill_daily_ohlc.py"]
