"""FastAPI backend for Snippets."""

import os
from contextlib import asynccontextmanager
from datetime import datetime

import jwt
import psycopg2.errors
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
import db

load_dotenv(f".env.{os.environ.get('APP_ENV', 'dev')}")


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.environ.get("DATABASE_URL", "postgresql://localhost/snippets")
    app.state.conn = db.init_db(database_url)
    yield
    app.state.conn.close()


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


_PUBLIC_PATHS = {"/health", "/register", "/login"}


@app.middleware("http")
async def jwt_middleware(request: Request, call_next):
    if request.url.path in _PUBLIC_PATHS or request.method == "OPTIONS":
        return await call_next(request)
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
    conn = app.state.conn
    if db.is_token_revoked(conn, payload.get("jti", "")):
        return JSONResponse(status_code=401, content={"detail": "Token revoked"})
    request.state.user_id = payload["user_id"]
    request.state.username = payload["username"]
    request.state.jti = payload.get("jti", "")
    return await call_next(request)


# --- Helpers ---

def to_dict(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def to_list(rows) -> list[dict]:
    return [to_dict(r) for r in rows]


def get_conn():
    return app.state.conn


def get_user_id(request: Request) -> int:
    return request.state.user_id


# --- Pydantic models ---

class RegisterBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


class CreateNoteBody(BaseModel):
    body: str
    source_id: int | None = None
    locator_type: str | None = None
    locator_value: str | None = None


class UpdateNoteSourceBody(BaseModel):
    source_id: int


class NoteIdsBody(BaseModel):
    note_ids: list[int]


class BulkSourceBody(BaseModel):
    note_ids: list[int]
    source_id: int


class AddTagToNoteBody(BaseModel):
    tag_id: int


class CreateSourceBody(BaseModel):
    name: str
    source_type_id: int | None = None
    year: str | None = None
    url: str | None = None
    accessed_date: str | None = None
    edition: str | None = None
    pages: str | None = None
    extra_notes: str | None = None
    publisher_id: int | None = None


class AddAuthorBody(BaseModel):
    first_name: str
    last_name: str
    order: int


class CreateSourceTypeBody(BaseModel):
    name: str


class GetOrCreatePublisherBody(BaseModel):
    name: str
    city: str | None = None


class GetOrCreateTagBody(BaseModel):
    name: str


# --- Health ---

@app.get("/health")
def health():
    return {"status": "ok"}


# --- Auth ---

@app.post("/register")
def register(body: RegisterBody):
    conn = get_conn()
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="Username required")
    existing = db.get_user_by_username(conn, body.username.strip())
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")
    password_hash = auth.hash_password(body.password)
    user = db.create_user(conn, body.username.strip(), password_hash)
    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user_id": user["id"], "username": user["username"]}


@app.post("/login")
def login(body: LoginBody):
    conn = get_conn()
    user = db.get_user_by_username(conn, body.username.strip())
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user_id": user["id"], "username": user["username"]}


@app.post("/logout")
def logout(request: Request):
    conn = get_conn()
    db.revoke_token(conn, request.state.jti)
    return {"ok": True}


@app.get("/me")
def me(request: Request):
    return {"user_id": get_user_id(request), "username": request.state.username}


# --- Notes ---

@app.post("/notes")
def create_note(body: CreateNoteBody, request: Request):
    conn = get_conn()
    uid = get_user_id(request)
    if body.source_id is not None:
        if db.get_source(conn, body.source_id, uid) is None:
            raise HTTPException(status_code=404, detail="Source not found")
    note_id = db.create_note(
        conn, body.body, uid,
        source_id=body.source_id,
        locator_type=body.locator_type,
        locator_value=body.locator_value,
    )
    return {"id": note_id}


@app.post("/notes/sourceless-check")
def get_sourceless_notes(body: NoteIdsBody, request: Request):
    conn = get_conn()
    return db.get_sourceless_notes(conn, body.note_ids, get_user_id(request))


@app.post("/notes/bulk-source")
def bulk_update_note_source(body: BulkSourceBody, request: Request):
    conn = get_conn()
    uid = get_user_id(request)
    if db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    db.bulk_update_note_source(conn, body.note_ids, body.source_id, uid)
    return {"ok": True}


