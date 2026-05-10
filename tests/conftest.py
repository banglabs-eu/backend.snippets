"""pytest fixtures for backend integration tests.

Uses a dedicated `snippets_test` Postgres database. Each session creates the
schema fresh; each test truncates the data tables so tests stay isolated.

Set TEST_DATABASE_URL in the environment to override the default. Default points
at the same Postgres instance as dev (port 5433) but a separate database.
"""

import os
from urllib.parse import urlparse, urlunparse

import psycopg2
import pytest
from fastapi.testclient import TestClient


# --- Resolve test DB URL up-front so the app sees it before import. ---

def _default_test_db_url() -> str:
    base = os.environ.get(
        "DATABASE_URL",
        "postgresql://adam:Pushcart-Museum-Baboon2@127.0.0.1:5433/snippets_dev",
    )
    parsed = urlparse(base)
    parts = parsed._replace(path="/snippets_test")
    return urlunparse(parts)


TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", _default_test_db_url())


def _admin_url(test_db_url: str) -> tuple[str, str]:
    """Return (admin_url, target_db_name). The admin URL connects to `postgres`
    so we can DROP/CREATE the target DB. psycopg2 can't do those in a transaction."""
    parsed = urlparse(test_db_url)
    target = parsed.path.lstrip("/")
    admin = urlunparse(parsed._replace(path="/postgres"))
    return admin, target


def _recreate_db_and_run_schema():
    admin_url, target = _admin_url(TEST_DB_URL)
    admin_conn = psycopg2.connect(admin_url)
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cur:
            # Terminate other sessions on the target so DROP doesn't block.
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (target,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{target}"')
            cur.execute(f'CREATE DATABASE "{target}"')
    finally:
        admin_conn.close()

    # Run schema.sql against the freshly-created DB.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "schema.sql"), "r") as f:
        sql = f.read()
    conn = psycopg2.connect(TEST_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_test_db():
    """Recreate the test DB once per session and point the app at it."""
    _recreate_db_and_run_schema()
    os.environ["DATABASE_URL"] = TEST_DB_URL
    os.environ["JWT_SECRET"] = os.environ.get(
        "JWT_SECRET",
        "test-secret-do-not-use-in-prod-but-long-enough-for-hmac-sha256",
    )
    os.environ["INVITE_ADMIN"] = "admin"
    yield


# --- Per-test data isolation ---

# Tables that hold per-user data. Order matters only for the truncate cascade.
_DATA_TABLES = (
    "post_snippets",
    "posts",
    "snippet_tags",
    "snippets",
    "source_authors",
    "sources",
    "source_publishers",
    "tags",
    "magic_links",
    "revoked_tokens",
    "login_attempts",
    "invite_codes",
    "users",
)


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate every data table before each test. Schema, source_types, and
    schema_version stay intact."""
    conn = psycopg2.connect(TEST_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE " + ", ".join(_DATA_TABLES) + " RESTART IDENTITY CASCADE"
            )
        conn.commit()
    finally:
        conn.close()
    yield


# --- App + DB helpers ---

@pytest.fixture(scope="session")
def app():
    """Lazily import the FastAPI app once env is set. Returned as a fixture so
    importing during collection doesn't reach an unconfigured pool."""
    import main  # noqa: WPS433 (intentional late import)
    return main.app


@pytest.fixture
def client(app):
    """A FastAPI TestClient. The lifespan starts/stops the pool around tests."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_conn():
    """Direct DB connection — handy for setup that bypasses HTTP."""
    conn = psycopg2.connect(TEST_DB_URL)
    try:
        yield conn
    finally:
        conn.close()


# --- Auth helpers ---

import bcrypt  # noqa: E402 — after env is set so test secret takes effect


@pytest.fixture
def make_user(db_conn):
    """Factory to insert a user directly with a known password. Returns (user_id, username)."""
    created: list[int] = []

    def _make(username: str = "alice", password: str = "test-password"):
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
                (username, pw_hash),
            )
            uid = cur.fetchone()[0]
        db_conn.commit()
        created.append(uid)
        return uid, username

    yield _make
    # truncate handles cleanup; no-op here.


@pytest.fixture
def auth_token(client, make_user):
    """Register a user and return a (token, user_id, username) tuple."""
    uid, username = make_user()
    resp = client.post("/login", json={"username": username, "password": "test-password"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return token, uid, username


@pytest.fixture
def auth_headers(auth_token):
    token, _, _ = auth_token
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(client, make_user):
    """A token for the user named 'admin' — INVITE_ADMIN is set to 'admin' in this test session."""
    make_user(username="admin", password="test-password")
    resp = client.post("/login", json={"username": "admin", "password": "test-password"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}
