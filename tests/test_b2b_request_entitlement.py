"""Entitlement scoping on client ad-hoc requests (handoff §4.4).

``allowed_section_keys`` existed but nothing on the client path called it, so a
client could ask for a product whose sections another client holds exclusively
and only find out when composition collided. Two things are asserted here:

* the check runs **before the request row is written**, so a refused request
  leaves nothing behind, and
* the refusal names the withheld sections and their reversion dates, so the
  dashboard can disable them up front instead of surfacing an opaque failure.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.core.dependencies import get_current_user, get_supabase
from app.modules.b2b.package_service import PRODUCT_TEMPLATES
from app.modules.b2b.router import get_b2b_service
from app.modules.b2b.service import B2BService, EntitlementScopeError

# A product that is both sold (B2B_PRODUCTS, so the route accepts it) and has
# its own section template. Note PRODUCT_TEMPLATES carries some keys that are
# not sold products and vice versa; product_template() falls back to
# strategic_trend for the latter.
PRODUCT = "camera_equipment"
TEMPLATE = PRODUCT_TEMPLATES[PRODUCT]
OURS = "sub_ours"
THEIRS = "sub_theirs"


def _entitlement_row(section_keys, subscription_id=THEIRS, reverts_at="2028-06-30"):
    return {
        "id": "ent-1",
        "b2b_subscription_id": subscription_id,
        "module_key": "ai_usage",
        "module_label": "AI Usage Module",
        "section_keys": list(section_keys),
        "is_exclusive": True,
        "reverts_at": reverts_at,
    }


def _service(entitlement_rows) -> B2BService:
    """A B2BService whose only live dependency is the entitlements table."""
    service = B2BService.__new__(B2BService)
    db = MagicMock()
    db.table.return_value.select.return_value.execute.return_value.data = entitlement_rows
    service.db = db
    return service


# ── request_entitlement ─────────────────────────────────────────────────────


def test_nothing_withheld_when_no_exclusivity_exists():
    result = _service([]).request_entitlement(
        product_type=PRODUCT, subscription_id=OURS,
    )
    assert result["allowed_section_keys"] == TEMPLATE
    assert result["withheld_sections"] == []
    assert result["can_request"] is True


def test_a_clients_own_exclusivity_never_withholds_from_itself():
    rows = [_entitlement_row(TEMPLATE, subscription_id=OURS)]
    result = _service(rows).request_entitlement(
        product_type=PRODUCT, subscription_id=OURS,
    )
    assert result["allowed_section_keys"] == TEMPLATE
    assert result["can_request"] is True


def test_sections_held_by_another_client_are_withheld():
    held = TEMPLATE[:2]
    result = _service([_entitlement_row(held)]).request_entitlement(
        product_type=PRODUCT, subscription_id=OURS,
    )
    assert result["allowed_section_keys"] == TEMPLATE[2:]
    assert {s["section_key"] for s in result["withheld_sections"]} == set(held)
    assert result["can_request"] is True


def test_withheld_sections_carry_a_reversion_date_and_a_readable_title():
    result = _service([_entitlement_row(TEMPLATE[:1])]).request_entitlement(
        product_type=PRODUCT, subscription_id=OURS,
    )
    withheld = result["withheld_sections"][0]
    assert withheld["available_from"] == "2028-06-30"
    assert withheld["module_label"] == "AI Usage Module"
    # A section key is not a label a client should be shown.
    assert withheld["section_title"] and withheld["section_title"] != withheld["section_key"]


def test_perpetual_exclusivity_reports_no_reversion_date():
    result = _service([_entitlement_row(TEMPLATE[:1], reverts_at=None)]).request_entitlement(
        product_type=PRODUCT, subscription_id=OURS,
    )
    assert result["withheld_sections"][0]["available_from"] is None


def test_reverted_exclusivity_withholds_nothing():
    rows = [_entitlement_row(TEMPLATE, reverts_at="2020-01-01")]
    result = _service(rows).request_entitlement(
        product_type=PRODUCT, subscription_id=OURS,
    )
    assert result["allowed_section_keys"] == TEMPLATE
    assert result["withheld_sections"] == []


def test_everything_withheld_means_the_product_cannot_be_requested():
    result = _service([_entitlement_row(TEMPLATE)]).request_entitlement(
        product_type=PRODUCT, subscription_id=OURS,
    )
    assert result["allowed_section_keys"] == []
    assert result["can_request"] is False


# ── assert_request_within_entitlement ───────────────────────────────────────


def test_partial_withholding_is_allowed_through():
    """Refusing the whole request over one exclusive section would withhold
    intelligence the client is contractually owed."""
    service = _service([_entitlement_row(TEMPLATE[:1])])
    service.assert_request_within_entitlement(
        product_type=PRODUCT, subscription_id=OURS,
    )  # does not raise


def test_total_withholding_raises_with_the_withheld_sections_attached():
    service = _service([_entitlement_row(TEMPLATE)])
    with pytest.raises(EntitlementScopeError) as exc_info:
        service.assert_request_within_entitlement(
            product_type=PRODUCT, subscription_id=OURS,
        )
    error = exc_info.value
    assert len(error.withheld) == len(TEMPLATE)
    assert "licensed exclusively" in str(error)
    assert "2028-06-30" in str(error)


def test_the_scope_error_is_still_a_permission_error():
    """Existing `except PermissionError` handlers must keep catching it."""
    assert issubclass(EntitlementScopeError, PermissionError)


# ── Rejection happens before the row is written ─────────────────────────────


def test_create_request_checks_entitlement_before_persisting():
    """The point of the fix: no half-made request row to clean up."""
    service = _service([_entitlement_row(TEMPLATE)])
    service.active_subscription = MagicMock(return_value={"id": OURS})
    service._clean_email = staticmethod(lambda v: v)

    with pytest.raises(EntitlementScopeError):
        service.create_intelligence_request(
            user_id="u1",
            user_email="client@example.com",
            product_type=PRODUCT,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )

    inserts = [
        call for call in service.db.table.return_value.mock_calls
        if "insert" in str(call)
    ]
    assert inserts == [], "the request row must not be written when refused"


def test_create_request_proceeds_when_entitled():
    service = _service([])
    service.active_subscription = MagicMock(return_value={"id": OURS})
    service._clean_email = staticmethod(lambda v: v)
    service.db.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": "req-1"}
    ]

    row = service.create_intelligence_request(
        user_id="u1",
        user_email="client@example.com",
        product_type=PRODUCT,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
    )
    assert row["id"] == "req-1"


# ── Route behaviour ─────────────────────────────────────────────────────────


_DEFAULT_SUB = object()  # sentinel: None is a meaningful value here


def _wire(client, entitlement_rows, *, subscription=_DEFAULT_SUB):
    from app.modules.auth.schemas import AuthUser

    service = _service(entitlement_rows)
    service.active_subscription = MagicMock(
        return_value={"id": OURS} if subscription is _DEFAULT_SUB else subscription
    )
    service._clean_email = staticmethod(lambda v: v)
    service.db.table.return_value.insert.return_value.execute.return_value.data = [{
        "id": "req-1", "user_id": "user-1", "b2b_subscription_id": OURS,
        "product_type": PRODUCT, "status": "processing", "request_type": "on_demand",
        "period_start": "2026-07-01", "period_end": "2026-07-31",
        "recipient_email": "client@example.com",
        "created_at": "2026-08-06T00:00:00Z", "updated_at": "2026-08-06T00:00:00Z",
    }]
    service.add_download_url = lambda row: row

    client.app.dependency_overrides[get_b2b_service] = lambda: service
    client.app.dependency_overrides[get_supabase] = lambda: MagicMock()
    client.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="client@example.com", name="Client", company="Acme",
        role="Buyer", user_type="paid", credits_remaining=0, plan="professional",
    )
    return service


def test_route_returns_a_specific_409_not_an_opaque_one(client):
    _wire(client, [_entitlement_row(TEMPLATE)])

    response = client.post(
        "/api/b2b/requests",
        json={
            "product_type": PRODUCT,
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
        },
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason"] == "entitlement_scope"
    assert detail["message"]
    assert len(detail["withheld_sections"]) == len(TEMPLATE)
    assert detail["withheld_sections"][0]["available_from"] == "2028-06-30"


def test_route_allows_an_entitled_request(client):
    _wire(client, [])
    response = client.post(
        "/api/b2b/requests",
        json={
            "product_type": PRODUCT,
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
        },
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200


def test_dashboard_can_read_its_entitlement(client):
    _wire(client, [_entitlement_row(TEMPLATE[:2])])
    response = client.get(
        f"/api/b2b/requests/entitlement?product_type={PRODUCT}",
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["allowed_section_keys"] == TEMPLATE[2:]
    assert len(body["withheld_sections"]) == 2
    assert body["can_request"] is True


def test_entitlement_endpoint_requires_a_subscription(client):
    _wire(client, [], subscription=None)
    response = client.get(
        f"/api/b2b/requests/entitlement?product_type={PRODUCT}",
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 403


def test_entitlement_endpoint_rejects_an_unknown_product(client):
    _wire(client, [])
    response = client.get(
        "/api/b2b/requests/entitlement?product_type=not_a_product",
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 404


def test_entitlement_path_is_not_captured_by_the_request_id_route(client):
    """/requests/entitlement is a literal path and must not be read as an id."""
    _wire(client, [])
    response = client.get(
        f"/api/b2b/requests/entitlement?product_type={PRODUCT}",
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200
    assert "allowed_section_keys" in response.json()
