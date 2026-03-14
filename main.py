"""FastAPI backend for Snippets."""

import asyncio
import json
import logging
import os
import smtplib
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import genanki
import jwt
import psycopg2.errors
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import auth
import db

log = logging.getLogger(__name__)

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


_PUBLIC_PATHS = {"/health", "/register", "/login", "/auth/magic-link", "/auth/verify-magic-link"}


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


def get_conn(request: Request):
    return request.state.conn


def get_user_id(request: Request) -> int:
    return request.state.user_id


# --- Pydantic models ---

class RegisterBody(BaseModel):
    username: str
    password: str
    invite_code: str


class LoginBody(BaseModel):
    username: str
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


class CreateNoteBody(BaseModel):
    body: str
    source_id: int | None = None
    locator_type: str | None = None
    locator_value: str | None = None


class UpdateNoteSourceBody(BaseModel):
    source_id: int


class UpdateNoteBodyRequest(BaseModel):
    body: str


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


class MagicLinkBody(BaseModel):
    email: str


class VerifyMagicLinkBody(BaseModel):
    token: str


# --- Email sending ---

_SMTP_HOST = os.environ.get("SMTP_HOST", "")
_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER = os.environ.get("SMTP_USER", "")
_SMTP_PASS = os.environ.get("SMTP_PASS", "")
_SMTP_FROM = os.environ.get("SMTP_FROM", "noreply@snippets.eu")
_MAGIC_LINK_BASE_URL = os.environ.get("MAGIC_LINK_BASE_URL", "https://web.snippets.eu")

# Basic disposable email domain blocklist
_DISPOSABLE_DOMAINS = {
    "mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com",
    "throwaway.email", "yopmail.com", "trashmail.com", "sharklasers.com",
    "guerrillamail.info", "grr.la", "guerrillamail.biz", "guerrillamail.de",
    "guerrillamail.net", "mailnesia.com", "maildrop.cc", "dispostable.com",
    "temp-mail.org", "fakeinbox.com", "mailcatch.com", "tempail.com",
}


_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

ANKI_MODEL = genanki.Model(
    1607392319,
    'Snippets Flashcard',
    fields=[{'name': 'Question'}, {'name': 'Answer'}],
    templates=[{
        'name': 'Card 1',
        'qfmt': '{{Question}}',
        'afmt': '{{FrontSide}}<hr id="answer">{{Answer}}',
    }],
)


def _is_disposable_email(email: str) -> bool:
    domain = email.lower().strip().split("@")[-1]
    return domain in _DISPOSABLE_DOMAINS


def _send_magic_link_email(email: str, raw_token: str):
    """Send magic link email via SMTP. Falls back to logging if SMTP is not configured."""
    link = f"{_MAGIC_LINK_BASE_URL}/?magic_token={raw_token}"

    if not _SMTP_HOST:
        log.warning("SMTP not configured — magic link for %s: %s", email, link)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Sign in to Snippets"
    msg["From"] = _SMTP_FROM
    msg["To"] = email

    text = f"Sign in to Snippets by clicking this link:\n\n{link}\n\nThis link expires in 10 minutes. If you didn't request this, ignore this email."
    html = f"""\
<html><body style="font-family: Inter, system-ui, sans-serif; color: #e2e8f0; background: #0a0e17; padding: 32px;">
<div style="max-width: 480px; margin: 0 auto;">
<h2 style="color: #fff; font-family: 'Playfair Display', serif;">Sign in to Snippets</h2>
<p>Click the button below to sign in:</p>
<p style="text-align: center; margin: 24px 0;">
  <a href="{link}" style="background: #6366f1; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600;">Sign in</a>
</p>
<p style="font-size: 13px; color: #94a3b8;">This link expires in 10 minutes. If you didn't request this, ignore this email.</p>
</div>
</body></html>"""

    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
        server.starttls()
        server.login(_SMTP_USER, _SMTP_PASS)
        server.send_message(msg)


# --- Health ---

@app.get("/health")
def health():
    return {"status": "ok"}


# --- Auth ---

@app.post("/register")
def register(body: RegisterBody, request: Request):
    conn = get_conn(request)
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="Username required")
    if not db.is_invite_code_valid(conn, body.invite_code):
        raise HTTPException(status_code=400, detail="Invalid or already used invite code")
    existing = db.get_user_by_username(conn, body.username.strip())
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")
    password_hash = auth.hash_password(body.password)
    user = db.create_user(conn, body.username.strip(), password_hash)
    if not db.validate_and_use_invite_code(conn, body.invite_code, user["id"]):
        db.delete_user(conn, user["id"])
        raise HTTPException(status_code=400, detail="Invalid or already used invite code")
    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user_id": user["id"], "username": user["username"]}


