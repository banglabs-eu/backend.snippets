# Snippets Backend

FastAPI REST API for the Snippets platform. Backs both [`web.snippets`](https://github.com/banglabs-eu/web.snippets) (the React explorer) and [`cli.snippets`](https://github.com/banglabs-eu/cli.snippets) (the input CLI). Postgres via psycopg2 — synchronous, raw SQL, no ORM.

## Quick start

```bash
# Copy and fill in your DATABASE_URL, JWT_SECRET, etc.
cp .env.example .env.dev

# Auto-creates .venv, installs deps, starts uvicorn on http://127.0.0.1:8000
./run-dev.sh

# Need autoreload?
./run-dev.sh --reload
```

The script self-heals a moved/copied venv (it rebuilds when `.venv/pyvenv.cfg` points at a stale path). Schema migrations run on startup — all DDL in `schema.sql` is idempotent and tracked via the `schema_version` table.

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

The Docker route still works (`docker compose up --build`), but day-to-day dev is `./run-dev.sh`.

## Configuration

`main.py` calls `load_dotenv(".env.{APP_ENV}")` at startup (`APP_ENV` defaults to `dev`). See `.env.example` for the full set; the highlights:

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection (`?keepalives=1&keepalives_idle=30…` recommended — see *Connection pool gotcha* below) |
| `JWT_SECRET` | `openssl rand -hex 32`. Required. |
| `JWT_EXPIRY_HOURS` | Default 720 (30 days). |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins. |
| `DEBUG` | `true` enables `/docs` and `/redoc`. |
| `INVITE_ADMIN` | Username allowed to mint invite codes (default `adam`). |
| `GOOGLE_CLIENT_ID` | Required for `/auth/google`. |
| `MAGIC_LINK_BASE_URL`, `MAGIC_LINK_TTL_MINUTES` | Magic-link sign-in. |
| `SMTP_*` | Outbound mail. If `SMTP_HOST` is empty, magic-link URLs are logged to stdout instead of emailed (handy in dev). |
| `DB_POOL_MIN`, `DB_POOL_MAX` | Pool sizing. Defaults 2 / 10. |

`.env.dev` and `.env.prod` are gitignored. `.env.example` is the canonical reference.

## Authentication

Three sign-in flows:

- **Password** — `POST /register` (invite-code gated) + `POST /login`. Rate-limited: 5 failed attempts in 15 min returns 429.
- **Google OAuth** — `POST /auth/google` with an ID token from the Google Sign-In flow. Auto-creates accounts; auto-links to existing ones by email.
- **Magic link** — `POST /auth/magic-link {email}` issues a one-time token (TTL `MAGIC_LINK_TTL_MINUTES`). User clicks the email link → `POST /auth/verify-magic-link {token}` returns a JWT. Always returns `{ok: true}` for the request step to avoid leaking which emails are registered.

JWT goes in `Authorization: Bearer <token>`. `POST /logout` revokes the token's `jti` server-side (the web client calls this on sign-out). All routes except `/health`, `/register`, `/login`, `/auth/google`, `/auth/magic-link`, `/auth/verify-magic-link` require auth.

## API surface

Routes live in `routers/*.py` (split per resource). Run with `DEBUG=true` and visit `/docs` for the live OpenAPI; the high-level grouping:

| Group | Endpoints |
|---|---|
| Auth | `/register`, `/login`, `/logout`, `/me`, `/change-password`, `/auth/google`, `/auth/magic-link`, `/auth/verify-magic-link` |
| Invite codes | `/invite-codes` (admin-only: list + mint) |
| Notes | `/notes` CRUD, `/notes/{id}/body` PATCH, `/notes/{id}/source` PATCH, `/notes/{id}/tags` add/remove, `/notes/sourceless-check`, `/notes/bulk-source`, `/notes/search`, `/notes/tags/batch`, `/notes/export/anki` |
| Sources | `/sources` CRUD/PATCH, `/sources/recent`, `/sources/search`, `/sources/{id}/citation`, `/sources/{id}/authors` |
| Source types | `/source-types` list/create, `/source-types/{id}` |
| Publishers | `/publishers/search`, `/publishers/cities`, `/publishers/get-or-create` |
| Authors | `/authors`, `/authors/recent`, `/authors/search`, `/authors/last-names`, `/authors/first-names`, `/authors/{id}` PATCH/DELETE |
| Tags | `/tags`, `/tags/recent`, `/tags/search`, `/tags/by-name`, `/tags/get-or-create`, `/tags/{id}` GET/DELETE |

Multi-tenancy: nearly every query filters by `user_id`. `source_types` is the deliberate exception (shared globally).

## Anki export

`POST /notes/export/anki` with `{note_ids: [...]}` returns a `.apkg` file (built via `genanki`). Source citations become the card front; note bodies become the back; tags travel with each card.

## Scripts

- `scripts/reset-password.py <username>` — interactive password reset against `.env.{APP_ENV}` (default dev).
- `scripts/clone-prod-to-dev.sh` — destructive prod → dev DB mirror. Connection params override via `SNIPPETS_DB_HOST`, `SNIPPETS_DB_PORT`, `SNIPPETS_DB_USER`, etc.

## Connection pool gotcha

`ThreadedConnectionPool` doesn't health-check connections. After a long idle, a server-side or NAT-side timeout can leave a dead conn in the pool, which then trips `OperationalError: SSL SYSCALL error: EOF detected` on the next query. Mitigate with TCP keepalives in `DATABASE_URL`:

```
?keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=3
```

Restart the backend if you hit it once; keepalives prevent it from coming back.

## Deploying

Production runs on **Scaleway Serverless Containers** at `api.snippets.eu`. Env vars are set in the Scaleway console — `.env.prod` is unused on Scaleway. Build, push, redeploy:

```bash
docker build -t rg.pl-waw.scw.cloud/snippets-backend/api:latest .
docker push  rg.pl-waw.scw.cloud/snippets-backend/api:latest
scw container container deploy <container-id>
```

Full setup: `DEPLOY.md`. Schema migrations run automatically on startup.

## Architecture

`main.py` is just lifespan + middleware + router mounting (~110 lines). The data layer lives in `db.py` (every function takes a psycopg2 conn as its first arg, returns dicts). Route helpers in `deps.py`. Schema in `schema.sql` (currently `schema_version = 11`).

Detailed map for contributors: `CLAUDE.md`.

## License

MIT
