# SnippetsBackend

FastAPI REST backend for [SnippetsCLI](../SnippetsCLI). Connects to PostgreSQL and exposes a JSON API on port 8000.

## Setup

### 1. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```
DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
API_KEY=<run: openssl rand -hex 32>
ALLOWED_ORIGINS=https://your-frontend.com
DEBUG=false
```

- **API_KEY** — all requests must include `Authorization: Bearer <key>`. Leave unset to disable auth (local dev only).
- **ALLOWED_ORIGINS** — comma-separated list of allowed web origins. Leave empty if only serving a CLI.
- **DEBUG** — set to `true` to enable `/docs` and `/redoc`.

### 2. Start with Docker

```bash
docker compose up --build
```

The backend initialises the schema on startup (safe to run against an existing database — all `CREATE TABLE` statements use `IF NOT EXISTS`).

### 3. Verify

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## Running without Docker

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

## Authentication

All endpoints except `/health` require a Bearer token matching your `API_KEY`:

```
Authorization: Bearer <your-api-key>
```

## API

All endpoints return JSON. Dates are ISO 8601 strings.

### Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Returns `{"status":"ok"}` |

### Notes

| Method | Path | Description |
|--------|------|-------------|
| POST | `/notes` | Create a note |
| GET | `/notes` | All notes (filter: `?source_id=`, `?tag_id=`, `?author_id=`) |
| GET | `/notes/{id}` | Get a note |
| PATCH | `/notes/{id}/source` | Update note's source |
| POST | `/notes/sourceless-check` | Return which note IDs (from a given list) have no source |
| POST | `/notes/bulk-source` | Set source on multiple notes at once |
| GET | `/notes/{id}/tags` | Tags on a note |
| POST | `/notes/{id}/tags` | Add a tag to a note |
| DELETE | `/notes/{id}/tags/{tag_id}` | Remove a tag from a note |
| POST | `/notes/tags/batch` | Get tags for multiple notes: `{note_id: [tags]}` |

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

## Files

```
main.py          FastAPI app — routes, Pydantic models, lifespan handler
db.py            Data access layer (psycopg2, all SQL queries)
schema.sql       PostgreSQL DDL + seed data
requirements.txt Python dependencies
Dockerfile
docker-compose.yml
.env.example
```

## License

MIT
