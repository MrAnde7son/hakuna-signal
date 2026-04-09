#!/usr/bin/env bash
#
# Deploy hakuna-signal pipeline to prod (Cloud Run Job).
#
# What this does:
#   1. Builds the Docker image via Cloud Build, tagged with the current git
#      short SHA. The SHA tag is immutable — keep it around for rollbacks.
#   2. Moves the `:latest` tag to the new image. The Cloud Run Job pins
#      `:latest` and Terraform ignores image tag drift on purpose
#      (see infra/main.tf), so re-pointing `:latest` IS the deploy.
#   3. With --execute, fires a one-off run of the job as a smoke test.
#      Without it, the next Cloud Scheduler tick picks the new image up.
#
# Resource names use the legacy `reddit-inbound-*` prefix on purpose — see
# infra/variables.tf:18 for why renaming would force destroy/recreate.
#
# Usage:
#   scripts/deploy.sh                 # build + move :latest
#   scripts/deploy.sh --execute       # build + move :latest + smoke test
#   scripts/deploy.sh --dirty         # allow deploying with uncommitted changes
#
# Rollback:
#   gcloud artifacts docker tags add \
#     us-east5-docker.pkg.dev/hakuna-prod-2026/reddit-inbound-images/reddit-inbound:<old-sha> \
#     us-east5-docker.pkg.dev/hakuna-prod-2026/reddit-inbound-images/reddit-inbound:latest
#
# Env overrides (defaults match infra/terraform.tfvars):
#   PROJECT_ID=hakuna-prod-2026
#   REGION=us-east5
#   NAME=reddit-inbound

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-hakuna-prod-2026}"
REGION="${REGION:-us-east5}"
NAME="${NAME:-reddit-inbound}"

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
gcloud builds submit \
  --project "${PROJECT_ID}" \
  --tag "${IMAGE_SHA}" \
  .

echo
echo "==> Moving :latest to ${GIT_SHA}..."
gcloud artifacts docker tags add \
  "${IMAGE_SHA}" "${IMAGE_LATEST}" \
  --project "${PROJECT_ID}"

echo
echo "==> Deployed ${GIT_SHA} → ${IMAGE_LATEST}"
echo "    Cloud Scheduler will pick this up on the next tick."

if [ "$EXECUTE" -eq 1 ]; then
  echo
  echo "==> Triggering smoke-test run of job '${NAME}'..."
  gcloud run jobs execute "${NAME}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --wait
  echo "==> Smoke test complete."
fi