@app.post("/login")
def login(body: LoginBody, request: Request):
    conn = get_conn(request)
    username = body.username.strip()
    attempts = db.get_recent_failed_attempts(conn, username)
    if attempts >= db.MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {db.LOCKOUT_MINUTES} minutes.",
        )
    user = db.get_user_by_username(conn, username)
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        db.record_failed_login(conn, username)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    db.clear_failed_attempts(conn, username)
    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user_id": user["id"], "username": user["username"]}


@app.post("/auth/magic-link")
def request_magic_link(body: MagicLinkBody, request: Request):
    conn = get_conn(request)
    email = body.email.lower().strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")
    if _is_disposable_email(email):
        raise HTTPException(status_code=400, detail="Disposable email addresses are not allowed")

    # Rate limit per email
    recent = db.count_recent_magic_links_for_email(conn, email)
    if recent >= db.MAGIC_LINK_RATE_PER_EMAIL:
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")

    # Generate token, hash it, store it
    raw_token = auth.generate_magic_token()
    token_hash = auth.hash_magic_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=db.MAGIC_LINK_EXPIRY_MINUTES)
    db.create_magic_link_token(conn, token_hash, email, expires_at)

    # Send email (or log if SMTP not configured)
    try:
        _send_magic_link_email(email, raw_token)
    except Exception:
        log.exception("Failed to send magic link email to %s", email)
        raise HTTPException(status_code=500, detail="Failed to send email. Try again later.")

    # Always return ok (don't reveal whether email exists)
    return {"ok": True}


@app.post("/auth/verify-magic-link")
def verify_magic_link(body: VerifyMagicLinkBody, request: Request):
    conn = get_conn(request)
    token_hash = auth.hash_magic_token(body.token)
    row = db.get_magic_link_token(conn, token_hash)
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired link")

    # Mark token as used immediately (single-use)
    db.mark_magic_link_used(conn, row["id"])

    email = row["email"]

    # Find or create user by email
    user = db.get_user_by_email(conn, email)
    if user is None:
        # Deferred account creation — account only created after clicking the link
        user = db.create_user_from_email(conn, email)
    elif not user.get("email_verified"):
        db.set_user_email(conn, user["id"], email)

    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user_id": user["id"], "email": email}


@app.post("/logout")
def logout(request: Request):
    conn = get_conn(request)
    db.revoke_token(conn, request.state.jti)
    return {"ok": True}


@app.post("/change-password")
def change_password(body: ChangePasswordBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    username = request.state.username
    attempts = db.get_recent_failed_attempts(conn, username)
    if attempts >= db.MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {db.LOCKOUT_MINUTES} minutes.",
        )
    user = db.get_user_by_id(conn, uid)
    if not auth.verify_password(body.current_password, user["password_hash"]):
        db.record_failed_login(conn, username)
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    db.clear_failed_attempts(conn, username)
    db.update_user_password(conn, uid, auth.hash_password(body.new_password))
    return {"ok": True}


@app.get("/me")
def me(request: Request):
    return {"user_id": get_user_id(request), "username": request.state.username}


# --- Invite Codes ---

_INVITE_ADMIN = os.environ.get("INVITE_ADMIN", "adam")


@app.post("/invite-codes")
def create_invite_code(request: Request):
    if request.state.username != _INVITE_ADMIN:
        raise HTTPException(status_code=403, detail="Only the invite admin can create invite codes")
    conn = get_conn(request)
    code = db.create_invite_code(conn, created_by=get_user_id(request))
    return {"code": code}


@app.get("/invite-codes")
def list_invite_codes(request: Request):
    if request.state.username != _INVITE_ADMIN:
        raise HTTPException(status_code=403, detail="Only the invite admin can view invite codes")
    conn = get_conn(request)
    return to_list(db.get_invite_codes(conn, get_user_id(request)))


# --- Notes ---

@app.post("/notes")
def create_note(body: CreateNoteBody, request: Request):
    conn = get_conn(request)
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
    conn = get_conn(request)
    return db.get_sourceless_notes(conn, body.note_ids, get_user_id(request))


