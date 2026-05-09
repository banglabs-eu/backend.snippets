#!/usr/bin/env bash
# Build and deploy the backend on the Pi, stamping version metadata from git.
# Usage:  ./deploy-pi.sh           (uses ENV=pi by default)
#         ENV=pi-dev ./deploy-pi.sh
set -euo pipefail

cd "$(dirname "$0")"

export GIT_VERSION="$(git describe --tags --always --dirty)"
export GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
export GIT_SHA="$(git rev-parse --short HEAD)"
export BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export ENV="${ENV:-pi}"

echo "Building backend"
echo "  ENV         = $ENV"
echo "  GIT_VERSION = $GIT_VERSION"
echo "  GIT_BRANCH  = $GIT_BRANCH"
echo "  GIT_SHA     = $GIT_SHA"
echo "  BUILT_AT    = $BUILT_AT"

docker compose build
docker compose up -d
