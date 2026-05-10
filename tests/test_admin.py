def test_non_admin_user_cannot_access_metrics(client, auth_headers):
    r = client.get("/admin/metrics", headers=auth_headers)
    assert r.status_code == 403


def test_admin_user_can_access_metrics(client, admin_headers):
    r = client.get("/admin/metrics", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    # Spot-check the contract the AdminPanel relies on.
    for key in ("pool", "active_users_5m", "totals", "slow_endpoints", "recent_samples"):
        assert key in body, f"missing {key}"
