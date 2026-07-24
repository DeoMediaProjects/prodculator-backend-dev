"""Pins the shape contract that caused a production-only outage: a real Stripe
SDK object (Subscription, Invoice, Session, Refund, Price, ...) is NOT a dict
and does not support .get() -- only attribute access (sub.id) and bracket
access (sub["id"]) work. Every webhook handler and payments-service method is
written against plain dicts, so every live-Stripe-object boundary must call
.to_dict() before handing the result off.

Every test in this suite that mocked a Stripe call previously returned a
plain dict or SimpleNamespace, which silently worked in tests but crashed in
production the moment a real event arrived (checkout.session.completed,
customer.subscription.updated/deleted, invoice.paid, the hourly reconciler,
and the compressed-cycle test-billing refund path were ALL affected). These
tests exist so that gap can never reopen unnoticed.
"""
import stripe


def test_stripe_object_has_no_get_method():
    """The regression itself, isolated: confirms .get() is unsupported on a
    real Stripe SDK object, so future readers understand exactly why every
    webhook/service boundary below calls .to_dict()."""
    obj = stripe.Subscription.construct_from(
        {"id": "sub_x", "metadata": {"a": "b"}}, "sk_test_x"
    )
    assert not hasattr(obj, "get")
    assert obj["id"] == "sub_x"  # bracket access works
    assert obj.id == "sub_x"  # attribute access works


def test_to_dict_recursively_converts_nested_stripe_objects():
    """.to_dict() must convert nested objects too (metadata, items.data[...]),
    not just the top level -- a shallow conversion would still crash on the
    first nested .get() call."""
    obj = stripe.Subscription.construct_from(
        {
            "id": "sub_x",
            "metadata": {"autoRefund": "true"},
            "items": {"object": "list", "data": [{"id": "si_1", "price": {"id": "price_1"}}]},
        },
        "sk_test_x",
    )
    d = obj.to_dict()
    assert type(d) is dict
    assert type(d["metadata"]) is dict
    assert type(d["items"]) is dict
    assert type(d["items"]["data"][0]) is dict
    assert type(d["items"]["data"][0]["price"]) is dict
    # .get() now works at every level, exactly like the plain-dict test mocks
    # this codebase's handlers are written against.
    assert d["metadata"].get("autoRefund") == "true"
    assert d["items"]["data"][0]["price"].get("id") == "price_1"


def test_webhook_router_converts_event_object_before_dispatch():
    """router.py must call .to_dict() on event.data.object before handing it
    to WebhookHandler -- source-level guard against the exact regression."""
    import inspect

    from app.modules.payments import router as payments_router

    source = inspect.getsource(payments_router.stripe_webhook)
    assert "event.data.object.to_dict()" in source


def test_service_stripe_retrieve_calls_convert_before_get():
    """Every live stripe.X.retrieve()/create() result that service.py later
    calls .get() on must be converted first. Source-level guard: for each
    call, .to_dict() (or an attribute-access-only usage) must be present on
    the same logical assignment."""
    import inspect

    from app.modules.payments import service as payments_service

    source = inspect.getsource(payments_service)
    # Every one of these is a live SDK call whose result is read with .get()
    # somewhere downstream in the same function -- each must be paired with
    # .to_dict() at the call site.
    assert "stripe.Subscription.retrieve(subscription_id).to_dict()" in source
    assert "stripe.Price.retrieve(new_price_id).to_dict()" in source
    assert "stripe.Price.retrieve(real_price_id).to_dict()" in source
    assert "stripe.Invoice.retrieve(invoice.get(\"id\")).to_dict()" in source


def test_reconciler_stripe_retrieve_converts_before_get():
    import inspect

    from app.modules.payments import reconciler

    source = inspect.getsource(reconciler.run_subscription_reconciler)
    assert "stripe.Subscription.retrieve(sub_id).to_dict()" in source
