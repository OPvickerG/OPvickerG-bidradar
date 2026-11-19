#!/usr/bin/env bash
# OPvickerG BidRadar — Google Cloud deployment (run in Google Cloud Shell from the project root).
#
#   Creates/updates: Artifact Registry repo, container image (Cloud Build), service accounts,
#   Cloud Run service (dashboard), Cloud Run Job (scanner), Cloud Scheduler trigger (:00 and :30 IST).
#   Secrets must already exist in Secret Manager (see DEPLOYMENT.md step 3) — this script never asks
#   for, prints or stores secret values.
#
#   Usage:  PROJECT_ID=my-project ./infrastructure/gcp/deploy.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"                    # Mumbai
REPO="${REPO:-bidradar}"
SERVICE="${SERVICE:-bidradar-web}"
JOB="${JOB:-bidradar-scan}"
SCHED="${SCHED:-bidradar-scan-every-30min}"
EMAIL_PROVIDER="${EMAIL_PROVIDER:-console}"        # switch to resend after the provider is set up
EMAIL_FROM="${EMAIL_FROM:-}"
TAG="$(date +%Y%m%d-%H%M%S)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/bidradar:${TAG}"
WEB_SA="bidradar-web@${PROJECT_ID}.iam.gserviceaccount.com"
JOB_SA="bidradar-scan@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="bidradar-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID" >/dev/null
echo "==> Enabling APIs"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com

echo "==> Checking secrets exist (values are not read)"
REQUIRED=(bidradar-database-url bidradar-session-secret bidradar-admin-password-hash)
for s in "${REQUIRED[@]}"; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || { echo "Missing secret '$s' — see DEPLOYMENT.md step 3"; exit 1; }
done
OPTIONAL_SECRETS=""
gcloud secrets describe bidradar-resend-api-key >/dev/null 2>&1 && OPTIONAL_SECRETS+=",RESEND_API_KEY=bidradar-resend-api-key:latest"
gcloud secrets describe bidradar-resend-webhook-secret >/dev/null 2>&1 && OPTIONAL_SECRETS+=",RESEND_WEBHOOK_SECRET=bidradar-resend-webhook-secret:latest"
if [[ "$EMAIL_PROVIDER" == "resend" && "$OPTIONAL_SECRETS" != *RESEND_API_KEY* ]]; then
  echo "EMAIL_PROVIDER=resend but secret bidradar-resend-api-key is missing"; exit 1
fi

echo "==> Service accounts (least privilege)"
for sa in bidradar-web bidradar-scan bidradar-scheduler; do
  gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$sa" --display-name "BidRadar ${sa#bidradar-}"
done
for s in "${REQUIRED[@]}" bidradar-resend-api-key bidradar-resend-webhook-secret; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || continue
  for sa in "$WEB_SA" "$JOB_SA"; do
    gcloud secrets add-iam-policy-binding "$s" --member "serviceAccount:$sa" \
      --role roles/secretmanager.secretAccessor >/dev/null
  done
done

echo "==> Building image with Cloud Build"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION"
gcloud builds submit --tag "$IMAGE" .

COMMON_SECRETS="DATABASE_URL=bidradar-database-url:latest,SESSION_SECRET=bidradar-session-secret:latest,ADMIN_PASSWORD_HASH=bidradar-admin-password-hash:latest${OPTIONAL_SECRETS}"

echo "==> Deploying dashboard service"
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --service-account "$WEB_SA" \
  --allow-unauthenticated --min-instances 0 --max-instances 2 --cpu 1 --memory 512Mi --concurrency 40 \
  --set-secrets "$COMMON_SECRETS" \
  --set-env-vars "APP_ENV=production,SECURE_COOKIES=true,EMAIL_PROVIDER=${EMAIL_PROVIDER},EMAIL_FROM=${EMAIL_FROM},GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},SCAN_JOB_NAME=${JOB}"
URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "PUBLIC_BASE_URL=${URL}" >/dev/null

echo "==> Deploying scanner job"
JOB_ARGS=(--image "$IMAGE" --region "$REGION" --service-account "$JOB_SA" --tasks 1 --max-retries 1
  --task-timeout 28m --cpu 1 --memory 1Gi --command python --args "-m,scanner.scheduler.run,--trigger,scheduler"
  --set-secrets "$COMMON_SECRETS"
  --set-env-vars "APP_ENV=production,EMAIL_PROVIDER=${EMAIL_PROVIDER},EMAIL_FROM=${EMAIL_FROM},PUBLIC_BASE_URL=${URL}")
if gcloud run jobs describe "$JOB" --region "$REGION" >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" "${JOB_ARGS[@]}"
else
  gcloud run jobs create "$JOB" "${JOB_ARGS[@]}"
fi
# the dashboard's "Scan now" button may start (only) this job
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" --member "serviceAccount:$WEB_SA" \
  --role roles/run.invoker >/dev/null
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" --member "serviceAccount:$WEB_SA" \
  --role roles/run.developer >/dev/null   # needed for container overrides (SCAN_TRIGGER=manual)

echo "==> Scheduler: every 30 minutes at :00 and :30, Asia/Kolkata"
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" --member "serviceAccount:$SCHED_SA" \
  --role roles/run.invoker >/dev/null
RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SCHED_ARGS=(--location "$REGION" --schedule "0,30 * * * *" --time-zone "Asia/Kolkata" --uri "$RUN_URI"
  --http-method POST --oauth-service-account-email "$SCHED_SA" --attempt-deadline 180s
  --max-retry-attempts 2 --min-backoff 30s)
if gcloud scheduler jobs describe "$SCHED" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SCHED" "${SCHED_ARGS[@]}"
else
  gcloud scheduler jobs create http "$SCHED" "${SCHED_ARGS[@]}"
fi

echo
echo "Deployed image ${IMAGE}"
echo "Dashboard: ${URL}"
echo "Next: run one scan now and confirm it in the Monitoring Centre:"
echo "  gcloud run jobs execute ${JOB} --region ${REGION} --wait"
