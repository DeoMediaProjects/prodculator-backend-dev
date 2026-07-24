import stripe

from app.core.config import Settings, get_settings
from app.core.dependencies import get_current_user, get_supabase
from app.modules.payments import router as payments_router
from app.modules.payments.router import get_stripe_service


class FakeStripeService:
    @staticmethod
    def create_checkout_session(price_id: str, user_email: str, user_id: str, metadata=None):
        return {"session_id": "cs_test", "url": "https://checkout.stripe.test"}

    @staticmethod
    def create_subscription_checkout(price_id: str, user_email: str, user_id: str, metadata=None):
        return {"session_id": "cs_sub", "url": "https://checkout.stripe.test/sub"}

    @staticmethod
    def cancel_subscription(subscription_id: str):
        return None

    @staticmethod
    def create_customer_portal_session(customer_id: str):
        return "https://billing.stripe.test"

    @staticmethod
    def update_payment_method(customer_id: str, payment_method_id: str):
        return None


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}

    def select(self, _value):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, _key, _values):
        # Membership filter (e.g. status in active/trialing/past_due). The tests
        # that hit this path seed no subscription rows, so it just needs to exist.
        return self

    def limit(self, _value):
        return self

    def execute(self):
        filtered = [
            row
            for row in self.rows
            if all(row.get(filter_key) == filter_val for filter_key, filter_val in self.filters.items())
        ]

        class Result:
            data = filtered

        return Result()


class FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return FakeQuery(self.rows)


def test_checkout_and_update_payment_method_success(client, auth_user):
    subscriptions = [
        {
            "id": "sub-row-1",
            "user_id": auth_user.id,
            "stripe_subscription_id": "sub_123",
            "stripe_customer_id": "cus_123",
        }
    ]
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase(subscriptions)
    client.app.dependency_overrides[get_stripe_service] = lambda: FakeStripeService()

    checkout_response = client.post(
        "/api/payments/checkout",
        headers={"Authorization": "Bearer token"},
        json={"price_id": "price_123"},
    )
    assert checkout_response.status_code == 200
    assert checkout_response.json()["session_id"] == "cs_test"

    update_response = client.post(
        "/api/payments/update-payment-method",
        headers={"Authorization": "Bearer token"},
        json={"customer_id": "cus_123", "payment_method_id": "pm_123"},
    )
    assert update_response.status_code == 200


class _CapturingStripeService:
    """Records the price_id the endpoint resolved before calling Stripe."""

    def __init__(self):
        self.received = {}

    def create_subscription_checkout(self, price_id, user_email, user_id, metadata=None):
        self.received["price_id"] = price_id
        return {"session_id": "cs_resolved", "url": "https://checkout.stripe.test/resolved"}


def _settings_with_prices():
    return Settings(
        _env_file=None,
        JWT_SECRET_KEY="x" * 64,
        STRIPE_PRICE_PROFESSIONAL_GBP="price_prof_gbp_live",
        STRIPE_PRICE_PROFESSIONAL_USD="price_prof_usd_live",
        STRIPE_PRICE_PROFESSIONAL_ANNUAL_GBP="price_prof_annual_gbp_live",
    )


def test_subscription_checkout_resolves_price_when_client_sends_empty(client, auth_user):
    """The live bug: a frontend build without VITE_STRIPE_PRICE_* baked in sends
    price_id="". The backend must resolve the price from plan/currency/cycle out
    of its own config instead of forwarding an empty string to Stripe (which 400s
    with 'You passed an empty string for line_items[0][price]')."""
    capturing = _CapturingStripeService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase([])
    client.app.dependency_overrides[get_stripe_service] = lambda: capturing
    client.app.dependency_overrides[get_settings] = _settings_with_prices

    resp = client.post(
        "/api/payments/subscription-checkout",
        headers={"Authorization": "Bearer token"},
        json={"price_id": "", "plan_type": "professional", "currency": "gbp", "billing_cycle": "monthly"},
    )
    assert resp.status_code == 200
    assert capturing.received["price_id"] == "price_prof_gbp_live"

    client.app.dependency_overrides.pop(get_settings, None)


