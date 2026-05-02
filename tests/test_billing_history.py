"""Tests for GET /api/subscriptions/invoices.

Coverage goals:
- Authentication required
- User with no subscription: returns empty invoice list (not 404/500)
- User with subscription but no Stripe customer ID: returns empty list
- Stripe service returns invoices: response matches expected schema
- Only paid/open invoices included: draft and void filtered out
- has_more flag set correctly when 20+ invoices exist
- Stripe error → 502 (not 500 with stack trace)
- Invoice fields mapped correctly (amount in pence → pence, unix timestamps)
- StripeService.list_invoices unit test: draft/void filtering
"""
import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.modules.auth.schemas import AuthUser
from app.modules.subscriptions.router import get_subscription_service, get_stripe_service
from app.modules.subscriptions.service import SubscriptionService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(plan: str = "producer", user_id: str = "user-1") -> AuthUser:
    return AuthUser(
        id=user_id,
        email=f"{plan}@example.com",
        name="Test",
        company="Co",
        role="Producer",
        user_type="paid",
        credits_remaining=0,
        plan=plan,
    )


_FAKE_INVOICE = {
    "id": "in_test_001",
    "number": "PROD-0001",
    "status": "paid",
    "amount_paid": 14900,
    "amount_due": 14900,
    "currency": "gbp",
    "created": 1_700_000_000,
    "period_start": 1_699_000_000,
    "period_end": 1_701_000_000,
    "hosted_invoice_url": "https://invoice.stripe.com/test",
    "invoice_pdf": "https://pay.stripe.com/invoice/test/pdf",
    "description": "1 × Producer (Monthly)",
}


class FakeSubscriptionService:
    def __init__(self, subscription: dict | None = None):
        self._subscription = subscription
        self.supabase = MagicMock()
        # Default: supabase returns no rows when searching for any past subscription
        self.supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

    def get_active_subscription(self, user_id: str) -> dict | None:
        return self._subscription


class FakeStripeService:
    def __init__(self, invoices: list[dict] | None = None, raise_error: bool = False):
        self._invoices = invoices or []
        self._raise_error = raise_error

    def list_invoices(self, customer_id: str, limit: int = 20) -> list[dict]:
        if self._raise_error:
            raise RuntimeError("Stripe unreachable")
        return self._invoices[:limit]


# ── Authentication ────────────────────────────────────────────────────────────

def test_invoices_requires_authentication(client):
    response = client.get("/api/subscriptions/invoices")
    assert response.status_code in (401, 403)


# ── No subscription ───────────────────────────────────────────────────────────

