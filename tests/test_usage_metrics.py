"""Tests for GET /api/subscriptions/usage.

Coverage goals:
- Requires authentication (no token → 401)
- Free user with no report used yet: reports_used=0, limit=1, remaining=1, can_generate=True
- Free user with their 1 free report used: reports_used=1, limit=1, remaining=0, can_generate=False
- Free user with a pay-per-report credit: can still generate despite used free slot
- Subscribed user mid-period: correct reports_used / reports_remaining counts
- Subscribed user at limit: can_generate=False, correct reason
- Subscribed user at limit but has a credit: can_generate=True (overflow)
- Unlimited plan (report_limit=None): reports_limit=None, reports_remaining=None, can_generate=True
- Period dates forwarded correctly
- Service-level unit tests for get_usage() directly
"""

from app.core.dependencies import get_current_user
from app.modules.auth.schemas import AuthUser
from app.modules.subscriptions.router import get_subscription_service
from app.modules.subscriptions.service import SubscriptionService


def _make_user(plan: str = "free", user_id: str = "user-1") -> AuthUser:
    return AuthUser(
        id=user_id,
        email=f"{plan}@example.com",
        name="Test",
        company="Co",
        role="Producer",
        user_type="paid" if plan != "free" else "free",
        credits_remaining=0,
        plan=plan,
    )


# ── Stub SubscriptionService ──────────────────────────────────────────────────

class StubSubscriptionService:
    """Controllable stub that avoids any real DB or Stripe calls."""

    def __init__(
        self,
        *,
        subscription: dict | None = None,
        free_report_count: int = 0,
        period_report_count: int = 0,
        credits: int = 0,
    ):
        self._subscription = subscription
        self._free_report_count = free_report_count
        self._period_report_count = period_report_count
        self._credits = credits

    def get_active_subscription(self, user_id: str) -> dict | None:
        return self._subscription

    def get_credits_remaining(self, user_id: str) -> int:
        return self._credits

    def can_generate_report(self, user_id: str) -> tuple[bool, str]:
        if not self._subscription:
            if self._credits > 0:
                return (True, f"pay-per-report ({self._credits} credit(s) remaining)")
            if self._free_report_count > 0:
                return (False, "Free report already used. Please upgrade to generate more reports.")
            return (True, "Free report available")

        report_limit = self._subscription.get("report_limit")
        if report_limit in (-1, None):
            return (True, "Unlimited reports")

        if self._period_report_count >= report_limit:
            if self._credits > 0:
                return (True, f"pay-per-report ({self._credits} credit(s) remaining)")
            return (False, f"Report limit reached ({self._period_report_count}/{report_limit}).")

        remaining = report_limit - self._period_report_count
        return (True, f"{remaining} reports remaining this period")

    def get_usage(self, user_id: str, current_plan: str) -> dict:
        # Delegate to the real service logic but using our stub data
        from app.models.enums import normalize_plan
        plan = normalize_plan(current_plan)
        sub = self._subscription
        credits = self._credits

        if not sub:
            limit = 1
            used = self._free_report_count
            remaining = max(0, limit - used)
            can_gen, reason = self.can_generate_report(user_id)
            return {
                "plan": plan,
                "reports_used": used,
                "reports_limit": limit,
                "reports_remaining": remaining,
                "credits_remaining": credits,
                "period_start": None,
                "period_end": None,
                "can_generate": can_gen,
                "reason": reason,
            }

        report_limit = sub.get("report_limit")
        period_start = sub.get("current_period_start")
        period_end = sub.get("current_period_end")

        if report_limit in (-1, None):
            can_gen, reason = self.can_generate_report(user_id)
            return {
                "plan": plan,
                "reports_used": self._period_report_count,
                "reports_limit": None,
                "reports_remaining": None,
                "credits_remaining": credits,
                "period_start": period_start,
                "period_end": period_end,
                "can_generate": can_gen,
                "reason": reason,
            }

        used = self._period_report_count
        remaining = max(0, report_limit - used)
        can_gen, reason = self.can_generate_report(user_id)
        return {
            "plan": plan,
            "reports_used": used,
            "reports_limit": report_limit,
            "reports_remaining": remaining,
            "credits_remaining": credits,
            "period_start": period_start,
            "period_end": period_end,
            "can_generate": can_gen,
            "reason": reason,
        }


# ── Auth guard ────────────────────────────────────────────────────────────────

def test_usage_requires_authentication(client):
    """No auth header → 403 (JWT dependency rejects unauthenticated requests)."""
    response = client.get("/api/subscriptions/usage")
    assert response.status_code in (401, 403)


