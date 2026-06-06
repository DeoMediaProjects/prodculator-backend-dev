"""Tests for the subscription reconciler — drift detection between local and Stripe."""

from unittest.mock import patch

from app.core.config import Settings
from app.modules.payments.reconciler import _diff_subscription, run_subscription_reconciler


class _FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class _ReconcilerQuery:
    def __init__(self, store: dict, table: str, fixture: dict):
        self._store = store
        self._table = table
        self._fixture = fixture
        self._filters: dict = {}
        self._in: dict = {}
        self._op = ""
        self._pending: dict | None = None

    def select(self, _cols):
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def in_(self, key, values):
        self._in[key] = values
        return self

    def update(self, data):
        self._op = "update"
        self._pending = data
        return self

    def execute(self):
        if self._op == "update":
            self._store.setdefault(f"{self._table}:update", []).append(
                {"data": self._pending, "filters": dict(self._filters)}
            )
            return _FakeResult([self._pending])

        if self._table == "subscriptions" and self._in:
            return _FakeResult(self._fixture.get("subscriptions", []))

        return _FakeResult([])


class FakeReconcilerSupabase:
    def __init__(self, fixture):
        self.fixture = fixture
        self.writes: dict[str, list] = {}

    def table(self, name):
        return _ReconcilerQuery(self.writes, name, self.fixture)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        JWT_SECRET_KEY="x" * 64,
        STRIPE_SECRET_KEY="sk_test_dummy",
        STRIPE_PRICE_PROFESSIONAL_GBP="price_pro",
        STRIPE_PRICE_PRODUCER_GBP="price_producer",
        REDIS_URL="redis://localhost:6379/0",
    )


class TestDiffSubscription:
    def test_no_drift_returns_empty(self):
        local = {
            "status": "active",
            "cancel_at_period_end": False,
            "plan_type": "producer",
            "current_period_start": "2026-01-01T00:00:00+00:00",
            "current_period_end": "2026-02-01T00:00:00+00:00",
        }
        stripe_sub = {
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_start": 1767225600,  # 2026-01-01 00:00 UTC
            "current_period_end": 1769904000,    # 2026-02-01 00:00 UTC
            "items": {"data": [{"price": {"id": "price_producer"}}]},
        }
        assert _diff_subscription(local, stripe_sub, _settings()) == {}

    def test_detects_plan_drift(self):
        local = {"status": "active", "plan_type": "professional"}
        stripe_sub = {
            "status": "active",
            "items": {"data": [{"price": {"id": "price_producer"}}]},
        }
        diff = _diff_subscription(local, stripe_sub, _settings())
        assert diff["plan_type"] == "producer"
        assert "report_limit" in diff

    def test_detects_status_drift(self):
        local = {"status": "active", "plan_type": "producer"}
        stripe_sub = {
            "status": "past_due",
            "items": {"data": [{"price": {"id": "price_producer"}}]},
        }
        diff = _diff_subscription(local, stripe_sub, _settings())
        assert diff["status"] == "past_due"

    def test_6_clears_stale_pending_when_schedule_gone(self):
        """#6 — rollover webhook applied the plan but left pending_plan set. The
        reconciler clears the stale markers once Stripe reports no schedule."""
        local = {
            "stripe_subscription_id": "sub_1",
            "status": "active",
            "plan_type": "professional",  # already correct — no plan drift
            "pending_plan": "professional",
            "stripe_schedule_id": "sched_1",
        }
        stripe_sub = {
            "status": "active",
            "schedule": None,
            "items": {"data": [{"price": {"id": "price_pro"}}]},
        }
        diff = _diff_subscription(local, stripe_sub, _settings())
        assert diff["pending_plan"] is None
        assert diff["stripe_schedule_id"] is None
        assert "plan_type" not in diff  # plan already correct

    def test_8_schedule_fired_to_different_plan_clears_pending(self):
        """#8 — schedule fired onto a plan that differs from pending_plan."""
        local = {
            "stripe_subscription_id": "sub_1",
            "status": "active",
            "plan_type": "studio",
            "pending_plan": "professional",
            "stripe_schedule_id": "sched_1",
        }
        stripe_sub = {
            "status": "active",
            "schedule": None,
            "items": {"data": [{"price": {"id": "price_producer"}}]},  # landed on producer
        }
        diff = _diff_subscription(local, stripe_sub, _settings())
        assert diff["plan_type"] == "producer"
        assert diff["pending_plan"] is None
        assert diff["stripe_schedule_id"] is None

    def test_7_unresolvable_price_falls_back_to_pending(self):
        """#7 — price not configured; fall back to recorded pending_plan once the
        schedule has fired."""
        local = {
            "stripe_subscription_id": "sub_1",
            "status": "active",
            "plan_type": "studio",
            "pending_plan": "professional",
            "stripe_schedule_id": "sched_1",
        }
        stripe_sub = {
            "status": "active",
            "schedule": None,
            "items": {"data": [{"price": {"id": "price_UNCONFIGURED"}}]},
        }
        diff = _diff_subscription(local, stripe_sub, _settings())
        assert diff["plan_type"] == "professional"
        assert diff["report_limit"] == 1
        assert diff["pending_plan"] is None

    def test_pending_kept_while_schedule_still_active(self):
        """Guard: while the schedule is still attached, markers are untouched."""
        local = {
            "stripe_subscription_id": "sub_1",
            "status": "active",
            "plan_type": "studio",
            "pending_plan": "professional",
            "stripe_schedule_id": "sched_1",
        }
        stripe_sub = {
            "status": "active",
            "schedule": "sched_1",
            "items": {"data": [{"price": {"id": "price_studio"}}]},
        }
        diff = _diff_subscription(local, stripe_sub, _settings())
        assert "pending_plan" not in diff
        assert "stripe_schedule_id" not in diff
        assert "plan_type" not in diff


