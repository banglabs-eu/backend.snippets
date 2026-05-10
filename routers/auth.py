"""Auth endpoints: register, login, Google OAuth, magic link, password change, me, logout."""

import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, EmailStr

import auth
import db
import email_send
from deps import get_conn, get_user_id, get_username


router = APIRouter()
log = logging.getLogger(__name__)


_GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
_MAGIC_LINK_BASE_URL = os.environ.get("MAGIC_LINK_BASE_URL", "http://localhost:5173").rstrip("/")
_MAGIC_LINK_TTL_MINUTES = int(os.environ.get("MAGIC_LINK_TTL_MINUTES", "10"))


# --- Models ---

class RegisterBody(BaseModel):
    username: str
    password: str
    invite_code: str


class LoginBody(BaseModel):
    username: str
    password: str


class GoogleAuthBody(BaseModel):
    token: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


class SetPasswordBody(BaseModel):
    password: str


class MagicLinkRequestBody(BaseModel):
    email: EmailStr


class MagicLinkVerifyBody(BaseModel):
    token: str


class CompleteRegistrationBody(BaseModel):
    registration_token: str
    username: str


# Username rules — kept here (not in db.py) so they're easy to surface in the UI hint.
_USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{1,18}[a-z0-9])?$")
_USERNAME_HINT = "3–20 chars, lowercase letters/digits, dashes/underscores allowed (not at the ends)."


def _validate_username(username: str) -> str:
    """Normalize + validate; raise 400 with a helpful detail if it doesn't fit the rules."""
    candidate = username.strip().lower()
    if not _USERNAME_RE.match(candidate):
        raise HTTPException(status_code=400, detail=_USERNAME_HINT)
    return candidate


# --- Endpoints ---

@router.post("/register")
def register(body: RegisterBody, request: Request):
    conn = get_conn(request)
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="Username required")
    if not db.is_invite_code_valid(conn, body.invite_code):
        raise HTTPException(status_code=400, detail="Invalid or already used invite code")
    if db.get_user_by_username(conn, body.username.strip()):
        raise HTTPException(status_code=409, detail="Username already taken")
    password_hash = auth.hash_password(body.password)
    user = db.create_user(conn, body.username.strip(), password_hash)
    if not db.validate_and_use_invite_code(conn, body.invite_code, user["id"]):
        db.delete_user(conn, user["id"])
        raise HTTPException(status_code=400, detail="Invalid or already used invite code")
    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user_id": user["id"], "username": user["username"]}


@router.post("/login")
def login(body: LoginBody, request: Request):
    conn = get_conn(request)
    username = body.username.strip()
    if db.get_recent_failed_attempts(conn, username) >= db.MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {db.LOCKOUT_MINUTES} minutes.",
        )
    user = db.get_user_by_username(conn, username)
    if not user or not user["password_hash"] or not auth.verify_password(body.password, user["password_hash"]):
        db.record_failed_login(conn, username)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    db.clear_failed_attempts(conn, username)
    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user_id": user["id"], "username": user["username"]}


@router.post("/auth/google")
def google_login(body: GoogleAuthBody, request: Request):
    if not _GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    conn = get_conn(request)
    try:
        idinfo = google_id_token.verify_oauth2_token(
            body.token, google_requests.Request(), _GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Google token")
    google_id = idinfo["sub"]
    email = idinfo.get("email")
    name = idinfo.get("name", "")

    user = db.get_user_by_google_id(conn, google_id)
    if user:
        token = auth.create_token(user["id"], user["username"])
        return {"token": token, "user_id": user["id"], "username": user["username"]}

    if email:
        existing = db.get_user_by_username(conn, email)
        if existing:
            db.link_google_account(conn, existing["id"], google_id, email)
            token = auth.create_token(existing["id"], existing["username"])
            return {"token": token, "user_id": existing["id"], "username": existing["username"]}

    username = email or name or f"google_{google_id[:8]}"
    base_username = username
    suffix = 1
    while db.get_user_by_username(conn, username):
        username = f"{base_username}_{suffix}"
        suffix += 1
    user = db.create_google_user(conn, username, google_id, email)
    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user_id": user["id"], "username": user["username"]}


@router.post("/auth/magic-link")
def request_magic_link(body: MagicLinkRequestBody, request: Request):
    """Issue a magic-link email — for sign-in *or* sign-up.

    If a user matches the email, the link signs them in.
    If none does, the link carries a "registration intent" (user_id=NULL); the
    verify step will hand the frontend a registration_token to finish onboarding.

    Always returns 200 regardless to avoid leaking which emails are registered.
    """
    conn = get_conn(request)
    email = body.email.lower().strip()
    user = db.get_user_by_email(conn, email)

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_MAGIC_LINK_TTL_MINUTES)
    db.create_magic_link(conn, user["id"] if user else None, email, token, expires_at)
    link = f"{_MAGIC_LINK_BASE_URL}/auth/verify?token={token}"
    try:
        email_send.send_magic_link(email, link)
    except Exception:
        log.exception("failed to send magic link email")
        # Don't reveal mail-server failures to the caller.
    return {"ok": True}


