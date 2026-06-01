#!/bin/sh
set -e

cd /app/services/tick_persistence

echo "[entrypoint] running alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting tick_persistence"
exec python -m tick_persistence
