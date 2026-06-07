import time

from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser
from app.modules.data_sources.rate_limit import _last_test
from tests.admin_fakes import FakeSupabase

HEADERS = {"Authorization": "Bearer token"}


def _admin_user() -> AdminUser:
    return AdminUser(id="admin-1", email="admin@example.com", name="Admin", role="master_admin")


def _source(**overrides):
    base = {
        "id": "ds1",
        "name": "TMDB",
        "slug": "tmdb",
        "category": "enrichment",
        "description": "Movie metadata",
        "endpoint": "https://api.themoviedb.org/3",
        "enabled": True,
        "status": "unknown",
        "credential_mode": "platform",
        "is_implemented": True,
        "last_tested_at": None,
        "last_test_result": None,
        "last_test_message": None,
        "sync_schedule": "weekly",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _seed() -> FakeSupabase:
    return FakeSupabase(
        {
            "data_sources": [
                _source(),
                _source(
                    id="ds2",
                    name="Google Maps",
                    slug="google_maps",
                    category="geo",
                    description=None,
                    endpoint=None,
                    enabled=False,
                    is_implemented=False,
                    sync_schedule=None,
                    updated_at=None,
                ),
            ]
        }
    )


def _setup(client) -> FakeSupabase:
    fake = _seed()
    client.app.dependency_overrides[get_current_admin] = _admin_user
    client.app.dependency_overrides[get_supabase] = lambda: fake
    return fake


def test_list_data_sources(client):
    _setup(client)
    response = client.get("/api/admin/data-sources", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert "credential_configured" in data["items"][0]


def test_get_data_source(client):
    _setup(client)
    response = client.get("/api/admin/data-sources/ds1", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["name"] == "TMDB"


def test_get_unknown_data_source_returns_404(client):
    _setup(client)
    response = client.get("/api/admin/data-sources/nope", headers=HEADERS)
    assert response.status_code == 404


def test_update_data_source(client):
    fake = _setup(client)
    response = client.patch(
        "/api/admin/data-sources/ds1",
        headers=HEADERS,
        json={"enabled": False, "sync_schedule": "monthly"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["sync_schedule"] == "monthly"
    row = next(r for r in fake.store["data_sources"] if r["id"] == "ds1")
    assert row["enabled"] is False


def test_update_data_source_empty_body_returns_400(client):
    _setup(client)
    response = client.patch("/api/admin/data-sources/ds1", headers=HEADERS, json={})
    assert response.status_code == 400


def test_bulk_configure(client):
    fake = _setup(client)
    response = client.put(
        "/api/admin/data-sources/configuration",
        headers=HEADERS,
        json={"sources": [{"id": "ds1", "enabled": False}, {"id": "ds2", "enabled": True}]},
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 2
    rows = {r["id"]: r for r in fake.store["data_sources"]}
    assert rows["ds1"]["enabled"] is False
    assert rows["ds2"]["enabled"] is True


def test_sync_schedule(client):
    _setup(client)
    response = client.get("/api/admin/data-sources/sync-schedule", headers=HEADERS)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    assert {i["slug"] for i in items} == {"tmdb", "google_maps"}


def test_test_connection_not_implemented(client):
    _setup(client)
    _last_test.clear()
    response = client.post("/api/admin/data-sources/ds2/test", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "disconnected"
    assert "not yet implemented" in body["message"]


def test_test_connection_unknown_source_returns_404(client):
    _setup(client)
    _last_test.clear()
    response = client.post("/api/admin/data-sources/nope/test", headers=HEADERS)
    assert response.status_code == 404


def test_test_connection_rate_limited(client):
    _setup(client)
    _last_test.clear()
    _last_test["tmdb"] = time.time()
    response = client.post("/api/admin/data-sources/ds1/test", headers=HEADERS)
    assert response.status_code == 429


def test_data_sources_require_permission(client):
    def insufficient_role() -> AdminUser:
        return AdminUser(id="a2", email="data@example.com", name="Data", role="data_admin")

    client.app.dependency_overrides[get_current_admin] = insufficient_role
    client.app.dependency_overrides[get_supabase] = lambda: _seed()
    response = client.get("/api/admin/data-sources", headers=HEADERS)
    assert response.status_code == 403
