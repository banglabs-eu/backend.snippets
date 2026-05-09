#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-prod}"
env_file=".env.deploy.${ENV}"

if [[ ! -f "$env_file" ]]; then
  echo "Error: $env_file not found" >&2
  exit 1
fi

set -a
source "$env_file"
set +a

echo "Deploying backend ($ENV) → $IMAGE..."
docker build -t "$IMAGE" .
docker push "$IMAGE"
scw container container deploy "$CONTAINER_ID" region="$REGION"
echo "Backend ($ENV) deployed."
