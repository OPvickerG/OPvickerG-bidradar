worker: python -m scanner.scheduler.worker
web: sh -c "alembic upgrade head && uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080}"
