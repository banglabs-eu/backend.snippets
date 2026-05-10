def test_create_get_delete_note(client, auth_headers):
    create = client.post("/snippets", json={"body": "hello world"}, headers=auth_headers)
    assert create.status_code == 200
    snippet_id = create.json()["id"]

    listed = client.get("/snippets", headers=auth_headers).json()
    assert any(n["id"] == snippet_id and n["body"] == "hello world" for n in listed)

    one = client.get(f"/snippets/{snippet_id}", headers=auth_headers)
    assert one.status_code == 200
    assert one.json()["body"] == "hello world"

    deleted = client.delete(f"/snippets/{snippet_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"/snippets/{snippet_id}", headers=auth_headers).status_code == 404


def test_attach_and_remove_tag(client, auth_headers):
    snippet_id = client.post("/snippets", json={"body": "tagged"}, headers=auth_headers).json()["id"]

    tag_id = client.post("/tags/get-or-create", json={"name": "history"}, headers=auth_headers).json()["id"]
    r = client.post(f"/snippets/{snippet_id}/tags", json={"tag_id": tag_id}, headers=auth_headers)
    assert r.status_code == 200

    tags = client.get(f"/snippets/{snippet_id}/tags", headers=auth_headers).json()
    assert any(t["id"] == tag_id for t in tags)

    r2 = client.delete(f"/snippets/{snippet_id}/tags/{tag_id}", headers=auth_headers)
    assert r2.status_code == 200
    tags2 = client.get(f"/snippets/{snippet_id}/tags", headers=auth_headers).json()
    assert all(t["id"] != tag_id for t in tags2)


def test_user_cannot_see_other_users_snippets(client, make_user):
    make_user("alice", "pw")
    make_user("bob", "pw")
    a = client.post("/login", json={"username": "alice", "password": "pw"}).json()["token"]
    b = client.post("/login", json={"username": "bob", "password": "pw"}).json()["token"]

    snippet_id = client.post("/snippets", json={"body": "alice-private"},
                          headers={"Authorization": f"Bearer {a}"}).json()["id"]

    bobs = client.get("/snippets", headers={"Authorization": f"Bearer {b}"}).json()
    assert all(n["id"] != snippet_id for n in bobs)

    r = client.get(f"/snippets/{snippet_id}", headers={"Authorization": f"Bearer {b}"})
    assert r.status_code == 404
