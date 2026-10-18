#!/usr/bin/env bash
# Deploy Riposte to Cloud Run. Usage: PROJECT=my-project tools/deploy.sh
# One instance (in-memory state), WebSockets up to 60 min, no CPU throttling (streaming), warm start.
set -euo pipefail
: "${PROJECT:?set PROJECT}"; REGION="${REGION:-asia-south1}"; SERVICE="${SERVICE:-riposte}"
cd "$(dirname "$0")/.."
gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
if ! gcloud secrets describe gemini-api-key >/dev/null 2>&1; then
  set -a; . ./.env; set +a
  printf '%s' "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=- --replication-policy=automatic
fi
PN=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding gemini-api-key --member="serviceAccount:${PN}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor >/dev/null
gcloud run deploy "$SERVICE" --source . --region "$REGION" --allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest \
  --timeout 3600 --min-instances 1 --max-instances 1 --concurrency 200 \
  --no-cpu-throttling --cpu-boost --session-affinity --memory 1Gi --cpu 1
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
