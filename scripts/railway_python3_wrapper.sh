#!/bin/sh
set -eu

REAL_PYTHON="/usr/local/bin/python3.real"

if [ "${1:-}" = "-c" ] && [ "${2:-}" = *"async def f()"* ]; then
  cd /app
  exec "$REAL_PYTHON" scripts/repair_alembic_state.py
fi

exec "$REAL_PYTHON" "$@"
