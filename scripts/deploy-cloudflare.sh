#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="thejumpuniverse"
BRANCH="main"

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "Missing CLOUDFLARE_API_TOKEN."
  echo "Add it to Cursor Cloud Agent Secrets or GitHub Actions secrets, then rerun."
  exit 1
fi

if [[ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  echo "Missing CLOUDFLARE_ACCOUNT_ID."
  echo "Add it to Cursor Cloud Agent Secrets or GitHub Actions secrets, then rerun."
  exit 1
fi

export CLOUDFLARE_API_TOKEN
export CLOUDFLARE_ACCOUNT_ID

echo "Ensuring Cloudflare Pages project exists: ${PROJECT_NAME}"
npx --yes wrangler@3 pages project create "${PROJECT_NAME}" \
  --production-branch "${BRANCH}" 2>/dev/null || true

echo "Deploying static site to Cloudflare Pages..."
npx --yes wrangler@3 pages deploy . \
  --project-name "${PROJECT_NAME}" \
  --branch "${BRANCH}"

echo "Deployed to https://${PROJECT_NAME}.pages.dev"