# ── Free user scenarios ───────────────────────────────────────────────────────

def test_usage_free_user_has_not_used_report_yet(client):
    """Fresh free user: 1 report remaining, can generate."""
    user = _make_user("free")
    service = StubSubscriptionService(free_report_count=0, credits=0)

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: service

    resp = client.get("/api/subscriptions/usage", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan"] == "free"
    assert data["reports_used"] == 0
    assert data["reports_limit"] == 1
    assert data["reports_remaining"] == 1
    assert data["can_generate"] is True
    assert data["credits_remaining"] == 0


def test_usage_free_user_exhausted_free_report(client):
    """Free user who already generated their 1 report: cannot generate more."""
    user = _make_user("free")
    service = StubSubscriptionService(free_report_count=1, credits=0)

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: service

    resp = client.get("/api/subscriptions/usage", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reports_used"] == 1
    assert data["reports_limit"] == 1
    assert data["reports_remaining"] == 0
    assert data["can_generate"] is False


def test_usage_free_user_with_credit_can_still_generate(client):
    """Free user who used their free report but bought a credit: can_generate=True."""
    user = _make_user("free")
    service = StubSubscriptionService(free_report_count=1, credits=1)

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: service

    resp = client.get("/api/subscriptions/usage", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_generate"] is True
    assert data["credits_remaining"] == 1


# ── Subscribed user scenarios ─────────────────────────────────────────────────

_PRODUCER_SUB = {
    "id": "sub-1",
    "user_id": "user-1",
    "status": "active",
    "plan_type": "producer",
    "report_limit": 3,
    "current_period_start": "2026-04-01T00:00:00",
    "current_period_end": "2026-04-30T23:59:59",
    "stripe_customer_id": "cus_xxx",
    "stripe_subscription_id": "sub_xxx",
    "cancel_at_period_end": False,
    "pending_plan": None,
    "past_due_since": None,
}


def test_usage_subscribed_user_mid_period(client):
    """Producer subscriber who used 1 of 3 reports: 2 remaining."""
    user = _make_user("producer")
    service = StubSubscriptionService(
        subscription=_PRODUCER_SUB,
        period_report_count=1,
        credits=0,
    )

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: service

    resp = client.get("/api/subscriptions/usage", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan"] == "producer"
    assert data["reports_used"] == 1
    assert data["reports_limit"] == 3
    assert data["reports_remaining"] == 2
    assert data["can_generate"] is True
    assert data["period_start"] == "2026-04-01T00:00:00"
    assert data["period_end"] == "2026-04-30T23:59:59"


def test_usage_subscribed_user_at_limit(client):
    """Producer subscriber who used all 3 reports and has no credits."""
    user = _make_user("producer")
    service = StubSubscriptionService(
        subscription=_PRODUCER_SUB,
        period_report_count=3,
        credits=0,
    )

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: service

    resp = client.get("/api/subscriptions/usage", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reports_used"] == 3
    assert data["reports_remaining"] == 0
    assert data["can_generate"] is False
    assert "limit" in data["reason"].lower() or "reached" in data["reason"].lower()


def test_usage_subscribed_user_at_limit_with_credit_overflow(client):
    """Producer at monthly limit but has 1 credit: can still generate via overflow."""
    user = _make_user("producer")
    service = StubSubscriptionService(
        subscription=_PRODUCER_SUB,
        period_report_count=3,
        credits=1,
    )

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: service

    resp = client.get("/api/subscriptions/usage", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_generate"] is True
    assert data["credits_remaining"] == 1
    assert "pay-per-report" in data["reason"].lower()


def test_usage_unlimited_plan_returns_null_limits(client):
    """Studio plan with no report limit: reports_limit and reports_remaining are null."""
    user = _make_user("studio")
    studio_sub = {**_PRODUCER_SUB, "plan_type": "studio", "report_limit": None}
    service = StubSubscriptionService(subscription=studio_sub, period_report_count=5, credits=0)

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: service

    resp = client.get("/api/subscriptions/usage", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reports_limit"] is None
    assert data["reports_remaining"] is None
    assert data["can_generate"] is True


def test_usage_response_schema_always_includes_required_fields(client):
    """Every response must include the 9 required fields."""
    REQUIRED_FIELDS = {
        "plan", "reports_used", "reports_limit", "reports_remaining",
        "credits_remaining", "period_start", "period_end", "can_generate", "reason",
    }
    user = _make_user("free")
    service = StubSubscriptionService()

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_subscription_service] = lambda: service

    resp = client.get("/api/subscriptions/usage", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    missing = REQUIRED_FIELDS - set(resp.json().keys())
    assert not missing, f"Missing fields in response: {missing}"


# ── Service-level unit tests ──────────────────────────────────────────────────

class _InMemoryDB:
    """Minimal supabase-like DB stub for service-level tests."""

    def __init__(self, *, subscriptions=None, reports=None, users=None):
        self._subscriptions: list[dict] = subscriptions or []
        self._reports: list[dict] = reports or []
        self._users: list[dict] = users or []
        self._query_state: dict = {}

    def table(self, name: str) -> "_InMemoryDB":
        self._query_state = {"table": name, "filters": [], "limit": None}
        return self

    def select(self, *_) -> "_InMemoryDB":
        return self

    def eq(self, field: str, value) -> "_InMemoryDB":
        self._query_state.setdefault("filters", []).append(("eq", field, value))
        return self

    def in_(self, field: str, values: list) -> "_InMemoryDB":
        self._query_state.setdefault("filters", []).append(("in", field, values))
        return self

    def gte(self, *_) -> "_InMemoryDB":
        return self

    def lte(self, *_) -> "_InMemoryDB":
        return self

    def limit(self, n: int) -> "_InMemoryDB":
        self._query_state["limit"] = n
        return self

    def single(self) -> "_InMemoryDB":
        self._query_state["single"] = True
        return self

    def execute(self):
        table = self._query_state.get("table", "")
        dataset = {
            "subscriptions": self._subscriptions,
            "reports": self._reports,
            "users": self._users,
        }.get(table, [])

        result = []
        for row in dataset:
            match = True
            for f_type, field, value in self._query_state.get("filters", []):
                if f_type == "eq" and row.get(field) != value:
                    match = False
                    break
                if f_type == "in" and row.get(field) not in value:
                    match = False
                    break
            if match:
                result.append(row)

        limit = self._query_state.get("limit")
        if limit:
            result = result[:limit]

        # .single() means the real supabase returns data as a dict, not a list
        if self._query_state.get("single"):
            data = result[0] if result else None
        else:
            data = result

        return type("Result", (), {"data": data})()


def test_service_get_usage_free_no_reports():
    db = _InMemoryDB(
        users=[{"id": "u1", "credits_remaining": 0}],
        reports=[],
    )
    svc = SubscriptionService(db)
    usage = svc.get_usage("u1", "free")
    assert usage["reports_used"] == 0
    assert usage["reports_limit"] == 1
    assert usage["reports_remaining"] == 1
    assert usage["can_generate"] is True


def test_service_get_usage_free_one_free_report_used():
    db = _InMemoryDB(
        users=[{"id": "u1", "credits_remaining": 0}],
        reports=[{"id": "r1", "user_id": "u1", "report_type": "free", "created_at": "2026-04-01"}],
    )
    svc = SubscriptionService(db)
    usage = svc.get_usage("u1", "free")
    assert usage["reports_used"] == 1
    assert usage["reports_remaining"] == 0
    assert usage["can_generate"] is False


def test_service_get_usage_subscribed_mid_period():
    subscription = {
        "id": "sub-1", "user_id": "u1", "status": "active",
        "plan_type": "producer", "report_limit": 3,
        "current_period_start": "2026-04-01", "current_period_end": "2026-04-30",
        "stripe_customer_id": "cus_x", "stripe_subscription_id": "sub_x",
        "cancel_at_period_end": False, "pending_plan": None, "past_due_since": None,
    }
    db = _InMemoryDB(
        subscriptions=[subscription],
        users=[{"id": "u1", "credits_remaining": 0}],
        reports=[
            {"id": "r1", "user_id": "u1", "report_type": "paid", "created_at": "2026-04-10"},
        ],
    )
    svc = SubscriptionService(db)
    usage = svc.get_usage("u1", "producer")
    assert usage["plan"] == "producer"
    assert usage["reports_limit"] == 3
    assert usage["can_generate"] is True


def test_service_get_usage_credits_shown_for_free_user():
    db = _InMemoryDB(
        users=[{"id": "u1", "credits_remaining": 2}],
        reports=[{"id": "r1", "user_id": "u1", "report_type": "free", "created_at": "2026-04-01"}],
    )
    svc = SubscriptionService(db)
    usage = svc.get_usage("u1", "free")
    assert usage["credits_remaining"] == 2
    # Even though free report used, credits allow generation
    assert usage["can_generate"] is True
