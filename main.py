"""FastAPI app entrypoint: lifespan, middleware, router mounting."""

import os
import time
from contextlib import asynccontextmanager

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import auth
import db
import metrics
from routers import admin as admin_router
from routers import auth as auth_router
from routers import authors as authors_router
from routers import invite_codes as invite_codes_router
from routers import snippets as snippets_router
from routers import posts as posts_router
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
    "/auth/complete-registration",
    "/auth/username-available",
}


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    started = time.perf_counter()
    conn = app.state.pool.getconn()
    request.state.conn = conn
    response = None
    status = 500
    try:
        path = request.url.path
        if path not in _PUBLIC_PATHS and not path.startswith("/public/"):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                response = JSONResponse(status_code=401, content={"detail": "Missing token"})
                status = 401
                return response
            token = header[len("Bearer "):]
            try:
                payload = auth.decode_token(token)
            except jwt.ExpiredSignatureError:
                response = JSONResponse(status_code=401, content={"detail": "Token expired"})
                status = 401
                return response
            except jwt.InvalidTokenError:
                response = JSONResponse(status_code=401, content={"detail": "Invalid token"})
                status = 401
                return response
            if db.is_token_revoked(conn, payload.get("jti", "")):
                response = JSONResponse(status_code=401, content={"detail": "Token revoked"})
                status = 401
                return response
            request.state.user_id = payload["user_id"]
            request.state.username = payload["username"]
            request.state.jti = payload.get("jti", "")

        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        app.state.pool.putconn(conn)
        try:
            duration_ms = (time.perf_counter() - started) * 1000.0
            metrics.record(
                request.url.path,
                request.method,
                status,
                duration_ms,
                getattr(request.state, "user_id", None),
            )
        except Exception:
            # Never let metrics failure break the response.
            pass


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
app.include_router(snippets_router.router)
app.include_router(posts_router.router)
app.include_router(sources_router.router)
app.include_router(source_types_router.router)
app.include_router(publishers_router.router)
app.include_router(authors_router.router)
app.include_router(tags_router.router)
app.include_router(admin_router.router)