class TestReconcilerRun:
    def test_fixes_plan_drift_and_mirrors_to_user(self):
        db = FakeReconcilerSupabase({
            "subscriptions": [
                {
                    "id": "sub-row-1",
                    "user_id": "user-1",
                    "stripe_subscription_id": "sub_stripe_1",
                    "status": "active",
                    "plan_type": "professional",
                    "cancel_at_period_end": False,
                },
            ],
        })

        fake_stripe_sub = {
            "id": "sub_stripe_1",
            "status": "active",
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": "price_producer"}}]},
        }

        with patch("app.modules.payments.reconciler.stripe.Subscription.retrieve", return_value=fake_stripe_sub):
            count = run_subscription_reconciler(db, _settings())

        assert count == 1
        sub_updates = db.writes.get("subscriptions:update", [])
        assert sub_updates[0]["data"]["plan_type"] == "producer"

        user_updates = db.writes.get("users:update", [])
        assert any(u["data"].get("plan") == "producer" for u in user_updates)

    def test_no_drift_no_writes(self):
        db = FakeReconcilerSupabase({
            "subscriptions": [
                {
                    "id": "sub-row-1",
                    "user_id": "user-1",
                    "stripe_subscription_id": "sub_stripe_1",
                    "status": "active",
                    "plan_type": "producer",
                    "cancel_at_period_end": False,
                },
            ],
        })

        fake_stripe_sub = {
            "id": "sub_stripe_1",
            "status": "active",
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": "price_producer"}}]},
        }

        with patch("app.modules.payments.reconciler.stripe.Subscription.retrieve", return_value=fake_stripe_sub):
            count = run_subscription_reconciler(db, _settings())

        assert count == 0
        assert "subscriptions:update" not in db.writes
        assert "users:update" not in db.writes

    def test_skips_when_stripe_secret_missing(self):
        db = FakeReconcilerSupabase({"subscriptions": []})
        s = Settings(_env_file=None, JWT_SECRET_KEY="x" * 64, STRIPE_SECRET_KEY="")
        assert run_subscription_reconciler(db, s) == 0