def test_invoices_empty_list_when_no_subscription(client):
    """User who has never subscribed gets an empty list, not an error."""
    user = _make_user("free")
    sub_service = FakeSubscriptionService(subscription=None)
    stripe_service = FakeStripeService(invoices=[])

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: sub_service
    client.app.dependency_overrides[get_stripe_service] = lambda: stripe_service

    resp = client.get("/api/subscriptions/invoices", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["invoices"] == []
    assert data["has_more"] is False


def test_invoices_empty_list_when_subscription_has_no_customer_id(client):
    """Subscription row exists but stripe_customer_id is absent: empty list."""
    user = _make_user("producer")
    # Subscription without a customer ID (data integrity gap)
    sub = {"id": "sub-1", "user_id": "user-1", "status": "active", "stripe_customer_id": None}
    sub_service = FakeSubscriptionService(subscription=sub)
    stripe_service = FakeStripeService(invoices=[_FAKE_INVOICE])

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: sub_service
    client.app.dependency_overrides[get_stripe_service] = lambda: stripe_service

    resp = client.get("/api/subscriptions/invoices", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    assert resp.json()["invoices"] == []


# ── Happy path ────────────────────────────────────────────────────────────────

def test_invoices_returns_list_for_subscribed_user(client):
    """Subscribed user with invoices gets the correct invoice list."""
    user = _make_user("producer")
    sub = {"id": "sub-1", "user_id": "user-1", "status": "active", "stripe_customer_id": "cus_xxx"}
    sub_service = FakeSubscriptionService(subscription=sub)
    stripe_service = FakeStripeService(invoices=[_FAKE_INVOICE])

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: sub_service
    client.app.dependency_overrides[get_stripe_service] = lambda: stripe_service

    resp = client.get("/api/subscriptions/invoices", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["invoices"]) == 1
    inv = data["invoices"][0]
    assert inv["id"] == "in_test_001"
    assert inv["number"] == "PROD-0001"
    assert inv["status"] == "paid"
    assert inv["amount_paid"] == 14900
    assert inv["currency"] == "gbp"
    assert inv["created"] == 1_700_000_000
    assert inv["hosted_invoice_url"] == "https://invoice.stripe.com/test"
    assert inv["invoice_pdf"] == "https://pay.stripe.com/invoice/test/pdf"


def test_invoices_schema_has_all_required_fields(client):
    """Each invoice item must contain all documented fields."""
    REQUIRED = {
        "id", "number", "status", "amount_paid", "amount_due",
        "currency", "created", "hosted_invoice_url", "invoice_pdf",
    }
    user = _make_user("producer")
    sub = {"id": "sub-1", "user_id": "user-1", "status": "active", "stripe_customer_id": "cus_xxx"}
    sub_service = FakeSubscriptionService(subscription=sub)
    stripe_service = FakeStripeService(invoices=[_FAKE_INVOICE])

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: sub_service
    client.app.dependency_overrides[get_stripe_service] = lambda: stripe_service

    resp = client.get("/api/subscriptions/invoices", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    inv = resp.json()["invoices"][0]
    missing = REQUIRED - set(inv.keys())
    assert not missing, f"Missing fields: {missing}"


def test_invoices_multiple_invoices_returned(client):
    """Multiple invoices returned in order."""
    user = _make_user("producer")
    sub = {"id": "sub-1", "user_id": "user-1", "status": "active", "stripe_customer_id": "cus_xxx"}
    sub_service = FakeSubscriptionService(subscription=sub)
    invoices = [
        {**_FAKE_INVOICE, "id": f"in_test_{i:03d}", "created": 1_700_000_000 - i * 86400}
        for i in range(5)
    ]
    stripe_service = FakeStripeService(invoices=invoices)

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: sub_service
    client.app.dependency_overrides[get_stripe_service] = lambda: stripe_service

    resp = client.get("/api/subscriptions/invoices", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    assert len(resp.json()["invoices"]) == 5


def test_invoices_has_more_flag_set_when_at_limit(client):
    """has_more=True when the returned count equals the 20-invoice limit."""
    user = _make_user("producer")
    sub = {"id": "sub-1", "user_id": "user-1", "status": "active", "stripe_customer_id": "cus_xxx"}
    sub_service = FakeSubscriptionService(subscription=sub)
    # Exactly 20 invoices → has_more should be True
    invoices = [
        {**_FAKE_INVOICE, "id": f"in_test_{i:03d}"}
        for i in range(20)
    ]
    stripe_service = FakeStripeService(invoices=invoices)

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: sub_service
    client.app.dependency_overrides[get_stripe_service] = lambda: stripe_service

    resp = client.get("/api/subscriptions/invoices", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["invoices"]) == 20
    assert data["has_more"] is True


def test_invoices_has_more_false_when_fewer_than_20(client):
    user = _make_user("producer")
    sub = {"id": "sub-1", "user_id": "user-1", "status": "active", "stripe_customer_id": "cus_xxx"}
    sub_service = FakeSubscriptionService(subscription=sub)
    stripe_service = FakeStripeService(invoices=[_FAKE_INVOICE])

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: sub_service
    client.app.dependency_overrides[get_stripe_service] = lambda: stripe_service

    resp = client.get("/api/subscriptions/invoices", headers={"Authorization": "Bearer token"})
    assert resp.json()["has_more"] is False


# ── Error handling ────────────────────────────────────────────────────────────

def test_invoices_stripe_error_returns_502(client):
    """Stripe network error → 502, not 500 stack trace."""
    user = _make_user("producer")
    sub = {"id": "sub-1", "user_id": "user-1", "status": "active", "stripe_customer_id": "cus_xxx"}
    sub_service = FakeSubscriptionService(subscription=sub)
    stripe_service = FakeStripeService(raise_error=True)

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: sub_service
    client.app.dependency_overrides[get_stripe_service] = lambda: stripe_service

    resp = client.get("/api/subscriptions/invoices", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 502
    assert "invoice" in resp.json()["detail"].lower()


# ── StripeService.list_invoices unit test ─────────────────────────────────────

def test_stripe_service_list_invoices_filters_draft_and_void():
    """Only paid/open invoices are returned; draft and void are discarded."""
    from app.modules.payments.service import StripeService

    fake_invoices = [
        {"id": "inv-paid", "status": "paid", "amount_paid": 5000, "amount_due": 5000,
         "currency": "usd", "created": 1700000001, "number": "PRD-001",
         "lines": {"data": [{"period": {"start": 0, "end": 0}, "description": "Sub"}]},
         "hosted_invoice_url": None, "invoice_pdf": None, "description": None},
        {"id": "inv-open", "status": "open", "amount_paid": 0, "amount_due": 5000,
         "currency": "usd", "created": 1700000002, "number": "PRD-002",
         "lines": {"data": []},
         "hosted_invoice_url": None, "invoice_pdf": None, "description": None},
        {"id": "inv-draft", "status": "draft", "amount_paid": 0, "amount_due": 5000,
         "currency": "usd", "created": 1700000003, "number": "PRD-003",
         "lines": {"data": []},
         "hosted_invoice_url": None, "invoice_pdf": None, "description": None},
        {"id": "inv-void", "status": "void", "amount_paid": 0, "amount_due": 0,
         "currency": "usd", "created": 1700000004, "number": "PRD-004",
         "lines": {"data": []},
         "hosted_invoice_url": None, "invoice_pdf": None, "description": None},
    ]

    # Stub stripe.Invoice.list to return a fake paginator
    class FakePager:
        def auto_paging_iter(self):
            return iter(fake_invoices)

    mock_settings = MagicMock()
    mock_settings.STRIPE_SECRET_KEY = "sk_test_fake"

    svc = StripeService(mock_settings)
    with patch("stripe.Invoice.list", return_value=FakePager()):
        results = svc.list_invoices("cus_xxx", limit=20)

    ids = [r["id"] for r in results]
    assert "inv-paid" in ids
    assert "inv-open" in ids
    assert "inv-draft" not in ids, "Draft invoices must be excluded"
    assert "inv-void" not in ids, "Void invoices must be excluded"
    assert len(results) == 2


def test_stripe_service_list_invoices_respects_limit():
    """list_invoices stops at the limit even if more invoices exist."""
    from app.modules.payments.service import StripeService

    def _make_inv(i):
        return {"id": f"inv-{i}", "status": "paid", "amount_paid": 1000, "amount_due": 1000,
                "currency": "usd", "created": i, "number": f"PRD-{i:04d}",
                "lines": {"data": []},
                "hosted_invoice_url": None, "invoice_pdf": None, "description": None}

    class FakePager:
        def auto_paging_iter(self):
            return iter([_make_inv(i) for i in range(50)])

    mock_settings = MagicMock()
    mock_settings.STRIPE_SECRET_KEY = "sk_test_fake"

    svc = StripeService(mock_settings)
    with patch("stripe.Invoice.list", return_value=FakePager()):
        results = svc.list_invoices("cus_xxx", limit=5)

    assert len(results) == 5
