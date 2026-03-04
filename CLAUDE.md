# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

FastAPI REST backend for SnippetsCLI (and a React web frontend) — a note-taking system with sources, authors, tags, and publishers. Uses PostgreSQL via psycopg2 (synchronous, raw SQL). Multi-user with JWT auth.

## Commands

```bash
# Dev (Docker, uses .env.dev) — no HTTPS
docker compose up --build

# Prod (Docker + Caddy HTTPS) — edit Caddyfile with your domain first
ENV=prod docker compose up --build

# Without Docker
pip install -r requirements.txt
uvicorn main:app --reload

# Health check
curl http://localhost:8000/health
```

No test suite exists yet. No linter is configured.

## Architecture

Three-file backend with no ORM:

- **main.py** — FastAPI app: all routes, Pydantic request models, JWT middleware, lifespan (DB init/teardown). Routes call `db.*` functions and convert results via `to_dict`/`to_list` helpers that serialize datetimes.
- **db.py** — Data access layer: every function takes a `conn` (psycopg2 connection with `RealDictCursor`) as its first arg, runs raw SQL, and returns dicts. Most queries filter by `user_id` for multi-tenancy. `init_db()` runs `schema.sql` on startup.
- **auth.py** — JWT helpers (PyJWT + bcrypt). `JWT_SECRET` and `JWT_EXPIRY_HOURS` from env vars. Tokens carry `user_id`, `username`, and `jti` (UUID for token revocation).
- **schema.sql** — DDL with `IF NOT EXISTS` for idempotent startup. Includes seed data for `source_types` and migration statements. Schema version tracked in `schema_version` table (currently v4).
- **Caddyfile** — Caddy reverse proxy config for automatic HTTPS in production.

## Key Patterns

- **Auth flow**: JWT middleware in `main.py` checks `Authorization: Bearer <token>` on all paths except `/health`, `/register`, `/login`. Authenticated user ID is stored on `request.state.user_id`. Tokens include a `jti` claim; `POST /logout` revokes the current token by storing its `jti` in the `revoked_tokens` table. The middleware checks this table on every authenticated request.
- **CORS**: Configured with `allow_credentials=True` for browser-based React frontend. Origins set via `ALLOWED_ORIGINS` env var (defaults to `localhost:5173,localhost:3000` for dev).
- **HTTPS**: Production uses Caddy as a reverse proxy (in `docker-compose.yml`). Caddy auto-provisions TLS certs via Let's Encrypt. Port 8000 is bound to `127.0.0.1` so only Caddy can reach the backend.
- **Multi-tenancy**: Most tables have a `user_id` column. Almost every `db.*` function filters by `user_id`. Exception: `source_types` are shared across all users.
- **Connection handling**: Single psycopg2 connection stored on `app.state.conn`, created in lifespan. `autocommit = False` — each `db.*` function calls `conn.commit()` after writes.
- **Env file selection**: `main.py` loads `.env.{APP_ENV}` (defaults to `.env.dev`). Docker compose sets this via the `ENV` variable.
- **Schema migrations**: Done inline in `schema.sql` with idempotent ALTER TABLE statements. Bump `schema_version` when adding migrations. Current version: 4.

## Database Schema

Core tables: `users`, `notes`, `sources`, `source_types`, `source_authors`, `source_publishers`, `tags`, `note_tags`, `revoked_tokens`. Notes optionally link to a source; sources have authors (ordered), a type, and a publisher. Tags are per-user and linked to notes via `note_tags` junction table. `revoked_tokens` stores `jti` values of logged-out JWT tokens.
