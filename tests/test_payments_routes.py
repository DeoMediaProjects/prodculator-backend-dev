from app.core.dependencies import get_current_user, get_supabase
from app.modules.payments import router as payments_router
from app.modules.payments.router import get_stripe_service


class FakeStripeService:
    @staticmethod
    def create_checkout_session(price_id: str, user_email: str, user_id: str, metadata=None):
        return {"session_id": "cs_test", "url": "https://checkout.stripe.test"}

    @staticmethod
    def create_subscription_checkout(price_id: str, user_email: str, user_id: str):
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
        type = "checkout.session.completed"

        class Data:
            object = {"id": "evt_obj"}

        data = Data()

    def fake_construct(self, payload, sig_header):
        return FakeEvent()

    handled = {"called": False}

    def fake_handle(self, event_type, data_object):
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
