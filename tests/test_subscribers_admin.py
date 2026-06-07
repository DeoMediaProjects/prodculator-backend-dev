from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser
from tests.admin_fakes import FakeSupabase

HEADERS = {"Authorization": "Bearer token"}
NOW = datetime.now(timezone.utc).isoformat()


def _admin_user() -> AdminUser:
    return AdminUser(id="admin-1", email="admin@example.com", name="Admin")


def _seed() -> FakeSupabase:
    return FakeSupabase(
        {
            "users": [
                {
                    "id": "u1",
                    "name": "Alice",
                    "email": "alice@example.com",
                    "company": "AlphaCo",
                    "user_type": "paid",
                    "credits_remaining": 1,
                    "created_at": "2026-01-10T00:00:00Z",
                },
                {
                    "id": "u2",
                    "name": "Bob",
                    "email": "bob@example.com",
                    "company": None,
                    "user_type": "free",
                    "credits_remaining": 0,
                    "created_at": "2026-02-01T00:00:00Z",
                },
                {
                    "id": "u3",
                    "name": "Carol",
                    "email": "carol@studio.com",
                    "company": "Studio",
                    "user_type": "b2b",
                    "credits_remaining": 5,
                    "created_at": "2026-03-01T00:00:00Z",
                },
            ],
            "subscriptions": [
                {
                    "id": "s1",
                    "user_id": "u1",
                    "status": "active",
                    "plan_type": "single",
                    "amount_cents": 4900,
                    "currency": "gbp",
                    "report_limit": 100,
                    "created_at": "2026-01-10T00:00:00Z",
                },
                {
                    "id": "s2",
                    "user_id": "u3",
                    "status": "active",
                    "plan_type": "studio",
                    "amount_cents": 24900,
                    "currency": "usd",
                    "report_limit": None,
                    "created_at": "2026-03-01T00:00:00Z",
                },
                {
                    "id": "s3",
                    "user_id": "u2",
                    "status": "past_due",
                    "plan_type": "single",
                    "amount_cents": 4900,
                    "currency": "usd",
                    "report_limit": 100,
                    "created_at": "2026-02-01T00:00:00Z",
                },
            ],
            "reports": [
                {"id": "r1", "user_id": "u1", "report_type": "full", "created_at": NOW},
                {"id": "r2", "user_id": "u1", "report_type": "preview", "created_at": NOW},
                {"id": "r3", "user_id": "u3", "report_type": "full", "created_at": NOW},
            ],
        }
    )


def _setup(client) -> FakeSupabase:
    fake = _seed()
    client.app.dependency_overrides[get_current_admin] = _admin_user
    client.app.dependency_overrides[get_supabase] = lambda: fake
    return fake


def test_subscriber_metrics(client):
    _setup(client)
    response = client.get("/api/admin/subscribers/metrics", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total_paid_users"] == 2  # paid + b2b
    assert data["mrr_gbp"] == 49.0
    assert data["mrr_usd"] == 249.0
    assert data["reports_this_month_total"] == 3
    assert data["reports_this_month_free"] == 1
    assert data["reports_this_month_paid"] == 2
    assert data["avg_reports_per_user"] == 1.0
    free_row = next(p for p in data["plan_distribution"] if p["plan"] == "Free")
    assert free_row["user_count"] == 1
    studio_row = next(p for p in data["plan_distribution"] if p["plan"] == "Studio")
    assert studio_row["user_count"] == 1
    assert studio_row["revenue"] == 249.0


def test_list_subscribers_returns_all_with_counts(client):
    _setup(client)
    response = client.get("/api/admin/subscribers", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["counts"] == {"active": 2, "past_due": 1, "canceled": 0}

    by_id = {item["id"]: item for item in data["items"]}
    assert by_id["u1"]["plan"] == "Pro Monthly"
    assert by_id["u1"]["status"] == "Active"
    assert by_id["u1"]["monthly_spend"] == 49.0
    assert by_id["u1"]["payment_currency"] == "GBP"
    assert by_id["u1"]["reports_this_month"] == 2
    assert by_id["u1"]["total_reports_generated"] == 2
    assert by_id["u2"]["status"] == "Past Due"
    assert by_id["u3"]["plan"] == "Studio"
    assert by_id["u3"]["monthly_spend"] == 249.0


def test_list_subscribers_status_filter(client):
    _setup(client)
    response = client.get("/api/admin/subscribers?status=active", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert {item["id"] for item in data["items"]} == {"u1", "u3"}


def test_list_subscribers_search(client):
    _setup(client)
    response = client.get("/api/admin/subscribers?search=alice", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == "u1"


def test_block_and_unblock_subscriber(client):
    fake = _setup(client)
    block = client.post("/api/admin/subscribers/u1/block", headers=HEADERS)
    assert block.status_code == 200
    user = next(u for u in fake.store["users"] if u["id"] == "u1")
    assert user["is_blocked"] is True

    unblock = client.post("/api/admin/subscribers/u1/unblock", headers=HEADERS)
    assert unblock.status_code == 200
    user = next(u for u in fake.store["users"] if u["id"] == "u1")
    assert user["is_blocked"] is False


def test_credit_subscriber_adds_credits(client):
    fake = _setup(client)
    response = client.post(
        "/api/admin/subscribers/u3/credit",
        headers=HEADERS,
        json={"adjustment": 5, "reason": "goodwill"},
    )
    assert response.status_code == 200
    assert response.json()["credits_remaining"] == 10
    user = next(u for u in fake.store["users"] if u["id"] == "u3")
    assert user["credits_remaining"] == 10


def test_credit_subscriber_floors_at_zero(client):
    _setup(client)
    response = client.post(
        "/api/admin/subscribers/u1/credit",
        headers=HEADERS,
        json={"adjustment": -100},
    )
    assert response.status_code == 200
    assert response.json()["credits_remaining"] == 0


def test_credit_subscriber_with_null_balance_treated_as_zero(client):
    fake = _setup(client)
    # A legacy row can carry a NULL credits_remaining; the webhook path guards
    # for this (``or 0``) so the admin credit path must too.
    next(u for u in fake.store["users"] if u["id"] == "u1")["credits_remaining"] = None
    response = client.post(
        "/api/admin/subscribers/u1/credit",
        headers=HEADERS,
        json={"adjustment": 5},
    )
    assert response.status_code == 200
    assert response.json()["credits_remaining"] == 5


def test_credit_unknown_subscriber_returns_404(client):
    _setup(client)
    response = client.post(
        "/api/admin/subscribers/nope/credit",
        headers=HEADERS,
        json={"adjustment": 1},
    )
    assert response.status_code == 404


def test_subscribers_require_admin(client):
    def deny():
        raise HTTPException(status_code=403, detail="Admin access required")

    client.app.dependency_overrides[get_current_admin] = deny
    response = client.get("/api/admin/subscribers/metrics", headers=HEADERS)
    assert response.status_code == 403
