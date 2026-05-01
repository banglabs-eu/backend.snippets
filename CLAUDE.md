# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

FastAPI REST backend for the Snippets platform (CLI input + React explorer). PostgreSQL via psycopg2 (synchronous, raw SQL — no ORM). Multi-user with JWT auth, gated by invite codes; supports username/password, Google OAuth, and magic-link sign-in.

## Commands

```bash
# Local dev (no Docker) — creates .venv, installs deps, runs uvicorn (no --reload by default)
./run-dev.sh
./run-dev.sh --reload          # opt in to autoreload for the session

# Dev (Docker, uses .env.dev) — no HTTPS
docker compose up --build

# Prod (Docker + Caddy HTTPS) — edit Caddyfile with your domain first
ENV=prod docker compose up --build

# Health check
curl http://localhost:8000/health

# Mirror prod DB onto dev DB (DESTRUCTIVE — wipes dev). Asks for confirmation.
./scripts/clone-prod-to-dev.sh

# Reset a user's password by hand (requires venv activated)
python scripts/reset-password.py
```

`run-dev.sh` deliberately omits `--reload` — uvicorn's watchfiles reloader has wedged the supervisor under heavy editor activity in the past. Restart manually after backend changes, or pass `--reload` only when you want it for a session.

No test suite. No linter configured.

## Architecture

```
main.py              app, lifespan, JWT middleware, router mounting (only ~110 lines)
auth.py              bcrypt + PyJWT helpers (hash_password, verify_password, create_token, decode_token)
db.py                Data access layer — every fn takes a psycopg2 conn, runs raw SQL, returns dicts
deps.py              Shared route helpers — to_dict/to_list (datetime → ISO), get_conn/get_user_id/get_username
schema.sql           DDL + seeds + idempotent migrations (current schema_version = 11)
anki_export.py       Builds .apkg packages via genanki for /notes/export/anki
email_send.py        SMTP helper for magic-link emails (logs to stdout if SMTP_HOST unset)
routers/
  auth.py            register, login, /auth/google, /auth/magic-link[+verify], /change-password, /me, /logout
  invite_codes.py    Admin-only invite-code CRUD (admin = INVITE_ADMIN env var, default "adam")
  notes.py           Notes CRUD, body/source PATCH, tag attach/detach, search, sourceless-check, bulk-source, /notes/tags/batch, /notes/export/anki
  sources.py         Sources CRUD/PATCH, recent, search, citation, authors-on-source
  source_types.py    Source types (shared across users)
  publishers.py      Publisher search, city search, get-or-create
  authors.py         Authors list/recent/search, last/first name search, PATCH, DELETE
  tags.py            Tags list/recent/search/by-name/get-or-create, DELETE
Caddyfile            Reverse proxy + automatic Let's Encrypt TLS
```

## Key Patterns

- **Connection-per-request**: `lifespan` builds a `ThreadedConnectionPool` on `app.state.pool` (sized by `DB_POOL_MIN`/`DB_POOL_MAX`, defaults 2/10). The HTTP middleware checks out a conn into `request.state.conn` and returns it in a `finally`. Each `db.*` write commits its own transaction. Uvicorn typically runs 4 workers, each with its own pool.
- **Auth middleware (`main.py`)**: All paths require `Authorization: Bearer <token>` *except* `/health`, `/register`, `/login`, `/auth/google`, `/auth/magic-link`, `/auth/verify-magic-link` (the `_PUBLIC_PATHS` set). `OPTIONS` requests bypass auth entirely so CORS preflight works. The decoded payload populates `request.state.user_id`, `username`, and `jti`. Revoked-token check (`db.is_token_revoked`) runs on every authenticated request.
- **Route handlers** pull `conn` / `user_id` / `username` via the helpers in `deps.py`. They never construct DB connections directly.
- **Multi-tenancy**: nearly every `db.*` function takes `user_id` and filters by it. Exceptions: `source_types` (shared globally) and `revoked_tokens` / `login_attempts` (keyed by `jti` / `username`). When adding entities, follow the `user_id` pattern.
- **Auth flows**:
  - *Password*: bcrypt-hashed in `users.password_hash`. `login_attempts` table tracks failed attempts; 5 failures within `LOCKOUT_MINUTES` (15) returns 429.
  - *Google OAuth*: `users.google_id` (nullable, unique). New users created with `password_hash = NULL`; existing users matched by email get auto-linked.
  - *Magic link*: `magic_links` table stores single-use tokens with `expires_at`. `MAGIC_LINK_TTL_MINUTES` (default 10) and `MAGIC_LINK_BASE_URL` (default `http://localhost:5173`). Endpoint always returns `{ok: true}` to avoid leaking which emails are registered.
  - *Logout*: revokes the current `jti` into `revoked_tokens`.
- **Registration is invite-gated**: `POST /register` requires a valid `invite_code`. Codes are created via `POST /invite-codes` by the admin user (username = `INVITE_ADMIN`, default `adam`). If invite consumption fails after user creation, the user is rolled back.
- **Schema migrations**: idempotent ALTER TABLEs and `DO $$ ... IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = N) ... INSERT INTO schema_version VALUES (N) ... END $$`. To add a migration, append a new block, then bump tracked version. **Current version is 11** (locations + dates on sources for things like lectures).
- **Env file selection**: `main.py` calls `load_dotenv(f".env.{APP_ENV}")` (default `dev`). Docker compose sets `APP_ENV` via the `ENV` variable.
- **HTTPS**: Production uses Caddy as a reverse proxy in `docker-compose.yml`. Caddy auto-provisions TLS via Let's Encrypt. Port 8000 is bound to `127.0.0.1` so only Caddy can reach uvicorn.
- **CORS**: `ALLOWED_ORIGINS` env var (comma-separated). `allow_credentials=True` for the browser frontend.

## Database Schema (highlights)

Core tables: `users`, `notes`, `sources`, `source_types`, `source_authors`, `source_publishers`, `tags`, `note_tags`. Sign-in / security: `revoked_tokens` (jti), `login_attempts`, `magic_links`. Registration: `invite_codes`. Sources support `location` and `date` (e.g. for lectures); notes have `updated_at`.

Notes optionally link to a source. Sources have ordered authors (via `source_authors.author_order`), one type, and one publisher. Tags are per-user and many-to-many with notes via `note_tags`. `ON DELETE CASCADE` is set on `source_authors → sources` and `note_tags → notes/tags`, so deleting a source/note/tag cascades cleanly.

## Environment variables

Required:
- `DATABASE_URL` — Postgres connection string
- `JWT_SECRET` — sign/verify key (use `openssl rand -hex 32`)

Common:
- `APP_ENV` (default `dev`) — picks `.env.{APP_ENV}`
- `JWT_EXPIRY_HOURS` (default 720 = 30 days)
- `ALLOWED_ORIGINS` — comma-separated CORS origins
- `DEBUG` — `true` enables `/docs` and `/redoc`
- `DB_POOL_MIN` / `DB_POOL_MAX` (defaults 2 / 10)
- `INVITE_ADMIN` (default `adam`) — username allowed to mint invite codes
- `GOOGLE_CLIENT_ID` — required for `/auth/google`
- `MAGIC_LINK_BASE_URL`, `MAGIC_LINK_TTL_MINUTES` — magic-link config
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` — outbound mail; if `SMTP_HOST` is empty, magic links are logged to stdout instead of sent (handy in dev)
