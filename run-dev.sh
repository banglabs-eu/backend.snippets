#!/usr/bin/env bash
# Local dev server. No --reload by default (watchfiles has wedged the supervisor
# under heavy editor activity). Restart manually after backend changes.
# Pass --reload as an argument if you want autoreload for a session.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt

exec uvicorn main:app "$@"
