def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"


def test_openapi_includes_core_paths(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    assert "/api/health" in paths
    assert "/api/auth/signup" in paths
    assert "/api/scripts/analyze" in paths
    assert "/api/reports" in paths
    assert "/api/payments/checkout" in paths
    assert "/api/payments/update-payment-method" in paths
    assert "/api/webhooks/stripe" in paths
    assert "/api/grants" in paths
    assert "/api/festivals" in paths
    assert "/api/watchlist" in paths
    assert "/api/subscriptions/active" in paths
    assert "/api/subscriptions/can-generate" in paths
    assert "/api/admin/users" in paths
    assert "/api/admin/metrics" in paths
    assert "/api/admin/incentives" in paths
    assert "/api/admin/crew-costs" in paths
    assert "/api/admin/email/preview" in paths
