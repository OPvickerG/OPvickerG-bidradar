#!/usr/bin/env bash
# Post-deployment checks (read-only). Usage: PROJECT_ID=my-project ./infrastructure/gcp/verify.sh
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"; REGION="${REGION:-asia-south1}"
gcloud config set project "$PROJECT_ID" >/dev/null
URL="$(gcloud run services describe bidradar-web --region "$REGION" --format 'value(status.url)')"
echo "Health:";        curl -fsS "$URL/healthz"; echo
echo "Scheduler:";     gcloud scheduler jobs describe bidradar-scan-every-30min --location "$REGION" \
                         --format 'table(schedule,timeZone,state,lastAttemptTime,status.code)'
echo "Executions:";    gcloud run jobs executions list --job bidradar-scan --region "$REGION" --limit 6
echo "Recent logs:";   gcloud logging read 'resource.type="cloud_run_job" AND jsonPayload.message="scan finished"' \
                         --limit 5 --format 'value(timestamp,jsonPayload.status,jsonPayload.new,jsonPayload.errors)'
