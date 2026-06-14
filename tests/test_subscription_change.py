"""Integration tests for the native plan-change endpoints (Phase 1)."""

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_supabase
from app.modules.subscriptions.router import get_stripe_service


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class _SubscriptionsQuery:
    """Fake query for the subscriptions table that supports the patterns used
    by SubscriptionService.get_active_subscription (eq + in_) and update."""

    def __init__(self, store: dict, rows: list):
        self._store = store
        self._rows = rows
        self._filters: dict = {}
        self._in: dict = {}
        self._op = ""
        self._pending: dict | None = None

    def select(self, _v):
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def in_(self, key, values):
        self._in[key] = list(values)
        return self

    def limit(self, _n):
        return self

    def update(self, data):
        self._op = "update"
        self._pending = data
        return self

    def execute(self):
        if self._op == "update":
            self._store.setdefault("subscriptions:update", []).append(
                {"data": self._pending, "filters": dict(self._filters)}
            )
            return _FakeResult([self._pending])

        # Read: filter by eq + in_ from rows.
        filtered = [
            row for row in self._rows
            if all(row.get(k) == v for k, v in self._filters.items())
            and all(row.get(k) in vs for k, vs in self._in.items())
        ]
        return _FakeResult(filtered)


class FakeSupabase:
    def __init__(self, subscriptions: list | None = None):
        self.subscriptions = subscriptions or []
        self.writes: dict = {}

    def table(self, name):
        if name == "subscriptions":
            return _SubscriptionsQuery(self.writes, self.subscriptions)
        return _SubscriptionsQuery(self.writes, [])


class FakeStripeService:
    """Records the calls made by the route handlers, returns canned data."""

    def __init__(self):
        self.preview_calls: list = []
        self.modify_calls: list = []
        self.schedule_calls: list = []

    def preview_subscription_change(self, subscription_id, new_price_id):
        self.preview_calls.append((subscription_id, new_price_id))
        return {
            "immediate_total": 1500,
            "proration_credit": 500,
            "next_invoice_total": 4900,
            "currency": "usd",
            "period_end": 1772000000,
        }

    def change_subscription_plan(
        self, subscription_id, new_price_id, *, prorate, idempotency_key=None
    ):
        self.modify_calls.append(
            {
                "subscription_id": subscription_id,
                "new_price_id": new_price_id,
                "prorate": prorate,
                "idempotency_key": idempotency_key,
            }
        )
        return {"id": subscription_id, "status": "active"}

    def schedule_subscription_downgrade(self, subscription_id, new_price_id):
        # Downgrades defer to period end via a Stripe Subscription Schedule
        # rather than an immediate, prorated plan change.
        self.schedule_calls.append(
            {"subscription_id": subscription_id, "new_price_id": new_price_id}
        )
        return "sched_test_123"


def _override(client, user, fake_db, fake_stripe):
    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_supabase] = lambda: fake_db
    client.app.dependency_overrides[get_stripe_service] = lambda: fake_stripe


def _settings_with_prices(monkeypatch):
    """Patch the cached settings instance to know our test price IDs."""
    s = get_settings()
    monkeypatch.setattr(s, "STRIPE_PRICE_PROFESSIONAL_GBP", "price_pro")
    monkeypatch.setattr(s, "STRIPE_PRICE_PRODUCER_GBP", "price_producer")
    monkeypatch.setattr(s, "STRIPE_PRICE_STUDIO_GBP", "price_studio")


def _make_paid_user(plan="professional"):
    from app.modules.auth.schemas import AuthUser
    return AuthUser(
        id="user-1",
        email="user@example.com",
        user_type="paid",
        credits_remaining=0,
        plan=plan,
    )


# ---------------------------------------------------------------------------
# /current
# ---------------------------------------------------------------------------


