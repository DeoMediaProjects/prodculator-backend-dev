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

    def test_captures_billing_country_and_state(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        session = self._make_session("professional")
        session["customer_details"] = {"address": {"country": "US", "state": "CA"}}
        handler.handle_event("evt_geo", "checkout.session.completed", session)

        # Billing geo is written in its own best-effort UPDATE, decoupled from the
        # critical plan upgrade so a geo-write failure can't strand the customer.
        user_updates = db.writes.get("users:update", [])
        geo_writes = [u for u in user_updates if "country" in u["data"]]
        assert len(geo_writes) == 1
        assert geo_writes[0]["data"] == {"country": "US", "state": "CA"}

        # The plan upgrade is its own UPDATE and carries no geo fields.
        plan_writes = [u for u in user_updates if "plan" in u["data"]]
        assert len(plan_writes) == 1
        assert "country" not in plan_writes[0]["data"]

    def test_missing_billing_address_does_not_set_geo(self):
        db = FakeSupabase()
        handler = WebhookHandler(db)
        handler.handle_event("evt_nogeo", "checkout.session.completed", self._make_session("professional"))

        user_updates = db.writes.get("users:update", [])
        assert "country" not in user_updates[0]["data"]
        assert "state" not in user_updates[0]["data"]

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


# ---------------------------------------------------------------------------
# Real-DB regression: a failing billing-geo write must not block the upgrade.
#
# The fake-DB tests above never validate columns, so they can't catch the class
# of bug where the user UPDATE itself fails. These run against a real (SQLite)
# DatabaseClient with the billing-geo columns ABSENT — exactly the production
# state if the add_user_billing_geo migration hasn't been applied — and assert
# the paying customer is still upgraded.
# ---------------------------------------------------------------------------

import tempfile

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as SASession

import app.core.database_client as dbc
from app.core.database_client import DatabaseClient


def _make_sqlite_db(*, with_geo_columns: bool) -> tuple[object, object]:
    """Create a temp SQLite DB with the columns the checkout handler touches."""
    engine = create_engine(f"sqlite:///{tempfile.mkstemp(suffix='.db')[1]}")
    geo_cols = ", country TEXT, state TEXT" if with_geo_columns else ""
    with engine.begin() as conn:
        conn.execute(text(
            f"CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, plan TEXT, "
            f"user_type TEXT, credits_remaining INTEGER{geo_cols})"
        ))
        conn.execute(text(
            "CREATE TABLE subscriptions (id TEXT PRIMARY KEY, user_id TEXT, "
            "stripe_customer_id TEXT, stripe_subscription_id TEXT, plan_type TEXT, "
            "status TEXT, report_limit INTEGER, cancel_at_period_end BOOLEAN, "
            "current_period_start TEXT, current_period_end TEXT, created_at TEXT, "
            "pending_plan TEXT, stripe_schedule_id TEXT, past_due_since TEXT, "
            "cancelled_at TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE processed_webhook_events (event_id TEXT PRIMARY KEY, processed_at TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO users (id, email, plan, user_type, credits_remaining) "
            "VALUES ('user-1', 'u@example.com', 'free', 'free', 0)"
        ))
    return engine


def _checkout_session_with_address() -> dict:
    # Real Stripe checkout sessions include the billing address the customer typed.
    return {
        "mode": "subscription",
        "metadata": {"userId": "user-1", "planType": "professional"},
        "subscription": "sub_123",
        "customer": "cus_123",
        "customer_details": {"address": {"country": "US", "state": "CA"}},
    }


def _user_row(engine) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT plan, user_type FROM users WHERE id='user-1'")
        ).first()
    return dict(row._mapping)


class TestCheckoutUpgradeResilientToGeoFailure:
    @pytest.fixture(autouse=True)
    def _reset_metadata(self):
        # The reflected-schema cache is a module global; reset it so each test
        # reflects its own throwaway SQLite schema.
        dbc._shared_metadata = None
        yield
        dbc._shared_metadata = None

    def test_upgrade_succeeds_when_geo_columns_missing(self):
        engine = _make_sqlite_db(with_geo_columns=False)
        db = DatabaseClient(SASession(engine), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))

        # Must not raise — a 500 here makes Stripe retry forever, never upgrading.
        WebhookHandler(db).handle_event(
            "evt_geo_missing", "checkout.session.completed", _checkout_session_with_address()
        )

        assert _user_row(engine) == {"plan": "professional", "user_type": "paid"}

    def test_upgrade_and_geo_both_persist_when_columns_exist(self):
        engine = _make_sqlite_db(with_geo_columns=True)
        db = DatabaseClient(SASession(engine), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))

        WebhookHandler(db).handle_event(
            "evt_geo_ok", "checkout.session.completed", _checkout_session_with_address()
        )

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT plan, user_type, country, state FROM users WHERE id='user-1'")
            ).first()
        assert dict(row._mapping) == {
            "plan": "professional", "user_type": "paid", "country": "US", "state": "CA",
        }
