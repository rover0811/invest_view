FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY services/ai_signal/pyproject.toml ./services/ai_signal/
RUN cd services/ai_signal && uv sync --frozen --package ai-signal --no-install-workspace --no-dev

COPY schemas/ ./schemas/
COPY services/ai_signal/ ./services/ai_signal/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/services/ai_signal/src

RUN chmod +x /app/services/ai_signal/docker-entrypoint.sh
ENTRYPOINT ["/app/services/ai_signal/docker-entrypoint.sh"]
