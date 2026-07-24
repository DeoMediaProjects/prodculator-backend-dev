"""Tests for the compressed-cycle billing test feature (2-day price + auto-refund).

The security-critical property is that a refund fires ONLY for subscriptions
explicitly tagged autoRefund="true" — a real customer's invoice must never be
refunded by this path. These tests pin that behaviour.
"""
from types import SimpleNamespace

import stripe

from app.core.config import Settings
from app.modules.payments.service import StripeService


def _service() -> StripeService:
    return Settings() and StripeService(Settings())


def test_auto_refund_fires_for_flagged_subscription(monkeypatch):
    svc = _service()

    monkeypatch.setattr(
        stripe.Subscription, "retrieve",
        lambda sub_id: {"id": sub_id, "metadata": {"autoRefund": "true"}},
    )
    created = {}

    def fake_refund_create(**kwargs):
        created.update(kwargs)
        return {"id": "re_test_123"}

    monkeypatch.setattr(stripe.Refund, "create", fake_refund_create)

    invoice = {"id": "in_1", "subscription": "sub_1", "payment_intent": "pi_1"}
    refund_id = svc.auto_refund_test_invoice(invoice)

    assert refund_id == "re_test_123"
    assert created["payment_intent"] == "pi_1"
    assert created["metadata"]["subscription_id"] == "sub_1"


def test_auto_refund_never_fires_for_real_subscription(monkeypatch):
    """A subscription without autoRefund=true must NEVER be refunded."""
    svc = _service()

    monkeypatch.setattr(
        stripe.Subscription, "retrieve",
        lambda sub_id: {"id": sub_id, "metadata": {}},  # no flag = real customer
    )

    def explode(**kwargs):
        raise AssertionError("Refund.create must not be called for a real subscription")

    monkeypatch.setattr(stripe.Refund, "create", explode)

    invoice = {"id": "in_2", "subscription": "sub_real", "payment_intent": "pi_2"}
    assert svc.auto_refund_test_invoice(invoice) is None


def test_auto_refund_noop_without_subscription(monkeypatch):
    """One-time (non-subscription) invoices carry no subscription and are ignored."""
    svc = _service()

    def explode(sub_id):
        raise AssertionError("Subscription.retrieve must not be called without a subscription")

    monkeypatch.setattr(stripe.Subscription, "retrieve", explode)

    assert svc.auto_refund_test_invoice({"id": "in_3", "subscription": None}) is None


def test_auto_refund_swallows_already_refunded(monkeypatch):
    """A Stripe retry of the same event (already-refunded charge) is benign."""
    svc = _service()

    monkeypatch.setattr(
        stripe.Subscription, "retrieve",
        lambda sub_id: {"id": sub_id, "metadata": {"autoRefund": "true"}},
    )

    def already(**kwargs):
        raise stripe.error.InvalidRequestError("Charge already refunded", param=None)

    monkeypatch.setattr(stripe.Refund, "create", already)

    invoice = {"id": "in_4", "subscription": "sub_1", "payment_intent": "pi_4"}
    assert svc.auto_refund_test_invoice(invoice) is None


def test_get_or_create_test_price_reuses_existing(monkeypatch):
    """Idempotency: an existing test price with the lookup_key is reused, not recreated."""
    svc = _service()

    monkeypatch.setattr(
        stripe.Price, "list",
        lambda **kwargs: SimpleNamespace(data=[SimpleNamespace(id="price_existing_test")]),
    )

    def explode(**kwargs):
        raise AssertionError("Price.create must not run when a matching test price exists")

    monkeypatch.setattr(stripe.Price, "create", explode)

    assert svc.get_or_create_test_price("price_real") == "price_existing_test"


def test_get_or_create_test_price_creates_short_cycle_clone(monkeypatch):
    svc = _service()

    monkeypatch.setattr(stripe.Price, "list", lambda **kwargs: SimpleNamespace(data=[]))
    monkeypatch.setattr(
        stripe.Price, "retrieve",
        lambda price_id: {"product": "prod_1", "currency": "gbp", "unit_amount": 4900, "recurring": {"interval": "month"}},
    )
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="price_new_test")

    monkeypatch.setattr(stripe.Price, "create", fake_create)

    result = svc.get_or_create_test_price("price_real")

    assert result == "price_new_test"
    assert captured["recurring"] == {"interval": "day", "interval_count": 2}
    # Token amount, NOT the real £49 price.
    assert captured["unit_amount"] == 100
    assert captured["product"] == "prod_1"
