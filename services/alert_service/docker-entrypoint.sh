#!/bin/sh
set -e

# cd so alembic.ini (script_location=alembic, relative) and env.py resolve.
cd /app/services/alert_service

echo "[entrypoint] running alembic upgrade head"
alembic upgrade head

# exec makes uvicorn PID 1 so SIGTERM reaches it directly for graceful shutdown.
echo "[entrypoint] starting alert_service"
exec python -m alert_service
