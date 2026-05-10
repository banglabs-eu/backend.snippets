"""Privacy boundary + publish flow.

These tests are the most important in the suite — a regression here would silently
leak private data. Read them when you're tempted to skip a publish-flow check."""


def _create_tagged_note(client, headers, body: str, tag_names: list[str]):
    snippet_id = client.post("/snippets", json={"body": body}, headers=headers).json()["id"]
    tag_ids = []
    for name in tag_names:
        tid = client.post("/tags/get-or-create", json={"name": name}, headers=headers).json()["id"]
        client.post(f"/snippets/{snippet_id}/tags", json={"tag_id": tid}, headers=headers)
        tag_ids.append(tid)
    return {"id": snippet_id, "body": body}, tag_ids


def test_unpublished_note_is_not_public(client, auth_headers, auth_token):
    _, _, username = auth_token
    _create_tagged_note(client, auth_headers, "private body", [])

    feed = client.get("/public/feed/dashboard").json()
    assert all(n["body"] != "private body" for n in feed["snippets"])

    user_notes = client.get(f"/public/users/{username}/snippets").json()
    assert user_notes == []


def test_publish_note_makes_only_selected_tags_public(client, auth_headers, auth_token):
    _, _, username = auth_token
    note, tag_ids = _create_tagged_note(client, auth_headers, "publishable", ["public-tag", "private-tag"])
    public_tag_id, private_tag_id = tag_ids

    # Publish, electing only the first tag to be public.
    r = client.post(f"/snippets/{note['id']}/publish",
                    json={"public_tag_ids": [public_tag_id]}, headers=auth_headers)
    assert r.status_code == 200

    notes = client.get(f"/public/users/{username}/snippets").json()
    assert len(notes) == 1
    visible_tag_ids = {t["id"] for t in notes[0]["tags"]}
    assert public_tag_id in visible_tag_ids
    assert private_tag_id not in visible_tag_ids  # privacy boundary


def test_unpublish_hides_note(client, auth_headers, auth_token):
    _, _, username = auth_token
    note, _ = _create_tagged_note(client, auth_headers, "toggle", [])
    client.post(f"/snippets/{note['id']}/publish", json={"public_tag_ids": []}, headers=auth_headers)
    assert len(client.get(f"/public/users/{username}/snippets").json()) == 1

    r = client.post(f"/snippets/{note['id']}/unpublish", headers=auth_headers)
    assert r.status_code == 200
    assert client.get(f"/public/users/{username}/snippets").json() == []


def test_one_user_cannot_publish_anothers_note(client, make_user):
    make_user("alice", "pw")
    make_user("bob", "pw")
    a = {"Authorization": f"Bearer {client.post('/login', json={'username':'alice','password':'pw'}).json()['token']}"}
    b = {"Authorization": f"Bearer {client.post('/login', json={'username':'bob','password':'pw'}).json()['token']}"}

    note = client.post("/snippets", json={"body": "alice's"}, headers=a).json()
    r = client.post(f"/snippets/{note['id']}/publish", json={"public_tag_ids": []}, headers=b)
    assert r.status_code == 404

    # Confirm it stayed private.
    feed = client.get("/public/feed/dashboard").json()
    assert all(n["id"] != note["id"] for n in feed["snippets"])


def test_dashboard_endpoint_filters_to_public_data(client, auth_headers, auth_token):
    """The aggregate /public/users/<u>/dashboard endpoint must only expose public notes,
    public tags, and the sources/authors reachable from those public notes."""
    _, _, username = auth_token

    # Two notes — one public, one private. Each with its own tag.
    public_note, [public_tag] = _create_tagged_note(client, auth_headers, "public", ["public-tag"])
    private_note, [private_tag] = _create_tagged_note(client, auth_headers, "private", ["private-tag"])
    client.post(f"/snippets/{public_note['id']}/publish",
                json={"public_tag_ids": [public_tag]}, headers=auth_headers)

    data = client.get(f"/public/users/{username}/dashboard").json()
    snippet_ids = {n["id"] for n in data["snippets"]}
    assert public_note["id"] in snippet_ids
    assert private_note["id"] not in snippet_ids

    tag_ids = {t["id"] for t in data["tags"]}
    assert public_tag in tag_ids
    assert private_tag not in tag_ids
