# SnippetsBackend

FastAPI REST backend for [SnippetsCLI](https://github.com/banglabs-eu/SnippetsCLI). Connects to PostgreSQL and exposes a JSON API on port 8000.

## Setup

### 1. Configure environment

Copy `.env.example` to `.env.dev` (or `.env.prod`) and fill in your values:

```
DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
JWT_SECRET=<run: openssl rand -hex 32>
JWT_EXPIRY_HOURS=720
ALLOWED_ORIGINS=https://your-frontend.com
DEBUG=false
```

- **JWT_SECRET** — secret key used to sign and verify JWT tokens. Must be set.
- **JWT_EXPIRY_HOURS** — token lifetime in hours (default: 720 = 30 days).
- **APP_ENV** — selects which `.env.{APP_ENV}` file to load (default: `dev`). Set to `prod` for production.
- **ALLOWED_ORIGINS** — comma-separated list of allowed web origins. Leave empty if only serving a CLI.
- **DEBUG** — set to `true` to enable `/docs` and `/redoc`.

### 2. Run (local dev — no HTTPS)

```bash
# Docker (uses .env.dev)
docker compose up --build

# Or without Docker
pip install -r requirements.txt
uvicorn main:app --reload
```

The backend is available at `http://localhost:8000`. Schema is initialised on startup (safe to re-run — all DDL uses `IF NOT EXISTS`).

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### 3. Run (production — with HTTPS)

Caddy is included in `docker-compose.yml` and handles TLS certificates automatically via Let's Encrypt.

1. Point your domain's DNS A record to your server's IP.
2. Edit `Caddyfile` — replace `yourdomain.com` with your actual domain.
3. Create `.env.prod` with a real `JWT_SECRET` (`openssl rand -hex 32`), `DEBUG=false`, and your `DATABASE_URL`.
4. Start:

```bash
ENV=prod docker compose up --build
```

Caddy will obtain and auto-renew a TLS certificate. The API is served at `https://yourdomain.com`. Port 8000 is only accessible from within Docker (bound to `127.0.0.1`), so all external traffic goes through HTTPS on port 443.

## Authentication

The app uses JWT-based multi-user authentication. Register a user, then include the returned token in all subsequent requests:

```
Authorization: Bearer <jwt-token>
```

All endpoints except `/health`, `/register`, `/login`, `/auth/magic-link`, and `/auth/verify-magic-link` require a valid JWT.

### Magic link authentication

Users can also sign in without a password via email magic links. Send a POST to `/auth/magic-link` with `{email}`. If SMTP is configured, an email with a one-time link is sent; otherwise the link is logged. The link is verified via `/auth/verify-magic-link`, which returns a JWT. Accounts are created automatically on first use.

## API

All endpoints return JSON. Dates are ISO 8601 strings.

### Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Returns `{"status":"ok"}` |

### Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/register` | No | Create account `{username, password, invite_code}` — returns JWT |
| POST | `/login` | No | Login `{username, password}` — returns JWT |
| POST | `/auth/magic-link` | No | Request magic link email `{email}` |
| POST | `/auth/verify-magic-link` | No | Verify magic link token `{token}` — returns JWT |
| POST | `/logout` | Yes | Revoke the current token |
| POST | `/change-password` | Yes | Change password `{current_password, new_password}` |
| GET | `/me` | Yes | Returns `{user_id, username}` for the current token |

### Notes

| Method | Path | Description |
|--------|------|-------------|
| POST | `/notes` | Create a note |
| GET | `/notes` | All notes (filter: `?source_id=`, `?tag_id=`, `?author_id=`) |
| GET | `/notes/{id}` | Get a note |
| DELETE | `/notes/{id}` | Delete a note |
| PATCH | `/notes/{id}/source` | Update note's source |
| POST | `/notes/sourceless-check` | Return which note IDs (from a given list) have no source |
| POST | `/notes/bulk-source` | Set source on multiple notes at once |
| GET | `/notes/{id}/tags` | Tags on a note |
| POST | `/notes/{id}/tags` | Add a tag to a note |
| DELETE | `/notes/{id}/tags/{tag_id}` | Remove a tag from a note |
| POST | `/notes/tags/batch` | Get tags for multiple notes: `{note_id: [tags]}` |
| POST | `/notes/export/anki` | Export notes as Anki .apkg file `{note_ids: [int]}` |

### Sources

| Method | Path | Description |
|--------|------|-------------|
| POST | `/sources` | Create a source |
| GET | `/sources` | All sources (filter: `?author_last=&author_first=`) |
| GET | `/sources/recent` | Recently used sources |
| GET | `/sources/search?q=` | Search sources by name prefix |
| GET | `/sources/{id}` | Get a source |
| GET | `/sources/{id}/citation` | Build MLA-ish citation string |
| GET | `/sources/{id}/authors` | Authors for a source |
| POST | `/sources/{id}/authors` | Add an author to a source |

### Source Types

| Method | Path | Description |
|--------|------|-------------|
| GET | `/source-types` | All source types |
| POST | `/source-types` | Create a source type (409 on duplicate) |
| GET | `/source-types/{id}` | Get a source type |

### Publishers

| Method | Path | Description |
|--------|------|-------------|
| GET | `/publishers/search?q=` | Search publishers by name prefix |
| GET | `/publishers/cities?q=` | Search publisher cities by prefix |
| POST | `/publishers/get-or-create` | Get existing or create publisher `{name, city}` |

### Authors

| Method | Path | Description |
|--------|------|-------------|
| GET | `/authors` | All authors |
| GET | `/authors/recent` | Recently used authors |
| GET | `/authors/search?q=` | Search by name prefix |
| GET | `/authors/last-names?q=` | Distinct last names matching prefix |
| GET | `/authors/first-names?q=` | Distinct first names matching prefix |

### Tags

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tags` | All tags |
| GET | `/tags/recent` | Recently used tags |
| GET | `/tags/search?q=` | Search by name prefix |
| GET | `/tags/by-name?name=` | Get tag by exact name |
| POST | `/tags/get-or-create` | Get existing or create tag `{name}` |
| GET | `/tags/{id}` | Get a tag |
| DELETE | `/tags/{id}` | Delete a tag |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://localhost/snippets` | PostgreSQL connection string |
| `JWT_SECRET` | `change-me-in-production` | Secret for signing JWTs |
| `JWT_EXPIRY_HOURS` | `720` | Token lifetime (30 days) |
| `ALLOWED_ORIGINS` | (empty) | Comma-separated CORS origins |
| `DEBUG` | `false` | Enables `/docs` and `/redoc` when `true` |
| `DB_POOL_MIN` | `2` | Min connections per worker pool |
| `DB_POOL_MAX` | `10` | Max connections per worker pool |
| `APP_ENV` | `dev` | Selects `.env.{APP_ENV}` file |
| `INVITE_ADMIN` | `adam` | Username allowed to create/view invite codes |
| `SMTP_HOST` | (empty) | SMTP host for magic link emails (logs links if empty) |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | (empty) | SMTP username |
| `SMTP_PASS` | (empty) | SMTP password |
| `SMTP_FROM` | `noreply@snippets.eu` | Sender address for magic link emails |
| `MAGIC_LINK_BASE_URL` | `https://web.snippets.eu` | Frontend URL for magic link redirect |
| `ANTHROPIC_API_KEY` | (empty) | Anthropic API key for AI-powered Anki export |

## Files

```
main.py            FastAPI app — routes, Pydantic models, JWT middleware, lifespan handler
db.py              Data access layer (psycopg2, all SQL queries)
auth.py            JWT helpers (PyJWT + bcrypt) — token creation/verification, password hashing
schema.sql         PostgreSQL DDL + seed data + migrations
Caddyfile          Caddy reverse proxy config (HTTPS)
requirements.txt   Python dependencies
Dockerfile
docker-compose.yml
.env.example
```

## License

MIT