@app.post("/notes/bulk-source")
def bulk_update_note_source(body: BulkSourceBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    db.bulk_update_note_source(conn, body.note_ids, body.source_id, uid)
    return {"ok": True}


@app.post("/notes/tags/batch")
def get_tags_for_notes(body: NoteIdsBody, request: Request):
    conn = get_conn(request)
    result = db.get_tags_for_notes(conn, body.note_ids, get_user_id(request))
    return {str(k): to_list(v) for k, v in result.items()}


@app.get("/notes/search")
def search_notes(request: Request, q: str = Query(default="")):
    conn = get_conn(request)
    if not q.strip():
        return []
    return to_list(db.search_notes(conn, q.strip(), get_user_id(request)))


@app.get("/notes")
def get_notes(
    request: Request,
    source_id: int | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    author_id: int | None = Query(default=None),
):
    conn = get_conn(request)
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
    conn = get_conn(request)
    row = db.get_note(conn, note_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return to_dict(row)


@app.patch("/notes/{note_id}/source")
def update_note_source(note_id: int, body: UpdateNoteSourceBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    db.update_note_source(conn, note_id, body.source_id, uid)
    return {"ok": True}


@app.patch("/notes/{note_id}/body")
def update_note_body(note_id: int, body: UpdateNoteBodyRequest, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    db.update_note_body(conn, note_id, body.body, uid)
    return {"ok": True}


@app.get("/notes/{note_id}/tags")
def get_tags_for_note(note_id: int, request: Request):
    conn = get_conn(request)
    # Verify note belongs to user
    note = db.get_note(conn, note_id, get_user_id(request))
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return to_list(db.get_tags_for_note(conn, note_id))


@app.post("/notes/{note_id}/tags")
def add_tag_to_note(note_id: int, body: AddTagToNoteBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if db.get_tag(conn, body.tag_id, uid) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.add_tag_to_note(conn, note_id, body.tag_id)
    return {"ok": True}


@app.delete("/notes/{note_id}")
def delete_note(note_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    row = db.get_note(conn, note_id, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete_note(conn, note_id, uid)
    return {"ok": True}


@app.delete("/notes/{note_id}/tags/{tag_id}")
def remove_tag_from_note(note_id: int, tag_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if db.get_tag(conn, tag_id, uid) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.remove_tag_from_note(conn, note_id, tag_id)
    return {"ok": True}


# --- Anki Export ---

async def _generate_flashcard(client, note_body: str, tags: str, source: str) -> tuple[str, str] | None:
    prompt = (
        "You are a flashcard generator. Convert the following knowledge snippet into a single Anki flashcard.\n"
        'Return ONLY valid JSON with two fields: "q" (question) and "a" (answer).\n'
        "The question should test recall of the key fact. The answer should be concise.\n"
        "If there are tags, use them as context clues for what domain this fact belongs to.\n\n"
        f"Tags: {tags}\n"
        f"Source: {source}\n\n"
        f"Snippet:\n{note_body}"
    )
    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        data = json.loads(text)
        return (data["q"], data["a"])
    except Exception:
        log.warning("Failed to generate flashcard for note", exc_info=True)
        return None


@app.post("/notes/export/anki")
async def export_anki(body: NoteIdsBody, request: Request):
    if not _ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")

    conn = get_conn(request)
    uid = get_user_id(request)

    notes = db.get_notes_by_ids(conn, body.note_ids, uid)
    if not notes:
        raise HTTPException(status_code=404, detail="No notes found")

    note_ids = [n["id"] for n in notes]
    tags_map = db.get_tags_for_notes(conn, note_ids, uid)

    source_cache: dict[int, str] = {}
    for note in notes:
        sid = note.get("source_id")
        if sid and sid not in source_cache:
            src = db.get_source(conn, sid, uid)
            source_cache[sid] = src["name"] if src else "unknown"

    client = anthropic.AsyncAnthropic(api_key=_ANTHROPIC_API_KEY)

    async def gen(note):
        note_tags = tags_map.get(note["id"], [])
        tag_str = ", ".join(t["name"] for t in note_tags) if note_tags else "none"
        source_str = source_cache.get(note.get("source_id"), "unknown")
        return await _generate_flashcard(client, note["body"], tag_str, source_str)

    flashcards: list[tuple[str, str]] = []
    for i in range(0, len(notes), 10):
        batch = notes[i : i + 10]
        results = await asyncio.gather(*(gen(n) for n in batch))
        flashcards.extend(r for r in results if r is not None)

    if not flashcards:
        raise HTTPException(status_code=400, detail="No flashcards could be generated")

    deck = genanki.Deck(2059400110, 'Snippets Export')
    for q, a in flashcards:
        deck.add_note(genanki.Note(model=ANKI_MODEL, fields=[q, a]))

    tmp = tempfile.NamedTemporaryFile(suffix=".apkg", delete=False)
    tmp.close()
    genanki.Package(deck).write_to_file(tmp.name)

    return FileResponse(
        tmp.name,
        media_type="application/octet-stream",
        filename="snippets.apkg",
        background=BackgroundTask(os.unlink, tmp.name),
    )


# --- Sources ---

@app.post("/sources")
def create_source(body: CreateSourceBody, request: Request):
    conn = get_conn(request)
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
    conn = get_conn(request)
    return to_list(db.get_recent_sources(conn, get_user_id(request)))


@app.get("/sources/search")
def search_sources(request: Request, q: str = Query(default="")):
    conn = get_conn(request)
    return to_list(db.search_sources(conn, q, get_user_id(request)))


@app.get("/sources")
def get_sources(
    request: Request,
    author_last: str | None = Query(default=None),
    author_first: str | None = Query(default=None),
):
    conn = get_conn(request)
    uid = get_user_id(request)
    if author_last is not None and author_first is not None:
        return to_list(db.get_sources_by_author(conn, author_last, author_first, uid))
    return to_list(db.get_all_sources(conn, uid))


@app.get("/sources/{source_id}/citation")
def get_citation(source_id: int, request: Request):
    conn = get_conn(request)
    citation = db.build_citation(conn, source_id, get_user_id(request))
    return {"citation": citation}


@app.get("/sources/{source_id}/authors")
def get_authors_for_source(source_id: int, request: Request):
    conn = get_conn(request)
    # Verify source belongs to user
    src = db.get_source(conn, source_id, get_user_id(request))
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return to_list(db.get_authors_for_source(conn, source_id))


@app.post("/sources/{source_id}/authors")
def add_author(source_id: int, body: AddAuthorBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    src = db.get_source(conn, source_id, uid)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    author_id = db.add_author(conn, source_id, body.first_name, body.last_name, body.order)
    return {"id": author_id}


@app.get("/sources/{source_id}")
def get_source(source_id: int, request: Request):
    conn = get_conn(request)
    row = db.get_source(conn, source_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return to_dict(row)


# --- Source Types ---

@app.get("/source-types")
def get_source_types(request: Request):
    conn = get_conn(request)
    return to_list(db.get_source_types(conn))


@app.post("/source-types")
def create_source_type(body: CreateSourceTypeBody, request: Request):
    conn = get_conn(request)
    try:
        type_id = db.create_source_type(conn, body.name)
        return {"id": type_id}
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail=f"Source type '{body.name}' already exists")


@app.get("/source-types/{type_id}")
def get_source_type(type_id: int, request: Request):
    conn = get_conn(request)
    row = db.get_source_type(conn, type_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source type not found")
    return to_dict(row)


# --- Publishers ---

@app.get("/publishers/search")
def search_publishers(request: Request, q: str = Query(default="")):
    conn = get_conn(request)
    return to_list(db.search_publishers(conn, q, get_user_id(request)))


@app.get("/publishers/cities")
def search_publisher_cities(request: Request, q: str = Query(default="")):
    conn = get_conn(request)
    return db.search_publisher_cities(conn, q, get_user_id(request))


@app.post("/publishers/get-or-create")
def get_or_create_publisher(body: GetOrCreatePublisherBody, request: Request):
    conn = get_conn(request)
    pub_id = db.get_or_create_publisher(conn, body.name, get_user_id(request), body.city)
    return {"id": pub_id}


# --- Authors ---

@app.get("/authors")
def get_all_authors(request: Request):
    conn = get_conn(request)
    return to_list(db.get_all_authors(conn, get_user_id(request)))


@app.get("/authors/recent")
def get_recent_authors(request: Request):
    conn = get_conn(request)
    return to_list(db.get_recent_authors(conn, get_user_id(request)))


@app.get("/authors/search")
def search_authors(request: Request, q: str = Query(default="")):
    conn = get_conn(request)
    return to_list(db.search_authors(conn, q, get_user_id(request)))


@app.get("/authors/last-names")
def search_author_last_names(request: Request, q: str = Query(default="")):
    conn = get_conn(request)
    return db.search_author_last_names(conn, q, get_user_id(request))


@app.get("/authors/first-names")
def search_author_first_names(request: Request, q: str = Query(default="")):
    conn = get_conn(request)
    return db.search_author_first_names(conn, q, get_user_id(request))


# --- Tags ---

@app.get("/tags/recent")
def get_recent_tags(request: Request):
    conn = get_conn(request)
    return to_list(db.get_recent_tags(conn, get_user_id(request)))


@app.get("/tags/search")
def search_tags(request: Request, q: str = Query(default="")):
    conn = get_conn(request)
    return to_list(db.search_tags(conn, q, get_user_id(request)))


@app.get("/tags/by-name")
def get_tag_by_name(request: Request, name: str = Query()):
    conn = get_conn(request)
    row = db.get_tag_by_name(conn, name, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return to_dict(row)


@app.post("/tags/get-or-create")
def get_or_create_tag(body: GetOrCreateTagBody, request: Request):
    conn = get_conn(request)
    tag_id = db.get_or_create_tag(conn, body.name, get_user_id(request))
    return {"id": tag_id}


@app.get("/tags")
def get_all_tags(request: Request):
    conn = get_conn(request)
    return to_list(db.get_all_tags(conn, get_user_id(request)))


@app.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    row = db.get_tag(conn, tag_id, uid)
    if row is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.delete_tag(conn, tag_id, uid)
    return {"ok": True}


@app.get("/tags/{tag_id}")
def get_tag(tag_id: int, request: Request):
    conn = get_conn(request)
    row = db.get_tag(conn, tag_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return to_dict(row)
