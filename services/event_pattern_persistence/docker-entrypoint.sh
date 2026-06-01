#!/bin/sh
set -e

cd /app/services/event_pattern_persistence

echo "[entrypoint] running alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting event_pattern_persistence"
exec python -m event_pattern_persistence
