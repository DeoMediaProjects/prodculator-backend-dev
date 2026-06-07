from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser
from tests.admin_fakes import FakeSupabase

HEADERS = {"Authorization": "Bearer token"}


def _admin_user() -> AdminUser:
    return AdminUser(id="admin-1", email="admin@example.com", name="Admin", role="master_admin")


def _seed() -> FakeSupabase:
    return FakeSupabase(
        {
            "email_gating_records": [
                {
                    "id": "e1",
                    "email": "alice@example.com",
                    "report_generated": True,
                    "blocked": False,
                    "created_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "e2",
                    "email": "bob@test.com",
                    "report_generated": False,
                    "blocked": False,
                    "created_at": "2026-02-01T00:00:00Z",
                },
            ]
        }
    )


def _setup(client) -> FakeSupabase:
    fake = _seed()
    client.app.dependency_overrides[get_current_admin] = _admin_user
    client.app.dependency_overrides[get_supabase] = lambda: fake
    return fake


def test_list_email_gating_records(client):
    _setup(client)
    response = client.get("/api/admin/email-gating", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert {item["email"] for item in data["items"]} == {
        "alice@example.com",
        "bob@test.com",
    }
    first = data["items"][0]
    assert set(first.keys()) >= {"id", "email", "date", "report_generated", "blocked"}


def test_list_email_gating_search(client):
    _setup(client)
    response = client.get("/api/admin/email-gating?search=test.com", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["email"] == "bob@test.com"


def test_block_email_record(client):
    fake = _setup(client)
    response = client.post("/api/admin/email-gating/e1/block", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["blocked"] is True
    row = next(r for r in fake.store["email_gating_records"] if r["id"] == "e1")
    assert row["blocked"] is True


def test_unblock_email_record(client):
    fake = _setup(client)
    fake.store["email_gating_records"][0]["blocked"] = True
    response = client.post("/api/admin/email-gating/e1/unblock", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["blocked"] is False


def test_block_unknown_record_returns_404(client):
    _setup(client)
    response = client.post("/api/admin/email-gating/nope/block", headers=HEADERS)
    assert response.status_code == 404


def test_email_gating_requires_permission(client):
    def insufficient_role() -> AdminUser:
        return AdminUser(id="a2", email="data@example.com", name="Data", role="data_admin")

    client.app.dependency_overrides[get_current_admin] = insufficient_role
    client.app.dependency_overrides[get_supabase] = lambda: _seed()
    response = client.get("/api/admin/email-gating", headers=HEADERS)
    assert response.status_code == 403
