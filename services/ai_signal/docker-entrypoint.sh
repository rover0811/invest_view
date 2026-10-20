#!/bin/sh
set -e

cd /app/services/ai_signal
alembic upgrade head
exec python -m ai_signal all
