# One image, two roles:
#   * Cloud Run service (dashboard/API):  default CMD
#   * Cloud Run Job (scanner):           command override  python -m scanner.scheduler.run
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app PIP_NO_CACHE_DIR=1 TZ=UTC
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY --chown=app:app . .
USER app
EXPOSE 8080
# Migrations run at service start (idempotent; Alembic serialises them with a version table).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