class TestGetCurrent:
    def test_returns_subscription_with_pending_and_status(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        user = _make_paid_user("producer")
        db = FakeSupabase([
            {
                "id": "sub-1",
                "user_id": "user-1",
                "stripe_subscription_id": "sub_123",
                "status": "active",
                "plan_type": "producer",
                "pending_plan": "professional",
                "cancel_at_period_end": False,
                "past_due_since": None,
            }
        ])
        _override(client, user, db, FakeStripeService())

        response = client.get(
            "/api/subscriptions/current", headers={"Authorization": "Bearer t"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["plan"] == "producer"
        assert body["pending_plan"] == "professional"
        assert body["cancel_at_period_end"] is False
        assert body["subscription"]["stripe_subscription_id"] == "sub_123"

    def test_returns_free_user_with_no_subscription(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        from app.modules.auth.schemas import AuthUser

        user = AuthUser(
            id="user-1", email="u@e.com", user_type="free", credits_remaining=0, plan="free"
        )
        db = FakeSupabase([])
        _override(client, user, db, FakeStripeService())

        response = client.get(
            "/api/subscriptions/current", headers={"Authorization": "Bearer t"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["plan"] == "free"
        assert body["subscription"] is None


# ---------------------------------------------------------------------------
# /preview-change
# ---------------------------------------------------------------------------


class TestPreviewChange:
    def test_upgrade_preview_returns_proration(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        user = _make_paid_user("professional")
        db = FakeSupabase([
            {
                "id": "sub-1",
                "user_id": "user-1",
                "stripe_subscription_id": "sub_123",
                "status": "active",
                "plan_type": "professional",
            }
        ])
        stripe_svc = FakeStripeService()
        _override(client, user, db, stripe_svc)

        response = client.post(
            "/api/subscriptions/preview-change",
            headers={"Authorization": "Bearer t"},
            json={"target_price_id": "price_producer"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["direction"] == "upgrade"
        assert body["target_plan"] == "producer"
        assert body["immediate_total"] == 1500
        assert body["proration_credit"] == 500
        assert stripe_svc.preview_calls == [("sub_123", "price_producer")]

    def test_no_active_subscription_returns_404(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        user = _make_paid_user("free")
        db = FakeSupabase([])
        _override(client, user, db, FakeStripeService())

        response = client.post(
            "/api/subscriptions/preview-change",
            headers={"Authorization": "Bearer t"},
            json={"target_price_id": "price_producer"},
        )
        assert response.status_code == 404

    def test_unknown_price_id_returns_400(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        user = _make_paid_user("professional")
        db = FakeSupabase([
            {
                "id": "sub-1",
                "user_id": "user-1",
                "stripe_subscription_id": "sub_123",
                "status": "active",
                "plan_type": "professional",
            }
        ])
        _override(client, user, db, FakeStripeService())

        response = client.post(
            "/api/subscriptions/preview-change",
            headers={"Authorization": "Bearer t"},
            json={"target_price_id": "price_not_in_catalog"},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# /change
# ---------------------------------------------------------------------------


class TestChangePlan:
    def test_upgrade_calls_stripe_with_proration(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        user = _make_paid_user("professional")
        db = FakeSupabase([
            {
                "id": "sub-1",
                "user_id": "user-1",
                "stripe_subscription_id": "sub_123",
                "status": "active",
                "plan_type": "professional",
            }
        ])
        stripe_svc = FakeStripeService()
        _override(client, user, db, stripe_svc)

        response = client.post(
            "/api/subscriptions/change",
            headers={"Authorization": "Bearer t"},
            json={"target_price_id": "price_producer", "idempotency_key": "key-1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "applied"
        assert body["direction"] == "upgrade"
        assert body["target_plan"] == "producer"
        assert stripe_svc.modify_calls[0]["prorate"] is True
        assert stripe_svc.modify_calls[0]["idempotency_key"] == "key-1"

    def test_downgrade_calls_stripe_without_proration_and_writes_pending_plan(
        self, client, monkeypatch
    ):
        _settings_with_prices(monkeypatch)
        user = _make_paid_user("studio")
        db = FakeSupabase([
            {
                "id": "sub-1",
                "user_id": "user-1",
                "stripe_subscription_id": "sub_123",
                "status": "active",
                "plan_type": "studio",
                "current_period_end": "2026-05-24T00:00:00+00:00",
            }
        ])
        stripe_svc = FakeStripeService()
        _override(client, user, db, stripe_svc)

        response = client.post(
            "/api/subscriptions/change",
            headers={"Authorization": "Bearer t"},
            json={"target_price_id": "price_professional"
                  if False else "price_pro",  # GBP key from settings
                  "idempotency_key": "key-down"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "scheduled"
        assert body["direction"] == "downgrade"
        assert body["target_plan"] == "professional"
        # Downgrade defers via a Subscription Schedule — no immediate, prorated
        # plan change. change_subscription_plan must NOT be called.
        assert stripe_svc.modify_calls == []
        assert stripe_svc.schedule_calls[0]["subscription_id"] == "sub_123"
        assert stripe_svc.schedule_calls[0]["new_price_id"] == "price_pro"

        sub_updates = db.writes.get("subscriptions:update", [])
        assert any(u["data"].get("pending_plan") == "professional" for u in sub_updates)
        assert any(u["data"].get("stripe_schedule_id") == "sched_test_123" for u in sub_updates)

    def test_same_plan_returns_400(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        user = _make_paid_user("producer")
        db = FakeSupabase([
            {
                "id": "sub-1",
                "user_id": "user-1",
                "stripe_subscription_id": "sub_123",
                "status": "active",
                "plan_type": "producer",
            }
        ])
        _override(client, user, db, FakeStripeService())

        response = client.post(
            "/api/subscriptions/change",
            headers={"Authorization": "Bearer t"},
            json={"target_price_id": "price_producer", "idempotency_key": "key-x"},
        )
        assert response.status_code == 400

    def test_no_active_subscription_returns_404(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        user = _make_paid_user("free")
        db = FakeSupabase([])
        _override(client, user, db, FakeStripeService())

        response = client.post(
            "/api/subscriptions/change",
            headers={"Authorization": "Bearer t"},
            json={"target_price_id": "price_producer", "idempotency_key": "k"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 409 guard on subscription-checkout
# ---------------------------------------------------------------------------


class TestSubscriptionCheckoutGuard:
    def test_existing_subscriber_blocked_with_409(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        user = _make_paid_user("professional")
        db = FakeSupabase([
            {
                "id": "sub-1",
                "user_id": "user-1",
                "stripe_subscription_id": "sub_existing",
                "status": "active",
                "plan_type": "professional",
            }
        ])
        # Stripe service shouldn't be called — we expect rejection before it.
        from app.modules.payments.router import get_stripe_service as payments_get_stripe

        client.app.dependency_overrides[get_current_user] = lambda: user
        client.app.dependency_overrides[get_supabase] = lambda: db
        client.app.dependency_overrides[payments_get_stripe] = lambda: FakeStripeService()

        response = client.post(
            "/api/payments/subscription-checkout",
            headers={"Authorization": "Bearer t"},
            json={"price_id": "price_producer", "plan_type": "producer"},
        )
        assert response.status_code == 409
        body = response.json()
        assert body["detail"]["error"] == "existing_subscription"
        assert body["detail"]["subscription_id"] == "sub_existing"

    def test_new_subscriber_passes_through(self, client, monkeypatch):
        _settings_with_prices(monkeypatch)
        from app.modules.auth.schemas import AuthUser

        user = AuthUser(
            id="user-1", email="u@e.com", user_type="free", credits_remaining=0, plan="free"
        )
        db = FakeSupabase([])

        from app.modules.payments.router import get_stripe_service as payments_get_stripe

        class _StripeOk:
            def create_subscription_checkout(self, **kw):
                return {"session_id": "cs_x", "url": "https://checkout/x"}

        client.app.dependency_overrides[get_current_user] = lambda: user
        client.app.dependency_overrides[get_supabase] = lambda: db
        client.app.dependency_overrides[payments_get_stripe] = lambda: _StripeOk()

        response = client.post(
            "/api/payments/subscription-checkout",
            headers={"Authorization": "Bearer t"},
            json={"price_id": "price_producer", "plan_type": "producer"},
        )
        assert response.status_code == 200
        assert response.json()["url"].startswith("https://checkout/")
