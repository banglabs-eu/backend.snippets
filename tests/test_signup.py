"""Email-only signup flow.

The magic-link endpoint always returns 200; we drive the verify + complete-
registration steps directly via DB lookups for the token because the email
sender is mocked out in test mode (SMTP_HOST unset → logs to stdout).
"""

import psycopg2
from tests.conftest import TEST_DB_URL


def _latest_magic_link_token(email: str) -> str:
    """Pull the most recent magic-link token for an email straight from the DB
    — saves us mocking the SMTP path."""
    conn = psycopg2.connect(TEST_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT token FROM magic_links WHERE email = %s ORDER BY created_at DESC LIMIT 1",
                (email,),
            )
            row = cur.fetchone()
        assert row is not None, f"no magic link found for {email}"
        return row[0]
    finally:
        conn.close()


def test_new_email_triggers_register_intent(client):
    r = client.post("/auth/magic-link", json={"email": "newcomer@example.com"})
    assert r.status_code == 200

    token = _latest_magic_link_token("newcomer@example.com")
    verify = client.post("/auth/verify-magic-link", json={"token": token}).json()
    assert verify["kind"] == "register"
    assert verify["email"] == "newcomer@example.com"
    assert "registration_token" in verify


def test_complete_registration_creates_user_and_signs_in(client):
    client.post("/auth/magic-link", json={"email": "alice@example.com"})
    token = _latest_magic_link_token("alice@example.com")
    verify = client.post("/auth/verify-magic-link", json={"token": token}).json()
    reg_token = verify["registration_token"]

    r = client.post("/auth/complete-registration", json={
        "registration_token": reg_token,
        "username": "alice",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "alice"
    assert "token" in body

    # Token works for authenticated calls.
    me = client.get("/me", headers={"Authorization": f"Bearer {body['token']}"}).json()
    assert me["username"] == "alice"


def test_existing_email_signs_in_directly(client, make_user):
    make_user(username="bob", password="pw")
    # Stamp the email on the user — make_user only sets username/password.
    import psycopg2
    conn = psycopg2.connect(TEST_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET email = %s WHERE username = %s", ("bob@example.com", "bob"))
        conn.commit()
    finally:
        conn.close()

    client.post("/auth/magic-link", json={"email": "bob@example.com"})
    token = _latest_magic_link_token("bob@example.com")
    verify = client.post("/auth/verify-magic-link", json={"token": token}).json()
    assert verify["kind"] == "sign_in"
    assert verify["username"] == "bob"
    assert "token" in verify


def test_username_availability_check(client):
    # Empty
    assert client.get("/auth/username-available?u=").json()["available"] is False
    # Bad format — too short
    bad = client.get("/auth/username-available?u=ab").json()
    assert bad["available"] is False and bad["reason"] == "invalid_format"
    # Bad format — has spaces
    bad2 = client.get("/auth/username-available?u=a b c").json()
    assert bad2["available"] is False and bad2["reason"] == "invalid_format"
    # Case is normalized — "Alice" is treated the same as "alice"
    assert client.get("/auth/username-available?u=Alice").json()["available"] is True
    # Available
    assert client.get("/auth/username-available?u=alice").json()["available"] is True
    # Now take it via the registration path and confirm it flips to "taken"
    client.post("/auth/magic-link", json={"email": "alice@example.com"})
    token = _latest_magic_link_token("alice@example.com")
    reg_token = client.post("/auth/verify-magic-link", json={"token": token}).json()["registration_token"]
    client.post("/auth/complete-registration", json={"registration_token": reg_token, "username": "alice"})
    taken = client.get("/auth/username-available?u=alice").json()
    assert taken["available"] is False and taken["reason"] == "taken"


def test_register_token_is_single_use(client):
    client.post("/auth/magic-link", json={"email": "carol@example.com"})
    token = _latest_magic_link_token("carol@example.com")
    reg_token = client.post("/auth/verify-magic-link", json={"token": token}).json()["registration_token"]

    first = client.post("/auth/complete-registration",
                        json={"registration_token": reg_token, "username": "carol"})
    assert first.status_code == 200
    second = client.post("/auth/complete-registration",
                         json={"registration_token": reg_token, "username": "carol2"})
    assert second.status_code == 401


def test_set_password_on_passwordless_account_still_sets_local_password(client):
    # Sign up via magic link — account is passwordless, and (unlike /register)
    # never touches accounts.bang-labs.eu, so it has no accounts_user_id.
    client.post("/auth/magic-link", json={"email": "dave@example.com"})
    mlink = _latest_magic_link_token("dave@example.com")
    reg = client.post("/auth/verify-magic-link", json={"token": mlink}).json()
    completed = client.post("/auth/complete-registration",
                            json={"registration_token": reg["registration_token"], "username": "dave"}).json()
    jwt = completed["token"]
    headers = {"Authorization": f"Bearer {jwt}"}

    # Setting an initial password still succeeds — the endpoint itself is
    # unchanged — but it's now vestigial: /login only trusts accounts, and
    # this account was never linked to an accounts identity. Known gap from
    # the SSO cutover (see routers/auth.py's module docstring); the magic-link
    # signup flow hasn't been migrated to also create/link one.
    r = client.post("/auth/set-password", json={"password": "secret123"}, headers=headers)
    assert r.status_code == 200, r.text

    login = client.post("/login", json={"username": "dave", "password": "secret123"})
    assert login.status_code == 401, login.text


def test_set_password_rejects_when_already_set(client):
    # make_user's accounts-linked users correctly have password_hash=NULL now
    # (see the SSO-cutover note in routers/auth.py) — the passwordless
    # magic-link path is the only one left that still writes a real local
    # hash, so it's the only way to set up an account this guard protects.
    client.post("/auth/magic-link", json={"email": "eve@example.com"})
    mlink = _latest_magic_link_token("eve@example.com")
    reg = client.post("/auth/verify-magic-link", json={"token": mlink}).json()
    completed = client.post("/auth/complete-registration",
                            json={"registration_token": reg["registration_token"], "username": "eve"}).json()
    headers = {"Authorization": f"Bearer {completed['token']}"}

    first = client.post("/auth/set-password", json={"password": "initial-pw"}, headers=headers)
    assert first.status_code == 200, first.text

    r = client.post("/auth/set-password", json={"password": "new-pw"}, headers=headers)
    assert r.status_code == 400
    assert "already set" in r.json()["detail"].lower()


def test_set_password_enforces_min_length(client):
    client.post("/auth/magic-link", json={"email": "frank@example.com"})
    mlink = _latest_magic_link_token("frank@example.com")
    reg = client.post("/auth/verify-magic-link", json={"token": mlink}).json()
    completed = client.post("/auth/complete-registration",
                            json={"registration_token": reg["registration_token"], "username": "frank"}).json()
    headers = {"Authorization": f"Bearer {completed['token']}"}

    short = client.post("/auth/set-password", json={"password": "12345"}, headers=headers)
    assert short.status_code == 400
    assert "at least 6" in short.json()["detail"]
