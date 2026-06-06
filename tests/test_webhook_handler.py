"""Tests for the Stripe webhook handler — subscription lifecycle."""

from datetime import datetime, timezone

from app.core.config import Settings
from app.modules.payments.webhook_handler import WebhookHandler, PLAN_REPORT_LIMITS


def _settings_with_prices() -> Settings:
    return Settings(
        _env_file=None,
        JWT_SECRET_KEY="x" * 64,
        STRIPE_PRICE_PROFESSIONAL_GBP="price_pro",
        STRIPE_PRICE_PRODUCER_GBP="price_producer",
        STRIPE_PRICE_STUDIO_GBP="price_studio",
    )


# ---------------------------------------------------------------------------
# Fake DatabaseClient that records writes
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class _FakeQuery:
    """Minimal query builder that records upserts/updates and supports eq()."""

    def __init__(self, store: dict, table_name: str):
        self._store = store
        self._table = table_name
        self._filters: dict = {}
        self._pending_data: dict | None = None
        self._op: str = ""

    # Builder methods ---
    def select(self, _cols):
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def gte(self, _key, _value):
        return self

    def lte(self, _key, _value):
        return self

    def limit(self, _n):
        return self

    def single(self):
        return self

    def upsert(self, data, on_conflict=None):
        self._op = "upsert"
        self._pending_data = data
        return self

    def update(self, data):
        self._op = "update"
        self._pending_data = data
        return self

    def insert(self, data):
        self._op = "insert"
        self._pending_data = data
        return self

    # Terminal ---
    def execute(self):
        key = f"{self._table}:{self._op}"
        self._store.setdefault(key, []).append({
            "data": self._pending_data,
            "filters": dict(self._filters),
        })
        # Return matching rows for update so handler can extract user_id
        if self._op == "update" and self._table == "subscriptions":
            return _FakeResult([{"user_id": "user-1", **(self._pending_data or {})}])
        # For deduplication lookup, return empty (no duplicate)
        if self._table == "processed_webhook_events" and self._op == "":
            return _FakeResult([])
        return _FakeResult([self._pending_data] if self._pending_data else [])


