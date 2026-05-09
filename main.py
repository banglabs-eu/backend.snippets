"""FastAPI app entrypoint: lifespan, middleware, router mounting."""

import os
from contextlib import asynccontextmanager

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import auth
import db
from routers import auth as auth_router
from routers import authors as authors_router
from routers import invite_codes as invite_codes_router
from routers import notes as notes_router
from routers import publishers as publishers_router
from routers import source_types as source_types_router
from routers import sources as sources_router
from routers import tags as tags_router


load_dotenv(f".env.{os.environ.get('APP_ENV', 'dev')}")


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.environ.get("DATABASE_URL", "postgresql://localhost/snippets")
    pool_min = int(os.environ.get("DB_POOL_MIN", "2"))
    pool_max = int(os.environ.get("DB_POOL_MAX", "10"))
    app.state.pool = db.init_db(database_url, pool_min, pool_max)
    yield
    app.state.pool.closeall()


_debug = os.environ.get("DEBUG", "false").lower() == "true"
_allowed_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]

app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if _debug else None,
    redoc_url="/redoc" if _debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_PUBLIC_PATHS = {
    "/health",
    "/version",
    "/register",
    "/login",
    "/auth/google",
    "/auth/magic-link",
    "/auth/verify-magic-link",
}


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    conn = app.state.pool.getconn()
    request.state.conn = conn
    try:
        if request.url.path not in _PUBLIC_PATHS:
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return JSONResponse(status_code=401, content={"detail": "Missing token"})
            token = header[len("Bearer "):]
            try:
                payload = auth.decode_token(token)
            except jwt.ExpiredSignatureError:
                return JSONResponse(status_code=401, content={"detail": "Token expired"})
            except jwt.InvalidTokenError:
                return JSONResponse(status_code=401, content={"detail": "Invalid token"})
            if db.is_token_revoked(conn, payload.get("jti", "")):
                return JSONResponse(status_code=401, content={"detail": "Token revoked"})
            request.state.user_id = payload["user_id"]
            request.state.username = payload["username"]
            request.state.jti = payload.get("jti", "")

        return await call_next(request)
    finally:
        app.state.pool.putconn(conn)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version(request: Request):
    with request.state.conn.cursor() as cur:
        cur.execute("SELECT MAX(version) AS v FROM schema_version")
        row = cur.fetchone()
        schema_v = row["v"] if row else None
    return {
        "version": os.environ.get("APP_VERSION", "unknown"),
        "branch": os.environ.get("APP_BRANCH", "unknown"),
        "sha": os.environ.get("APP_SHA", "unknown"),
        "built_at": os.environ.get("APP_BUILT_AT", "unknown"),
        "schema_version": schema_v,
    }


# --- Routers ---

app.include_router(auth_router.router)
app.include_router(invite_codes_router.router)
app.include_router(notes_router.router)
app.include_router(sources_router.router)
app.include_router(source_types_router.router)
app.include_router(publishers_router.router)
app.include_router(authors_router.router)
app.include_router(tags_router.router)
