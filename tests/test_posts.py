def test_post_create_requires_title_to_publish(client, auth_headers):
    r = client.post("/posts", json={"body": "x", "title": "", "published": True}, headers=auth_headers)
    assert r.status_code == 400


def test_post_publish_propagates_to_referenced_notes(client, auth_headers, auth_token):
    """Publishing a post auto-publishes every note it references and surfaces the
    selected tags. This is the contract the PostsView publish dialog relies on."""
    _, _, username = auth_token

    # A note with two tags, plus a second note that won't be referenced.
    snippet_id = client.post("/snippets", json={"body": "referenced"}, headers=auth_headers).json()["id"]
    tag_a_id = client.post("/tags/get-or-create", json={"name": "alpha"}, headers=auth_headers).json()["id"]
    tag_b_id = client.post("/tags/get-or-create", json={"name": "beta"}, headers=auth_headers).json()["id"]
    for tid in (tag_a_id, tag_b_id):
        client.post(f"/snippets/{snippet_id}/tags", json={"tag_id": tid}, headers=auth_headers)

    other_id = client.post("/snippets", json={"body": "unrelated"}, headers=auth_headers).json()["id"]

    # Create + publish a post that references the first note. Mark only tag_a public.
    body = f"intro\n[snippet:{snippet_id}]\nend"
    create = client.post("/posts", json={
        "body": body,
        "title": "My Essay",
        "published": True,
        "public_tag_ids": [tag_a_id],
    }, headers=auth_headers)
    assert create.status_code == 200

    public_snippets = client.get(f"/public/users/{username}/snippets").json()
    public_snippet_ids = {n["id"] for n in public_snippets}
    assert snippet_id in public_snippet_ids        # referenced → published
    assert other_id not in public_snippet_ids   # unrelated → still private

    # Tag visibility on the referenced note: alpha only.
    visible = next(n for n in public_snippets if n["id"] == snippet_id)
    visible_tag_ids = {t["id"] for t in visible["tags"]}
    assert tag_a_id in visible_tag_ids
    assert tag_b_id not in visible_tag_ids


def test_post_slug_is_unique_per_user(client, make_user):
    make_user("alice", "pw")
    headers = {"Authorization": f"Bearer {client.post('/login', json={'username':'alice','password':'pw'}).json()['token']}"}

    # Two posts with the same title → second should get a deduped slug.
    p1_id = client.post("/posts", json={"title": "Hello World", "body": "a", "published": True},
                        headers=headers).json()["id"]
    p2_id = client.post("/posts", json={"title": "Hello World", "body": "b", "published": True},
                        headers=headers).json()["id"]
    assert p1_id != p2_id

    listed = client.get("/posts", headers=headers).json()
    slugs = [p["slug"] for p in listed]
    assert len(slugs) == len(set(slugs))  # all unique
