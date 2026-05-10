"""Frontend/backend JSON contract tests.

If you change a key name in a public response payload, the matching frontend
type in `web.snippets/src/api.ts` has to move with it. These tests freeze the
exact key set the frontend reads so a drift breaks pytest before it breaks the
browser. Update both sides together when you intentionally change a contract.
"""


def _expect_keys(payload: dict, expected: set[str]) -> None:
    actual = set(payload.keys())
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"missing keys: {sorted(missing)}"
    assert not extra, f"unexpected keys: {sorted(extra)} (frontend doesn't read these)"


def test_public_user_dashboard_payload_shape(client, auth_token):
    _, _, username = auth_token
    body = client.get(f"/public/users/{username}/dashboard").json()
    _expect_keys(body, {
        "snippets", "tags", "sources", "authors",
        "publishers", "source_types", "snippet_tags",
    })


def test_public_global_feed_payload_shape(client):
    body = client.get("/public/feed/dashboard").json()
    _expect_keys(body, {
        "snippets", "tags", "sources", "authors",
        "publishers", "source_types", "snippet_tags",
    })


def test_admin_metrics_totals_keys_match_admin_panel(client, admin_headers):
    """The AdminPanel renders specific cards by name; bake them in."""
    body = client.get("/admin/metrics", headers=admin_headers).json()
    totals = body["totals"]
    for key in ("users", "snippets", "posts", "sources", "tags"):
        assert key in totals, f"AdminPanel reads metrics.totals.{key}; backend dropped it"
