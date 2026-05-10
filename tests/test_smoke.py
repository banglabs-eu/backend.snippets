def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_version(client):
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert "schema_version" in body
    # schema.sql is at v15+ as of writing — guard against accidental rollback.
    assert body["schema_version"] is not None and body["schema_version"] >= 15
