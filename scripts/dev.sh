#!/usr/bin/env bash
# Local development helper. Usage: scripts/dev.sh migrate | scan | serve
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env.local ] && . ./.env.local; set +a
export PYTHONPATH=.
case "${1:-serve}" in
  migrate) .venv/bin/alembic upgrade head ;;
  scan)    .venv/bin/python -m scanner.scheduler.run --trigger manual ;;
  serve)   .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port "${PORT:-8080}" ;;
  *) echo "usage: $0 migrate|scan|serve"; exit 2 ;;
esac
