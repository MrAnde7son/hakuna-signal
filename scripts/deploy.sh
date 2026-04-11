#!/usr/bin/env bash
#
# Deploy hakuna-signal pipeline to prod (Cloud Run Job).
#
# What this does:
#   1. Builds the Docker image via Cloud Build, tagged with the current git
#      short SHA. The SHA tag is immutable — keep it around for rollbacks.
#   2. Moves the `:latest` tag to the new image. The Cloud Run Job picks
#      `:latest` up on each execution, and Terraform ignores image tag drift
#      on purpose (see infra/main.tf), so re-pointing `:latest` IS the
#      pipeline-side deploy.
#   3. Uploads the static frontend files (web/dashboard.html, web/favicon.svg)
#      directly to the dashboard GCS bucket — and also mirrors dashboard.html
#      into the state bucket under reports/. The static template SHOULD ship
#      in lockstep with code, but the pipeline's mtime-based sync logic in
#      src/report.py is broken in the cloud-hydrate path (the hydrated copy
#      always wins by mtime, and `generate_report` early-returns when there
#      are no new opportunities and never calls _sync_dashboard at all). The
#      state-bucket mirror is required so the next pipeline hydrate doesn't
#      bring back the stale copy and clobber what we just uploaded.
#   4. Deploys a new revision of the dashboard Cloud Run *service* pinned to
#      the SHA tag. Cloud Run services resolve image tags at revision-creation
#      time, so re-aliasing :latest in Artifact Registry does NOT roll the
#      service — you have to explicitly deploy a new revision. Pinning to the
#      SHA (rather than :latest) makes Cloud Run's revision history line up
#      cleanly with git history for rollbacks.
#   5. With --execute, fires a one-off run of the Job as a smoke test.
#      Without it, the next Cloud Scheduler tick picks the new image up.
#
# Resource names use the legacy `reddit-inbound-*` prefix on purpose — see
# infra/variables.tf:18 for why renaming would force destroy/recreate.
#
# Usage:
#   scripts/deploy.sh                 # build + move :latest + deploy dashboard
#   scripts/deploy.sh --execute       # ... and smoke-test the Job
#   scripts/deploy.sh --dirty         # allow deploying with uncommitted changes
#
# Rollback:
#   # 1. Re-alias :latest so the next Job run uses the old code:
#   gcloud artifacts docker tags add \
#     us-east5-docker.pkg.dev/hakuna-prod-2026/reddit-inbound-images/reddit-inbound:<old-sha> \
#     us-east5-docker.pkg.dev/hakuna-prod-2026/reddit-inbound-images/reddit-inbound:latest
#   # 2. Roll the dashboard service back to its previous revision:
#   gcloud run services update-traffic reddit-inbound-dashboard \
#     --region us-east5 --to-revisions <previous-revision>=100
#
# Env overrides (defaults match infra/terraform.tfvars):
#   PROJECT_ID=hakuna-prod-2026
#   REGION=us-east5
#   NAME=reddit-inbound

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-hakuna-prod-2026}"
REGION="${REGION:-us-east5}"
NAME="${NAME:-reddit-inbound}"
DASHBOARD_BUCKET="${DASHBOARD_BUCKET:-${PROJECT_ID}-${NAME}-dashboard}"
STATE_BUCKET="${STATE_BUCKET:-${PROJECT_ID}-${NAME}-state}"

EXECUTE=0
ALLOW_DIRTY=0
for arg in "$@"; do
  case "$arg" in
    --execute) EXECUTE=1 ;;
    --dirty)   ALLOW_DIRTY=1 ;;
    -h|--help)
      grep -E '^# ?' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown arg: $arg" >&2
      echo "see: $0 --help" >&2
      exit 1
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "error: gcloud not found in PATH" >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ] && [ "$ALLOW_DIRTY" -eq 0 ]; then
  echo "error: working tree has uncommitted changes. commit, stash, or pass --dirty." >&2
  git status --short >&2
  exit 1
fi

GIT_SHA="$(git rev-parse --short HEAD)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${NAME}-images/${NAME}"
IMAGE_SHA="${IMAGE_BASE}:${GIT_SHA}"
IMAGE_LATEST="${IMAGE_BASE}:latest"

echo "==> Deploying ${NAME}"
echo "    project : ${PROJECT_ID}"
echo "    region  : ${REGION}"
echo "    branch  : ${BRANCH}"
echo "    sha     : ${GIT_SHA}"
echo "    image   : ${IMAGE_SHA}"
echo

echo "==> Building image via Cloud Build..."
# --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET: Cloud Build's default
# global logs bucket requires project Viewer to stream logs, which the
# github-deploy SA intentionally doesn't have. Regional user-owned buckets
# live in the project and are readable via the SA's existing storage perms.
gcloud builds submit \
  --project "${PROJECT_ID}" \
  --tag "${IMAGE_SHA}" \
  --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET \
  --region "${REGION}" \
  .

echo
echo "==> Moving :latest to ${GIT_SHA}..."
gcloud artifacts docker tags add \
  "${IMAGE_SHA}" "${IMAGE_LATEST}" \
  --project "${PROJECT_ID}"

echo
echo "==> Pipeline image is live: ${GIT_SHA} → :latest"
echo "    Cloud Scheduler will run the new code on the next tick."

# Sync the static frontend. See header docs for why this is the deploy script's
# responsibility and not the pipeline's. Both buckets must be updated:
#   - dashboard bucket: served by the Cloud Run service (live immediately)
#   - state bucket reports/: prevents the next pipeline hydrate from clobbering
#     the dashboard bucket on the next tick.
if ! command -v gsutil >/dev/null 2>&1; then
  echo "error: gsutil not found in PATH (needed to sync static frontend)" >&2
  exit 1
fi
echo
echo "==> Syncing static frontend to gs://${DASHBOARD_BUCKET}/ ..."
gsutil -h "Cache-Control:private, max-age=60" \
  cp web/dashboard.html web/favicon.svg "gs://${DASHBOARD_BUCKET}/"
echo "==> Mirroring dashboard.html into gs://${STATE_BUCKET}/reports/ ..."
gsutil -h "Cache-Control:private, max-age=60" \
  cp web/dashboard.html "gs://${STATE_BUCKET}/reports/dashboard.html"

# Cloud Run services resolve image tags to digests at revision-creation time,
# so re-aliasing :latest above does nothing for the dashboard. We have to roll
# a new revision explicitly. Pinning to the SHA (not :latest) makes Cloud Run's
# revision history mirror git history.
echo
echo "==> Rolling dashboard service '${NAME}-dashboard' to ${GIT_SHA}..."
gcloud run deploy "${NAME}-dashboard" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_SHA}" \
  --quiet
echo "==> Dashboard service rolled."

if [ "$EXECUTE" -eq 1 ]; then
  echo
  echo "==> Triggering smoke-test run of job '${NAME}'..."
  gcloud run jobs execute "${NAME}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --wait
  echo "==> Smoke test complete."
fi
