FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /bin/uv

# OPENSSL_armcap=0 forces OpenSSL's software crypto path. The cryptography wheel
# (pulled in transitively by confluent_kafka.schema_registry and by PyJWT) SIGILLs at
# import on this aarch64 host because OpenSSL's ARM CPU-feature auto-probe emits an
# instruction the VM traps; without this, `python -m alert_service` crashes on startup
# (jwt -> cryptography._rust). No-op on x86/production. Parity with kis_ingestion (T6).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    OPENSSL_armcap=0

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY services/alert_service/pyproject.toml ./services/alert_service/pyproject.toml
COPY services/kis_ingestion/pyproject.toml ./services/kis_ingestion/pyproject.toml

RUN uv sync --frozen --package alert-service --no-install-workspace --no-dev

COPY schemas/ ./schemas/
COPY services/alert_service/ ./services/alert_service/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/services/alert_service/src \
    ALERT_SERVICE_AVRO_SCHEMA_PATH=/app/schemas/stock-alerts.avsc

RUN chmod +x /app/services/alert_service/docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"]

ENTRYPOINT ["/app/services/alert_service/docker-entrypoint.sh"]
