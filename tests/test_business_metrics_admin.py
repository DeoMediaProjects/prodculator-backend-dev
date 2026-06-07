from datetime import datetime, timedelta, timezone

from app.core.dependencies import get_current_admin
from app.modules.admin.business_metrics_service import BusinessMetricsDashboardService
from app.modules.admin.router import get_business_metrics_dashboard_service
from app.modules.admin.schemas import AdminUser
from tests.admin_fakes import FakeSupabase

HEADERS = {"Authorization": "Bearer token"}

RECENT_CANCEL = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

# Stub FX so the test never hits the network: GBP→USD at 1.27, everything else 1:1.
def _fx_stub(amount: float, currency: str) -> float:
    return amount * 1.27 if currency == "GBP" else amount


def _admin_user() -> AdminUser:
    return AdminUser(id="admin-1", email="admin@example.com", name="Admin", role="master_admin")


def _seed() -> FakeSupabase:
    return FakeSupabase(
        {
            "users": [
                {"id": "u1", "user_type": "paid", "role": "Producer", "plan": "professional",
                 "country": "US", "state": "CA", "created_at": "2026-01-01T00:00:00Z"},
                {"id": "u2", "user_type": "paid", "role": "Director", "plan": "studio",
                 "country": "US", "state": "NY", "created_at": "2026-01-10T00:00:00Z"},
                {"id": "u3", "user_type": "b2b", "role": "Producer", "plan": "studio",
                 "country": "GB", "state": None, "created_at": "2026-02-01T00:00:00Z"},
                {"id": "u4", "user_type": "free", "role": None, "plan": "free",
                 "country": None, "state": None, "created_at": "2026-03-01T00:00:00Z"},
                {"id": "u5", "user_type": "paid", "role": "Producer", "plan": "professional",
                 "country": None, "state": None, "created_at": "2026-03-05T00:00:00Z"},
            ],
            "subscriptions": [
                {"user_id": "u1", "status": "active", "amount_cents": 4900, "currency": "usd",
                 "plan_type": "professional", "created_at": "2026-01-05T00:00:00Z", "cancelled_at": None},
                {"user_id": "u2", "status": "active", "amount_cents": 24900, "currency": "usd",
                 "plan_type": "studio", "created_at": "2026-01-12T00:00:00Z", "cancelled_at": None},
                {"user_id": "u3", "status": "active", "amount_cents": 20000, "currency": "gbp",
                 "plan_type": "studio", "created_at": "2026-02-03T00:00:00Z", "cancelled_at": None},
                {"user_id": "u5", "status": "active", "amount_cents": 4900, "currency": "usd",
                 "plan_type": "professional", "created_at": "2026-03-09T00:00:00Z", "cancelled_at": None},
                {"user_id": "u6", "status": "cancelled", "amount_cents": 4900, "currency": "usd",
                 "plan_type": "professional", "created_at": "2026-01-01T00:00:00Z", "cancelled_at": RECENT_CANCEL},
            ],
            "reports": [
                {"user_id": "u1"}, {"user_id": "u1"}, {"user_id": "u2"},
            ],
        }
    )


def _setup(client, fake: FakeSupabase | None = None):
    fake = fake or _seed()
    client.app.dependency_overrides[get_current_admin] = _admin_user
    client.app.dependency_overrides[get_business_metrics_dashboard_service] = (
        lambda: BusinessMetricsDashboardService(fake, None, fx_converter=_fx_stub)
    )
    return fake


def test_business_metrics_core_kpis(client):
    _setup(client)
    response = client.get("/api/admin/business-metrics", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()

    assert data["total_users"] == 5
    assert data["total_paid_users"] == 4
    assert data["active_subscriptions"] == 4
    # USD 49+249+49 = 347; GBP 200 -> 254 USD; total 601, ARR x12
    assert data["mrr_usd"] == 601.0
    assert data["arr_usd"] == 7212.0
    assert data["free_to_paid_percent"] == 80.0
    assert data["activation_rate_percent"] == 40.0
    assert data["avg_days_to_convert"] == 3.0
    # 1 cancellation in last 30d vs 4 active -> 1/5
    assert data["monthly_churn_percent"] == 20.0


def test_business_metrics_mrr_by_currency(client):
    _setup(client)
    data = client.get("/api/admin/business-metrics", headers=HEADERS).json()
    by_cur = {c["currency"]: c["amount"] for c in data["mrr_by_currency"]}
    assert by_cur == {"USD": 347.0, "GBP": 200.0}


def test_business_metrics_distributions(client):
    _setup(client)
    data = client.get("/api/admin/business-metrics", headers=HEADERS).json()
    roles = {r["role"]: r["count"] for r in data["role_distribution"]}
    assert roles == {"Producer": 3, "Director": 1, "Unspecified": 1}
    plans = {p["plan"]: p["count"] for p in data["plan_distribution"]}
    assert plans == {"Professional": 2, "Studio": 2, "Free": 1}


def test_business_metrics_geographic(client):
    _setup(client)
    data = client.get("/api/admin/business-metrics", headers=HEADERS).json()
    assert data["geo_available"] is True

    geo = {g["country"]: g for g in data["geographic"]}
    assert geo["United States"]["users"] == 2
    assert geo["United States"]["revenue_usd"] == 298.0  # 49 + 249
    assert geo["United States"]["percentage"] == 50.0
    assert geo["United Kingdom"]["users"] == 1
    assert geo["United Kingdom"]["revenue_usd"] == 254.0  # 200 GBP -> USD
    assert geo["Unknown"]["users"] == 1  # paid user u5 has no country yet

    states = {s["state"]: s for s in data["us_states"]}
    assert states["California"]["users"] == 1
    assert states["California"]["revenue_usd"] == 49.0
    assert states["New York"]["revenue_usd"] == 249.0


def test_business_metrics_geo_unavailable_when_no_country(client):
    fake = FakeSupabase(
        {
            "users": [
                {"id": "u1", "user_type": "paid", "role": "Producer", "plan": "professional",
                 "country": None, "state": None, "created_at": "2026-01-01T00:00:00Z"},
            ],
            "subscriptions": [
                {"user_id": "u1", "status": "active", "amount_cents": 4900, "currency": "usd",
                 "plan_type": "professional", "created_at": "2026-01-05T00:00:00Z", "cancelled_at": None},
            ],
            "reports": [],
        }
    )
    _setup(client, fake)
    data = client.get("/api/admin/business-metrics", headers=HEADERS).json()
    assert data["geo_available"] is False
    assert data["geographic"] == []
    assert data["us_states"] == []


def test_business_metrics_empty_platform(client):
    _setup(client, FakeSupabase({"users": [], "subscriptions": [], "reports": []}))
    data = client.get("/api/admin/business-metrics", headers=HEADERS).json()
    assert data["total_users"] == 0
    assert data["mrr_usd"] == 0.0
    assert data["free_to_paid_percent"] == 0.0
    assert data["avg_days_to_convert"] is None
    assert data["geo_available"] is False


def test_business_metrics_requires_permission(client):
    def insufficient_role() -> AdminUser:
        return AdminUser(id="a2", email="data@example.com", name="Data", role="data_admin")

    client.app.dependency_overrides[get_current_admin] = insufficient_role
    client.app.dependency_overrides[get_business_metrics_dashboard_service] = (
        lambda: BusinessMetricsDashboardService(_seed(), None, fx_converter=_fx_stub)
    )
    response = client.get("/api/admin/business-metrics", headers=HEADERS)
    assert response.status_code == 403
