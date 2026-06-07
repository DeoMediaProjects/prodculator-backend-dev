import pytest

from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser
from tests.admin_fakes import FakeSupabase

HEADERS = {"Authorization": "Bearer token"}


class _FakeEmailService:
    def __init__(self, *_args, **_kwargs):
        pass

    def send(self, *_args, **_kwargs):
        return None


def _current_admin() -> AdminUser:
    return AdminUser(id="a1", email="boss@example.com", name="Boss", role="master_admin")


def _seed() -> FakeSupabase:
    return FakeSupabase(
        {
            "admins": [
                {
                    "id": "a1",
                    "email": "boss@example.com",
                    "name": "Boss",
                    "role": "master_admin",
                    "password_hash": "hashed-1",
                    "created_at": "2026-01-01T00:00:00Z",
                    "last_login": None,
                },
                {
                    "id": "a2",
                    "email": "helper@example.com",
                    "name": "Helper",
                    "role": "support_admin",
                    "password_hash": "hashed-2",
                    "created_at": "2026-02-01T00:00:00Z",
                    "last_login": None,
                },
            ]
        }
    )


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    monkeypatch.setattr(
        "app.modules.admin.admin_users_router.EmailService", _FakeEmailService
    )


def _setup(client) -> FakeSupabase:
    fake = _seed()
    client.app.dependency_overrides[get_current_admin] = _current_admin
    client.app.dependency_overrides[get_supabase] = lambda: fake
    return fake


def test_list_admin_users_hides_password_hash(client):
    _setup(client)
    response = client.get("/api/admin/admin-users", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert "password_hash" not in item


def test_create_admin_user(client):
    fake = _setup(client)
    response = client.post(
        "/api/admin/admin-users",
        headers=HEADERS,
        json={"email": "new@example.com", "name": "New", "role": "data_admin"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["admin"]["email"] == "new@example.com"
    assert body["admin"]["role"] == "data_admin"
    assert "password_hash" not in body["admin"]
    assert len(body["temporary_password"]) > 0
    assert len(fake.store["admins"]) == 3


def test_create_admin_invalid_role_returns_422(client):
    _setup(client)
    response = client.post(
        "/api/admin/admin-users",
        headers=HEADERS,
        json={"email": "x@example.com", "name": "X", "role": "wizard"},
    )
    assert response.status_code == 422


def test_update_admin_user(client):
    fake = _setup(client)
    response = client.put(
        "/api/admin/admin-users/a2",
        headers=HEADERS,
        json={"name": "Helper Renamed"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Helper Renamed"
    row = next(r for r in fake.store["admins"] if r["id"] == "a2")
    assert row["name"] == "Helper Renamed"


def test_update_own_role_rejected(client):
    _setup(client)
    response = client.put(
        "/api/admin/admin-users/a1",
        headers=HEADERS,
        json={"role": "data_admin"},
    )
    assert response.status_code == 400


def test_update_unknown_admin_returns_404(client):
    _setup(client)
    response = client.put(
        "/api/admin/admin-users/nope",
        headers=HEADERS,
        json={"name": "Ghost"},
    )
    assert response.status_code == 404


def test_delete_admin_user(client):
    fake = _setup(client)
    response = client.delete("/api/admin/admin-users/a2", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert {r["id"] for r in fake.store["admins"]} == {"a1"}


def test_delete_own_account_rejected(client):
    _setup(client)
    response = client.delete("/api/admin/admin-users/a1", headers=HEADERS)
    assert response.status_code == 400


def test_delete_unknown_admin_returns_404(client):
    _setup(client)
    response = client.delete("/api/admin/admin-users/nope", headers=HEADERS)
    assert response.status_code == 404


def test_admin_users_require_permission(client):
    def insufficient_role() -> AdminUser:
        return AdminUser(id="a2", email="helper@example.com", name="Helper", role="support_admin")

    client.app.dependency_overrides[get_current_admin] = insufficient_role
    client.app.dependency_overrides[get_supabase] = lambda: _seed()
    response = client.get("/api/admin/admin-users", headers=HEADERS)
    assert response.status_code == 403
