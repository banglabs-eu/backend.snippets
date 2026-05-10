def test_login_returns_token_and_username(client, make_user):
    make_user(username="alice", password="hunter2")
    r = client.post("/login", json={"username": "alice", "password": "hunter2"})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "alice"
    assert isinstance(body["token"], str) and len(body["token"]) > 20


def test_login_wrong_password_fails(client, make_user):
    make_user(username="alice", password="hunter2")
    r = client.post("/login", json={"username": "alice", "password": "WRONG"})
    assert r.status_code == 401


def test_protected_endpoint_requires_token(client):
    r = client.get("/snippets")
    assert r.status_code == 401


def test_protected_endpoint_with_valid_token(client, auth_headers):
    r = client.get("/snippets", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_me_returns_username_and_admin_flag(client, auth_token, admin_headers):
    token, uid, username = auth_token
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == uid
    assert body["username"] == username
    assert body["is_admin"] is False

    r2 = client.get("/me", headers=admin_headers)
    assert r2.status_code == 200
    assert r2.json()["is_admin"] is True


def test_logout_revokes_token(client, auth_token):
    token, _, _ = auth_token
    headers = {"Authorization": f"Bearer {token}"}
    r = client.post("/logout", headers=headers)
    assert r.status_code == 200
    # After logout, the same token should no longer be accepted.
    r2 = client.get("/snippets", headers=headers)
    assert r2.status_code == 401