class FakeSupabase:
    def __init__(self):
        self.writes: dict[str, list] = {}

    def table(self, name):
        return _FakeQuery(self.writes, name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanReportLimits:
    def test_free_limit_is_1(self):
        assert PLAN_REPORT_LIMITS["free"] == 1

    def test_professional_limit_is_1(self):
        assert PLAN_REPORT_LIMITS["professional"] == 1

    def test_producer_limit_is_3(self):
        assert PLAN_REPORT_LIMITS["producer"] == 3

    def test_studio_limit_is_10(self):
        assert PLAN_REPORT_LIMITS["studio"] == 10


class TestCheckoutCompleted:
    def _make_session(self, plan_type="professional", user_id="user-1"):
        return {
            "metadata": {"userId": user_id, "planType": plan_type},
            "subscription": "sub_123",
            "customer": "cus_123",
        }

    def test_creates_subscription_with_professional_limits(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_1", "checkout.session.completed", self._make_session("professional"))

        sub_upserts = db.writes.get("subscriptions:upsert", [])
        assert len(sub_upserts) == 1
        sub = sub_upserts[0]["data"]
        assert sub["plan_type"] == "professional"
        assert sub["report_limit"] == 1
        assert sub["status"] == "active"

    def test_creates_subscription_with_producer_limits(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_2a", "checkout.session.completed", self._make_session("producer"))

        sub_upserts = db.writes.get("subscriptions:upsert", [])
        assert len(sub_upserts) == 1
        sub = sub_upserts[0]["data"]
        assert sub["plan_type"] == "producer"
        assert sub["report_limit"] == 3

    def test_creates_subscription_with_studio_limits(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_2", "checkout.session.completed", self._make_session("studio"))

        sub_upserts = db.writes.get("subscriptions:upsert", [])
        assert len(sub_upserts) == 1
        assert sub_upserts[0]["data"]["report_limit"] == 10

    def test_normalizes_legacy_single_to_professional(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_3", "checkout.session.completed", self._make_session("single"))

        sub_upserts = db.writes.get("subscriptions:upsert", [])
        assert sub_upserts[0]["data"]["plan_type"] == "professional"
        assert sub_upserts[0]["data"]["report_limit"] == 1

    def test_updates_user_plan_and_user_type(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_4", "checkout.session.completed", self._make_session("professional"))

        user_updates = db.writes.get("users:update", [])
        assert len(user_updates) == 1
        assert user_updates[0]["data"]["plan"] == "professional"
        assert user_updates[0]["data"]["user_type"] == "paid"
        assert user_updates[0]["filters"]["id"] == "user-1"

    def test_missing_user_id_does_nothing(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        session = {"metadata": {}, "subscription": "sub_999", "customer": "cus_999"}
        handler.handle_event("evt_5", "checkout.session.completed", session)

        assert "subscriptions:upsert" not in db.writes
        assert "users:update" not in db.writes

    def test_default_plan_is_professional_when_metadata_missing(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        session = {
            "metadata": {"userId": "user-1"},
            "subscription": "sub_123",
            "customer": "cus_123",
        }
        handler.handle_event("evt_6", "checkout.session.completed", session)

        sub_upserts = db.writes.get("subscriptions:upsert", [])
        assert sub_upserts[0]["data"]["plan_type"] == "professional"
        assert sub_upserts[0]["data"]["report_limit"] == 1


class TestSubscriptionDeleted:
    def test_cancels_subscription_and_downgrades_user(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_7", "customer.subscription.deleted", {"id": "sub_123"})

        sub_updates = db.writes.get("subscriptions:update", [])
        assert len(sub_updates) == 1
        assert sub_updates[0]["data"]["status"] == "cancelled"

        user_updates = db.writes.get("users:update", [])
        assert len(user_updates) == 1
        assert user_updates[0]["data"]["plan"] == "free"
        assert user_updates[0]["data"]["user_type"] == "free"

    def test_missing_subscription_id_does_nothing(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_8", "customer.subscription.deleted", {})

        assert "subscriptions:update" not in db.writes


class TestSubscriptionUpdated:
    def test_updates_period_dates(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_9", "customer.subscription.updated", {
            "id": "sub_123",
            "status": "active",
            "current_period_start": 1700000000,
            "current_period_end": 1702592000,
            "cancel_at_period_end": False,
        })

        sub_updates = db.writes.get("subscriptions:update", [])
        assert len(sub_updates) >= 1
        data = sub_updates[0]["data"]
        assert data["status"] == "active"
        assert data["cancel_at_period_end"] is False

    def test_writes_plan_from_price_id_and_mirrors_to_user(self):
        """The single fix that unlocks Subscription.modify upgrades."""
        db = FakeSupabase()
        handler = WebhookHandler(db, _settings_with_prices())
        handler.handle_event("evt_upgrade", "customer.subscription.updated", {
            "id": "sub_123",
            "status": "active",
            "current_period_start": 1700000000,
            "current_period_end": 1702592000,
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": "price_producer"}}]},
        })

        sub_updates = db.writes.get("subscriptions:update", [])
        sub_payload = sub_updates[0]["data"]
        assert sub_payload["plan_type"] == "producer"
        assert sub_payload["report_limit"] == PLAN_REPORT_LIMITS["producer"]

        user_updates = db.writes.get("users:update", [])
        assert any(u["data"].get("plan") == "producer" for u in user_updates)
        assert any(u["data"].get("user_type") == "paid" for u in user_updates)

    def test_unknown_price_id_skips_plan_update(self):
        db = FakeSupabase()
        handler = WebhookHandler(db, _settings_with_prices())
        handler.handle_event("evt_unknown", "customer.subscription.updated", {
            "id": "sub_123",
            "status": "active",
            "items": {"data": [{"price": {"id": "price_not_in_catalog"}}]},
        })
        sub_updates = db.writes.get("subscriptions:update", [])
        assert "plan_type" not in sub_updates[0]["data"]
        # No mirror to user when plan can't be resolved.
        assert "users:update" not in db.writes


class TestWebhookDeduplication:
    def test_duplicate_event_skipped(self):
        """When a processed_webhook_events lookup returns a row, the event is skipped."""

        class DedupSupabase:
            def __init__(self):
                self.writes: dict[str, list] = {}

            def table(self, name):
                if name == "processed_webhook_events":
                    return _DedupQuery(self.writes, already_processed=True)
                return _FakeQuery(self.writes, name)

        class _DedupQuery(_FakeQuery):
            def __init__(self, store, already_processed=False):
                super().__init__(store, "processed_webhook_events")
                self._already_processed = already_processed

            def execute(self):
                if self._op == "" and self._already_processed:
                    return _FakeResult([{"event_id": "evt_dup"}])
                return super().execute()

        db = DedupSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_dup", "checkout.session.completed", {
            "metadata": {"userId": "user-1", "planType": "professional"},
            "subscription": "sub_123",
            "customer": "cus_123",
        })

        # Should NOT have created a subscription because event was deduplicated
        assert "subscriptions:upsert" not in db.writes


# ---------------------------------------------------------------------------
# Scheduled-downgrade rollover edge cases (#7, #8) — stateful fake that returns
# a real pre-update row carrying pending_plan / stripe_schedule_id.
# ---------------------------------------------------------------------------

class _SchedResult:
    def __init__(self, data):
        self.data = data


class _SchedQuery:
    def __init__(self, store: dict, table: str):
        self._store = store
        self._table = table
        self._filters: dict = {}
        self._op = ""
        self._payload = None

    def select(self, _cols):
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def limit(self, _n):
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def _matches(self, row):
        return all(row.get(k) == v for k, v in self._filters.items())

    def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "update":
            patched = []
            for row in rows:
                if self._matches(row):
                    row.update(self._payload)
                    patched.append(row)
            self._store.setdefault(f"{self._table}:update", []).append(dict(self._payload))
            return _SchedResult(patched)
        return _SchedResult([row for row in rows if self._matches(row)])


class _SchedSupabase:
    def __init__(self, subscriptions, users):
        self.store = {"subscriptions": subscriptions, "users": users}

    def table(self, name):
        return _SchedQuery(self.store, name)


def _sub_event(price_id=None, schedule=None):
    sub = {
        "id": "sub_1",
        "status": "active",
        "cancel_at_period_end": False,
        "current_period_start": 1700000000,
        "current_period_end": 1702592000,
        "schedule": schedule,
    }
    if price_id is not None:
        sub["items"] = {"data": [{"price": {"id": price_id}}]}
    return sub


def _handler_with_settings(db):
    handler = WebhookHandler(db, _settings_with_prices())
    handler.email_service = None  # keep settings (for price resolution), skip SMTP
    return handler


class TestScheduledDowngradeRollover:
    def _seed(self, **sub_overrides):
        sub = {
            "stripe_subscription_id": "sub_1",
            "user_id": "user-1",
            "plan_type": "studio",
            "pending_plan": "professional",
            "stripe_schedule_id": "sched_1",
            "status": "active",
        }
        sub.update(sub_overrides)
        return _SchedSupabase([sub], [{"id": "user-1", "plan": "studio", "user_type": "paid"}])

    def test_rollover_clears_pending_and_applies_plan(self):
        """Normal rollover: schedule gone, resolved plan == pending_plan."""
        db = self._seed()
        _handler_with_settings(db)._handle_subscription_updated(
            _sub_event(price_id="price_pro", schedule=None)
        )
        sub = db.store["subscriptions"][0]
        assert sub["plan_type"] == "professional"
        assert sub["report_limit"] == PLAN_REPORT_LIMITS["professional"]
        assert sub["pending_plan"] is None
        assert sub["stripe_schedule_id"] is None
        assert db.store["users"][0]["plan"] == "professional"

    def test_8_schedule_fired_but_resolved_differs_still_clears_pending(self):
        """#8 — schedule fired onto a DIFFERENT plan (e.g. changed out-of-band).
        pending markers must still be cleared, and the actual plan applied."""
        db = self._seed()  # pending_plan="professional"
        _handler_with_settings(db)._handle_subscription_updated(
            _sub_event(price_id="price_producer", schedule=None)  # lands on producer
        )
        sub = db.store["subscriptions"][0]
        assert sub["plan_type"] == "producer"
        assert sub["pending_plan"] is None
        assert sub["stripe_schedule_id"] is None
        assert db.store["users"][0]["plan"] == "producer"

    def test_7_unresolvable_price_falls_back_to_pending_plan(self, caplog):
        """#7 — price ID not configured in settings. After the schedule fires we
        must fall back to the recorded pending_plan, not strand the user."""
        import logging

        db = self._seed()  # pending_plan="professional"
        with caplog.at_level(logging.ERROR):
            _handler_with_settings(db)._handle_subscription_updated(
                _sub_event(price_id="price_UNCONFIGURED", schedule=None)
            )
        sub = db.store["subscriptions"][0]
        assert sub["plan_type"] == "professional"
        assert sub["report_limit"] == PLAN_REPORT_LIMITS["professional"]
        assert sub["pending_plan"] is None
        assert sub["stripe_schedule_id"] is None
        assert db.store["users"][0]["plan"] == "professional"
        assert any("falling back to" in r.message.lower() or "pending_plan" in r.message
                   for r in caplog.records)

    def test_pending_not_cleared_while_schedule_still_active(self):
        """Guard: a subscription.updated during the pending window (schedule still
        present, e.g. a card update) must NOT clear pending or downgrade early."""
        db = self._seed()
        # Still on studio price, schedule still attached.
        _handler_with_settings(db)._handle_subscription_updated(
            _sub_event(price_id="price_studio", schedule="sched_1")
        )
        sub = db.store["subscriptions"][0]
        assert sub["plan_type"] == "studio"
        assert sub["pending_plan"] == "professional"
        assert sub["stripe_schedule_id"] == "sched_1"
        assert db.store["users"][0]["plan"] == "studio"
