#!/usr/bin/env bash
# Overwrites the dev database with a copy of prod. Destroys all dev data.
set -euo pipefail

# Override any of these via env vars. Defaults match the current shared host.
HOST="${SNIPPETS_DB_HOST:-151.115.15.189}"
PORT="${SNIPPETS_DB_PORT:-18988}"
USER="${SNIPPETS_DB_USER:-abk}"
PROD_DB="${SNIPPETS_PROD_DB:-prod}"
DEV_DB="${SNIPPETS_DEV_DB:-dev}"
DUMP_FILE="${SNIPPETS_DUMP_FILE:-prod.dump}"

echo "==> Verifying connections"
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$PROD_DB" -c "SELECT current_database();"
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DEV_DB"  -c "SELECT current_database();"

read -rp "About to WIPE database '${DEV_DB}' on ${HOST} and replace with '${PROD_DB}'. Type 'yes' to continue: " CONFIRM
[[ "$CONFIRM" == "yes" ]] || { echo "Aborted."; exit 1; }

echo "==> Dumping ${PROD_DB}"
pg_dump -h "$HOST" -p "$PORT" -U "$USER" -d "$PROD_DB" \
  --no-owner --no-privileges -Fc -f "$DUMP_FILE"

echo "==> Restoring into ${DEV_DB} (drops existing objects via --clean)"
pg_restore -h "$HOST" -p "$PORT" -U "$USER" -d "$DEV_DB" \
  --no-owner --no-privileges --clean --if-exists "$DUMP_FILE"

echo "==> Verifying"
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DEV_DB" -c "\dt"

echo "Done. Dump kept at ${DUMP_FILE} (gitignored)."