@router.post("/auth/verify-magic-link")
def verify_magic_link(body: MagicLinkVerifyBody, request: Request):
    """Consume a magic link. Discriminated response:

    - sign-in (link points at an existing user)
        → { kind: 'sign_in', token, user_id, username, email }
    - register (link is a registration intent)
        → { kind: 'register', email, registration_token }
          The frontend then prompts for a username and POSTs to
          /auth/complete-registration with that registration_token.
    """
    conn = get_conn(request)
    link = db.consume_magic_link(conn, body.token.strip())
    if not link:
        raise HTTPException(status_code=401, detail="Link is invalid, expired, or already used")

    if link["user_id"] is None:
        # Registration intent: mint a fresh short-lived token the frontend will
        # send back along with the chosen username. Reuse magic_links as the
        # store — same TTL, same uniqueness, same expiry semantics.
        reg_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_MAGIC_LINK_TTL_MINUTES)
        db.create_magic_link(conn, None, link["email"], reg_token, expires_at)
        return {"kind": "register", "email": link["email"], "registration_token": reg_token}

    user = db.get_user_by_id(conn, link["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    token = auth.create_token(user["id"], user["username"])
    return {
        "kind": "sign_in",
        "token": token,
        "user_id": user["id"],
        "username": user["username"],
        "email": link["email"],
    }


@router.post("/auth/complete-registration")
def complete_registration(body: CompleteRegistrationBody, request: Request):
    """Finish the email-verified signup. Consumes the registration_token,
    creates the user with the requested username, returns a JWT."""
    conn = get_conn(request)
    username = _validate_username(body.username)

    link = db.consume_magic_link(conn, body.registration_token.strip())
    if not link or link["user_id"] is not None:
        raise HTTPException(status_code=401, detail="Registration link is invalid, expired, or already used")
    if db.get_user_by_email(conn, link["email"]):
        raise HTTPException(status_code=409, detail="An account already exists for that email")
    if db.get_user_by_username(conn, username):
        raise HTTPException(status_code=409, detail="That username is already taken")

    user = db.create_user_passwordless(conn, username, link["email"])
    token = auth.create_token(user["id"], user["username"])
    return {"token": token, "user_id": user["id"], "username": user["username"]}


@router.get("/auth/username-available")
def username_available(u: str = Query(default=""), request: Request = None):  # type: ignore[assignment]
    """Cheap availability + format check the signup form polls while the user types."""
    if not u:
        return {"available": False, "reason": "empty"}
    candidate = u.strip().lower()
    if not _USERNAME_RE.match(candidate):
        return {"available": False, "reason": "invalid_format", "hint": _USERNAME_HINT}
    conn = get_conn(request)
    if db.get_user_by_username(conn, candidate):
        return {"available": False, "reason": "taken"}
    return {"available": True}


@router.post("/logout")
def logout(request: Request):
    conn = get_conn(request)
    db.revoke_token(conn, request.state.jti)
    return {"ok": True}


@router.post("/auth/set-password")
def set_password(body: SetPasswordBody, request: Request):
    """Set an initial password on a passwordless account.

    Used after magic-link signup to give the user a way to sign in without
    going through email next time. Fails if the account already has a password
    — those go through /change-password.
    """
    conn = get_conn(request)
    uid = get_user_id(request)
    user = db.get_user_by_id(conn, uid)
    if user["password_hash"]:
        raise HTTPException(status_code=400, detail="Password already set. Use change-password to update it.")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    db.update_user_password(conn, uid, auth.hash_password(body.password))
    return {"ok": True}


@router.post("/change-password")
def change_password(body: ChangePasswordBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    username = get_username(request)
    if db.get_recent_failed_attempts(conn, username) >= db.MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {db.LOCKOUT_MINUTES} minutes.",
        )
    user = db.get_user_by_id(conn, uid)
    if not user["password_hash"]:
        raise HTTPException(status_code=400, detail="Account uses Google login. Set a password first.")
    if not auth.verify_password(body.current_password, user["password_hash"]):
        db.record_failed_login(conn, username)
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    db.clear_failed_attempts(conn, username)
    db.update_user_password(conn, uid, auth.hash_password(body.new_password))
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    username = get_username(request)
    is_admin = username == os.environ.get("INVITE_ADMIN", "adam")
    return {"user_id": get_user_id(request), "username": username, "is_admin": is_admin}
