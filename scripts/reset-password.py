#!/usr/bin/env python3
"""Reset a Snippets user's password.

Usage:
    ./scripts/reset-password.py <username>             # prompts for password
    ./scripts/reset-password.py <username> --password X
    APP_ENV=prod ./scripts/reset-password.py alice     # reset against prod env

Loads DB config from .env.<APP_ENV> (default: dev), same as main.py.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / f".env.{os.environ.get('APP_ENV', 'dev')}")

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

import auth  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Reset a Snippets user password.")
    p.add_argument("username")
    p.add_argument("--password", help="New password (omit to be prompted, recommended)")
    p.add_argument("--min-length", type=int, default=6, help="Minimum password length (default: 6)")
    args = p.parse_args()

    password = args.password
    if password is None:
        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("Passwords don't match.", file=sys.stderr)
            return 2

    if len(password) < args.min_length:
        print(f"Password must be at least {args.min_length} characters.", file=sys.stderr)
        return 2

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set. Did .env.{dev,prod,...} load?", file=sys.stderr)
        return 1

    pwd_hash = auth.hash_password(password)
    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE username = %s RETURNING id",
            (pwd_hash, args.username),
        )
        row = cur.fetchone()
        if row is None:
            print(f"No user with username {args.username!r}.", file=sys.stderr)
            conn.rollback()
            return 1
        # Invalidate any active sessions so the old password's tokens don't keep working.
        # We don't have user_id on revoked_tokens; instead, login_attempts cleanup is enough
        # for password tracking. For full session invalidation we'd need a per-user token
        # version — skipping for now.
        cur.execute("DELETE FROM login_attempts WHERE username = %s", (args.username,))
        conn.commit()
        print(f"Password reset for {args.username} (user_id={row['id']}). Cleared {cur.rowcount} login-attempt rows.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
