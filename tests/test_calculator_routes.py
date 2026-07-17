"""Route-level gating tests for the What-If calculator.

The public /what-if lead magnet (handoff §7) requires the scenario endpoint to
serve anonymous users a TEASER (programme/rates/rebate visible; currency
advantage, crew savings, net saving, minimum spend and payment timelines
redacted) instead of rejecting them with a 401.
"""
from __future__ import annotations


SCENARIO_BODY = {
    "budget_amount": 4_000_000,
    "budget_currency": "GBP",
    "vfx_pct": 0,
    "production_format": "Feature Film",
    "production_priority": "full",
}


def test_anonymous_scenario_returns_teaser_not_401(client):
    response = client.post("/api/calculator/scenario", json=SCENARIO_BODY)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["budget_amount"] == 4_000_000
    # Every territory row (if the test DB has any seeded) must be redacted.
    for territory in payload["territories"]:
        assert territory["net_saving_display"] == "Sign up to view"
        assert territory["payment_timeline"] is None
        assert territory["min_spend"] is None
        assert territory["fx_rate"] is None
        assert territory["crew_rates"] == {}


def test_scenario_rejects_invalid_body_regardless_of_auth(client):
    response = client.post("/api/calculator/scenario", json={"budget_amount": -5})
    assert response.status_code == 422
