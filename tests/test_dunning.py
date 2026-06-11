"""Tests for the dunning grace task."""

from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.modules.payments.dunning import GRACE_PERIOD_DAYS, run_dunning_grace_check


class _FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class _DunningQuery:
    """Fake query supporting eq/lte for subscriptions reads, and update."""

    def __init__(self, store: dict, table_name: str, fixture: dict):
        self._store = store
        self._table = table_name
        self._fixture = fixture
        self._filters: dict = {}
        self._lte: dict = {}
        self._op = ""
        self._pending: dict | None = None

    def select(self, _cols):
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def lte(self, key, value):
        self._lte[key] = value
        return self

    def limit(self, _n):
        return self

    def update(self, data):
        self._op = "update"
        self._pending = data
        return self

    def insert(self, data):
        self._op = "insert"
        self._pending = data
        return self

    def execute(self):
        if self._op == "update":
            self._store.setdefault(f"{self._table}:update", []).append(
                {"data": self._pending, "filters": dict(self._filters)}
            )
            return _FakeResult([self._pending])

        # Read path — return fixture rows for this table that match filters.
        if self._table == "subscriptions":
            past_due_rows = self._fixture.get("past_due_subscriptions", [])
            cutoff_iso = self._lte.get("past_due_since")
            if cutoff_iso:
                past_due_rows = [
                    r for r in past_due_rows
                    if r.get("past_due_since") and r["past_due_since"] < cutoff_iso
                ]
            return _FakeResult(past_due_rows)

        if self._table == "users":
            user_id = self._filters.get("id")
            users = self._fixture.get("users", {})
            user = users.get(user_id)
            return _FakeResult([user] if user else [])

        return _FakeResult([])


class FakeDunningSupabase:
    def __init__(self, fixture: dict):
        self.fixture = fixture
        self.writes: dict[str, list] = {}

    def table(self, name):
        return _DunningQuery(self.writes, name, self.fixture)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        JWT_SECRET_KEY="x" * 64,
        STRIPE_SECRET_KEY="sk_test_dummy",
        BREVO_API_KEY="",  # email send is skipped — we only assert DB writes
        REDIS_URL="redis://localhost:6379/0",
    )


class TestDunningGraceCheck:
    def test_downgrades_subscription_past_grace_window(self):
        nine_days_ago = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        db = FakeDunningSupabase({
            "past_due_subscriptions": [
                {
                    "id": "sub-row-1",
                    "user_id": "user-1",
                    "stripe_subscription_id": "sub_stripe_1",
                    "past_due_since": nine_days_ago,
                },
            ],
            "users": {"user-1": {"email": "u@example.com"}},
        })
        count = run_dunning_grace_check(db, _settings())
        assert count == 1

        sub_updates = db.writes.get("subscriptions:update", [])
        assert any(u["data"].get("status") == "cancelled" for u in sub_updates)

        user_updates = db.writes.get("users:update", [])
        assert any(u["data"].get("plan") == "free" for u in user_updates)
        assert any(u["data"].get("user_type") == "free" for u in user_updates)

    def test_keeps_subscription_within_grace_window(self):
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        db = FakeDunningSupabase({
            "past_due_subscriptions": [
                {
                    "id": "sub-row-1",
                    "user_id": "user-1",
                    "stripe_subscription_id": "sub_stripe_1",
                    "past_due_since": three_days_ago,
                },
            ],
            "users": {"user-1": {"email": "u@example.com"}},
        })
        count = run_dunning_grace_check(db, _settings())
        assert count == 0
        assert "subscriptions:update" not in db.writes
        assert "users:update" not in db.writes

    def test_grace_period_is_seven_days(self):
        assert GRACE_PERIOD_DAYS == 7

    def test_no_past_due_subscriptions_does_nothing(self):
        db = FakeDunningSupabase({"past_due_subscriptions": [], "users": {}})
        assert run_dunning_grace_check(db, _settings()) == 0
        assert db.writes == {}
