"""Tests for the 'never charged for a failed report' guarantee:
failed reports are excluded from quota, and credits can be refunded.
"""

from app.modules.subscriptions.service import SubscriptionService


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters: dict = {}
        self.filters_in: tuple | None = None
        self.single_ = False
        self.op = ""
        self.payload = None

    def select(self, *_):
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def in_(self, k, v):
        self.filters_in = (k, list(v))
        return self

    def gte(self, *_):
        return self

    def lte(self, *_):
        return self

    def limit(self, _n):
        return self

    def single(self):
        self.single_ = True
        return self

    def update(self, data):
        self.op = "update"
        self.payload = data
        return self

    def _matches(self, row):
        if not all(row.get(k) == v for k, v in self.filters.items()):
            return False
        if self.filters_in:
            k, vals = self.filters_in
            return row.get(k) in vals
        return True

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "update":
            for row in rows:
                if self._matches(row):
                    row.update(self.payload)
            return _Result(self.payload)
        out = [row for row in rows if self._matches(row)]
        if self.single_:
            return _Result(out[0] if out else None)
        return _Result(out)


class _DB:
    def __init__(self, users=None, reports=None, subscriptions=None):
        self.store = {
            "users": users or [],
            "reports": reports or [],
            "subscriptions": subscriptions or [],
        }

    def table(self, name):
        return _Query(self.store, name)


class TestFailedReportsExcludedFromQuota:
    def test_failed_free_report_does_not_consume_the_free_slot(self):
        db = _DB(
            users=[{"id": "u1", "credits_remaining": 0}],
            reports=[{"user_id": "u1", "report_type": "free", "status": "failed"}],
        )
        can_gen, reason = SubscriptionService(db).can_generate_report("u1")
        assert can_gen is True
        assert "Free report available" in reason

    def test_completed_free_report_does_consume_the_free_slot(self):
        db = _DB(
            users=[{"id": "u1", "credits_remaining": 0}],
            reports=[{"user_id": "u1", "report_type": "free", "status": "completed"}],
        )
        can_gen, _ = SubscriptionService(db).can_generate_report("u1")
        assert can_gen is False

    def test_failed_subscription_report_does_not_consume_quota(self):
        db = _DB(
            users=[{"id": "u1", "credits_remaining": 0}],
            subscriptions=[{
                "user_id": "u1",
                "status": "active",
                "report_limit": 1,
                "current_period_start": "2026-01-01",
                "current_period_end": "2026-02-01",
            }],
            reports=[{"user_id": "u1", "status": "failed", "created_at": "2026-01-10"}],
        )
        can_gen, reason = SubscriptionService(db).can_generate_report("u1")
        assert can_gen is True
        assert "remaining" in reason


class TestCreditRefund:
    def test_refund_increments_credits(self):
        db = _DB(users=[{"id": "u1", "credits_remaining": 2}])
        SubscriptionService(db).refund_report_credit("u1")
        assert db.store["users"][0]["credits_remaining"] == 3

    def test_consume_then_refund_round_trips(self):
        db = _DB(users=[{"id": "u1", "credits_remaining": 2}])
        svc = SubscriptionService(db)
        svc.consume_report_credit("u1")
        assert db.store["users"][0]["credits_remaining"] == 1
        svc.refund_report_credit("u1")
        assert db.store["users"][0]["credits_remaining"] == 2
