#!/usr/bin/env bash
# Local dev server. No --reload by default (watchfiles has wedged the supervisor
# under heavy editor activity). Restart manually after backend changes.
# Pass --reload as an argument if you want autoreload for a session.

set -euo pipefail
cd "$(dirname "$0")"

APP_ENV="${APP_ENV:-dev}"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

# Rebuild the venv if it doesn't exist or if it was created at a different path
# (a moved/copied repo leaves pyvenv.cfg pointing at the old location, which makes
# pip drop out of the venv and trip PEP 668 "externally-managed-environment").
EXPECTED_VENV="$(realpath .venv 2>/dev/null || true)"
if [[ ! -d .venv ]] || ! grep -qF "venv ${EXPECTED_VENV:-.venv}" .venv/pyvenv.cfg 2>/dev/null; then
    echo "Creating virtualenv at .venv"
    rm -rf .venv
    python3 -m venv .venv
fi

echo "Activating .venv"
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

echo
echo "  API     http://${HOST}:${PORT}"
echo "  env     .env.${APP_ENV}"
echo "  venv    $(realpath .venv) (python $(python --version 2>&1 | awk '{print $2}'))"
echo "  reload  $([[ " $* " == *" --reload "* ]] && echo "on" || echo "off (pass --reload to enable)")"
echo

export APP_ENV
exec uvicorn main:app --host "$HOST" --port "$PORT" "$@"