@app.post("/notes/tags/batch")
def get_tags_for_notes(body: NoteIdsBody, request: Request):
    conn = get_conn()
    result = db.get_tags_for_notes(conn, body.note_ids, get_user_id(request))
    return {str(k): to_list(v) for k, v in result.items()}


@app.get("/notes")
def get_notes(
    request: Request,
    source_id: int | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    author_id: int | None = Query(default=None),
):
    conn = get_conn()
    uid = get_user_id(request)
    if source_id is not None:
        return to_list(db.get_notes_by_source(conn, source_id, uid))
    if tag_id is not None:
        return to_list(db.get_notes_by_tag(conn, tag_id, uid))
    if author_id is not None:
        return to_list(db.get_notes_by_author(conn, author_id, uid))
    return to_list(db.get_all_notes(conn, uid))


@app.get("/notes/{note_id}")
def get_note(note_id: int, request: Request):
    conn = get_conn()
    row = db.get_note(conn, note_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return to_dict(row)


@app.patch("/notes/{note_id}/source")
def update_note_source(note_id: int, body: UpdateNoteSourceBody, request: Request):
    conn = get_conn()
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    db.update_note_source(conn, note_id, body.source_id, uid)
    return {"ok": True}


@app.get("/notes/{note_id}/tags")
def get_tags_for_note(note_id: int, request: Request):
    conn = get_conn()
    # Verify note belongs to user
    note = db.get_note(conn, note_id, get_user_id(request))
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return to_list(db.get_tags_for_note(conn, note_id))


@app.post("/notes/{note_id}/tags")
def add_tag_to_note(note_id: int, body: AddTagToNoteBody, request: Request):
    conn = get_conn()
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if db.get_tag(conn, body.tag_id, uid) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.add_tag_to_note(conn, note_id, body.tag_id)
    return {"ok": True}


@app.delete("/notes/{note_id}")
def delete_note(note_id: int, request: Request):
    conn = get_conn()
    uid = get_user_id(request)
    row = db.get_note(conn, note_id, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete_note(conn, note_id, uid)
    return {"ok": True}


@app.delete("/notes/{note_id}/tags/{tag_id}")
def remove_tag_from_note(note_id: int, tag_id: int, request: Request):
    conn = get_conn()
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if db.get_tag(conn, tag_id, uid) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.remove_tag_from_note(conn, note_id, tag_id)
    return {"ok": True}


# --- Sources ---

@app.post("/sources")
def create_source(body: CreateSourceBody, request: Request):
    conn = get_conn()
    uid = get_user_id(request)
    if body.publisher_id is not None:
        if db.get_publisher(conn, body.publisher_id, uid) is None:
            raise HTTPException(status_code=404, detail="Publisher not found")
    source_id = db.create_source(
        conn, body.name, uid,
        source_type_id=body.source_type_id,
        year=body.year,
        url=body.url,
        accessed_date=body.accessed_date,
        edition=body.edition,
        pages=body.pages,
        extra_notes=body.extra_notes,
        publisher_id=body.publisher_id,
    )
    return {"id": source_id}


@app.get("/sources/recent")
def get_recent_sources(request: Request):
    conn = get_conn()
    return to_list(db.get_recent_sources(conn, get_user_id(request)))


@app.get("/sources/search")
def search_sources(request: Request, q: str = Query(default="")):
    conn = get_conn()
    return to_list(db.search_sources(conn, q, get_user_id(request)))


@app.get("/sources")
def get_sources(
    request: Request,
    author_last: str | None = Query(default=None),
    author_first: str | None = Query(default=None),
):
    conn = get_conn()
    uid = get_user_id(request)
    if author_last is not None and author_first is not None:
        return to_list(db.get_sources_by_author(conn, author_last, author_first, uid))
    return to_list(db.get_all_sources(conn, uid))


@app.get("/sources/{source_id}/citation")
def get_citation(source_id: int, request: Request):
    conn = get_conn()
    citation = db.build_citation(conn, source_id, get_user_id(request))
    return {"citation": citation}


@app.get("/sources/{source_id}/authors")
def get_authors_for_source(source_id: int, request: Request):
    conn = get_conn()
    # Verify source belongs to user
    src = db.get_source(conn, source_id, get_user_id(request))
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return to_list(db.get_authors_for_source(conn, source_id))


@app.post("/sources/{source_id}/authors")
def add_author(source_id: int, body: AddAuthorBody, request: Request):
    conn = get_conn()
    uid = get_user_id(request)
    src = db.get_source(conn, source_id, uid)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    author_id = db.add_author(conn, source_id, body.first_name, body.last_name, body.order)
    return {"id": author_id}


@app.get("/sources/{source_id}")
def get_source(source_id: int, request: Request):
    conn = get_conn()
    row = db.get_source(conn, source_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return to_dict(row)


# --- Source Types ---

@app.get("/source-types")
def get_source_types(request: Request):
    conn = get_conn()
    return to_list(db.get_source_types(conn))


@app.post("/source-types")
def create_source_type(body: CreateSourceTypeBody, request: Request):
    conn = get_conn()
    try:
        type_id = db.create_source_type(conn, body.name)
        return {"id": type_id}
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail=f"Source type '{body.name}' already exists")


@app.get("/source-types/{type_id}")
def get_source_type(type_id: int, request: Request):
    conn = get_conn()
    row = db.get_source_type(conn, type_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source type not found")
    return to_dict(row)


# --- Publishers ---

@app.get("/publishers/search")
def search_publishers(request: Request, q: str = Query(default="")):
    conn = get_conn()
    return to_list(db.search_publishers(conn, q, get_user_id(request)))


@app.get("/publishers/cities")
def search_publisher_cities(request: Request, q: str = Query(default="")):
    conn = get_conn()
    return db.search_publisher_cities(conn, q, get_user_id(request))


@app.post("/publishers/get-or-create")
def get_or_create_publisher(body: GetOrCreatePublisherBody, request: Request):
    conn = get_conn()
    pub_id = db.get_or_create_publisher(conn, body.name, get_user_id(request), body.city)
    return {"id": pub_id}


# --- Authors ---

@app.get("/authors")
def get_all_authors(request: Request):
    conn = get_conn()
    return to_list(db.get_all_authors(conn, get_user_id(request)))


@app.get("/authors/recent")
def get_recent_authors(request: Request):
    conn = get_conn()
    return to_list(db.get_recent_authors(conn, get_user_id(request)))


@app.get("/authors/search")
def search_authors(request: Request, q: str = Query(default="")):
    conn = get_conn()
    return to_list(db.search_authors(conn, q, get_user_id(request)))


@app.get("/authors/last-names")
def search_author_last_names(request: Request, q: str = Query(default="")):
    conn = get_conn()
    return db.search_author_last_names(conn, q, get_user_id(request))


@app.get("/authors/first-names")
def search_author_first_names(request: Request, q: str = Query(default="")):
    conn = get_conn()
    return db.search_author_first_names(conn, q, get_user_id(request))


# --- Tags ---

@app.get("/tags/recent")
def get_recent_tags(request: Request):
    conn = get_conn()
    return to_list(db.get_recent_tags(conn, get_user_id(request)))


@app.get("/tags/search")
def search_tags(request: Request, q: str = Query(default="")):
    conn = get_conn()
    return to_list(db.search_tags(conn, q, get_user_id(request)))


@app.get("/tags/by-name")
def get_tag_by_name(request: Request, name: str = Query()):
    conn = get_conn()
    row = db.get_tag_by_name(conn, name, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return to_dict(row)


@app.post("/tags/get-or-create")
def get_or_create_tag(body: GetOrCreateTagBody, request: Request):
    conn = get_conn()
    tag_id = db.get_or_create_tag(conn, body.name, get_user_id(request))
    return {"id": tag_id}


@app.get("/tags")
def get_all_tags(request: Request):
    conn = get_conn()
    return to_list(db.get_all_tags(conn, get_user_id(request)))


@app.get("/tags/{tag_id}")
def get_tag(tag_id: int, request: Request):
    conn = get_conn()
    row = db.get_tag(conn, tag_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return to_dict(row)