def test_subscription_checkout_honours_nonempty_client_price(client, auth_user):
    """A non-empty client price_id is still honoured (keeps local dev, where the
    frontend DOES bake the price, working unchanged)."""
    capturing = _CapturingStripeService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase([])
    client.app.dependency_overrides[get_stripe_service] = lambda: capturing
    client.app.dependency_overrides[get_settings] = _settings_with_prices

    resp = client.post(
        "/api/payments/subscription-checkout",
        headers={"Authorization": "Bearer token"},
        json={"price_id": "price_client_supplied", "plan_type": "professional", "currency": "gbp"},
    )
    assert resp.status_code == 200
    assert capturing.received["price_id"] == "price_client_supplied"

    client.app.dependency_overrides.pop(get_settings, None)


def test_subscription_checkout_annual_cycle_resolves_annual_price(client, auth_user):
    capturing = _CapturingStripeService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase([])
    client.app.dependency_overrides[get_stripe_service] = lambda: capturing
    client.app.dependency_overrides[get_settings] = _settings_with_prices

    resp = client.post(
        "/api/payments/subscription-checkout",
        headers={"Authorization": "Bearer token"},
        json={"price_id": "", "plan_type": "professional", "currency": "gbp", "billing_cycle": "annual"},
    )
    assert resp.status_code == 200
    assert capturing.received["price_id"] == "price_prof_annual_gbp_live"

    client.app.dependency_overrides.pop(get_settings, None)


def test_subscription_checkout_400_when_price_unconfigured(client, auth_user):
    """When neither the client nor the server has a price for the requested
    plan/currency/cycle, respond with a clear 400 naming the missing env var --
    never forward an empty string to Stripe."""
    capturing = _CapturingStripeService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase([])
    client.app.dependency_overrides[get_stripe_service] = lambda: capturing
    client.app.dependency_overrides[get_settings] = _settings_with_prices

    resp = client.post(
        "/api/payments/subscription-checkout",
        headers={"Authorization": "Bearer token"},
        # producer/usd is not configured in _settings_with_prices
        json={"price_id": "", "plan_type": "producer", "currency": "usd", "billing_cycle": "monthly"},
    )
    assert resp.status_code == 400
    assert "STRIPE_PRICE_PRODUCER_USD" in resp.json()["detail"]
    assert "price_id" not in capturing.received  # Stripe was never called

    client.app.dependency_overrides.pop(get_settings, None)


def test_cancel_subscription_denied_when_not_owner(client, auth_user):
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase([])
    client.app.dependency_overrides[get_stripe_service] = lambda: FakeStripeService()

    response = client.post(
        "/api/payments/cancel-subscription",
        headers={"Authorization": "Bearer token"},
        json={"subscription_id": "sub_not_owned"},
    )
    assert response.status_code == 403


def test_webhook_bad_signature_returns_400(client):
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase([])
    response = client.post(
        "/api/webhooks/stripe",
        data=b"{}",
        headers={"stripe-signature": "invalid"},
    )
    assert response.status_code == 400


def test_webhook_dispatches_when_signature_valid(client, monkeypatch):
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase([])

    class FakeEvent:
        id = "evt_test_123"
        type = "checkout.session.completed"

        class Data:
            # A real stripe.StripeObject, not a plain dict -- router.py calls
            # .to_dict() on event.data.object.
            object = stripe.Subscription.construct_from({"id": "evt_obj"}, "sk_test_x")

        data = Data()

    def fake_construct(self, payload, sig_header):
        return FakeEvent()

    handled = {"called": False}

    def fake_handle(self, event_id, event_type, data_object):
        handled["called"] = True
        handled["event_type"] = event_type
        handled["data_object"] = data_object

    monkeypatch.setattr(payments_router.StripeService, "construct_webhook_event", fake_construct)
    monkeypatch.setattr(payments_router.WebhookHandler, "handle_event", fake_handle)

    response = client.post(
        "/api/webhooks/stripe",
        data=b"{}",
        headers={"stripe-signature": "ok"},
    )
    assert response.status_code == 200
    assert handled["called"] is True
