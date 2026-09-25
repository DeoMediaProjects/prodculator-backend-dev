import pytest

from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser
from tests.admin_fakes import FakeSupabase

HEADERS = {"Authorization": "Bearer token"}
BASE = "/api/admin/distributors"


def _admin(role: str):
    return lambda: AdminUser(id="admin-1", email="admin@example.com", name="Admin", role=role)


def _distributor(**overrides):
    base = {
        "id": "d1",
        "name": "Arrow Films",
        "primary_market": "United Kingdom",
        "specialty_genres": ["Horror"],
        "source_url": "https://arrow.example.com",
        "active_status": "active",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _change(**overrides):
    base = {
        "id": "pc1",
        "resource_type": "distributors",
        "resource_id": "d1",
        "territory": "United Kingdom",
        "field": "activeStatus",
        "current_value": "active",
        "detected_value": "inactive",
        "confidence": "high",
        "source": "https://arrow.example.com",
        "status": "pending",
        "created_at": "2026-02-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _seed() -> FakeSupabase:
    return FakeSupabase(
        {
            "distributors": [
                _distributor(),
                _distributor(id="d2", name="MUBI", primary_market="United States", source_url=None),
            ],
            "pending_changes": [
                _change(),
                _change(id="pc2", status="approved", resolved_by="someone"),
                _change(id="pc3", resource_type="festivals"),
            ],
        }
    )


def _setup(client, role: str = "data_admin") -> FakeSupabase:
    fake = _seed()
    client.app.dependency_overrides[get_current_admin] = _admin(role)
    client.app.dependency_overrides[get_supabase] = lambda: fake
    return fake


ROUTES = [
    ("get", BASE, None),
    ("post", BASE, {"payload": {"name": "New"}}),
    ("patch", f"{BASE}/d1", {"payload": {"name": "Renamed"}}),
    ("delete", f"{BASE}/d1", None),
    ("post", f"{BASE}/sync", None),
    ("get", f"{BASE}/sync-status", None),
    ("get", f"{BASE}/pending-changes", None),
    ("post", f"{BASE}/pending-changes/pc1/approve", None),
    ("post", f"{BASE}/pending-changes/pc1/reject", None),
    ("get", f"{BASE}/sync-settings", None),
    ("patch", f"{BASE}/sync-settings", {"enabled": False}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_admin_without_incentive_permission_is_refused(client, method, path, body):
    fake = _setup(client, role="support_admin")
    kwargs = {"headers": HEADERS}
    if body is not None:
        kwargs["json"] = body
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403
    assert "canEditIncentiveData" in response.json()["detail"]
    assert [r["id"] for r in fake.store["distributors"]] == ["d1", "d2"]
    assert fake.store["pending_changes"][0]["status"] == "pending"


@pytest.mark.parametrize("role", ["data_admin", "senior_admin", "master_admin"])
def test_roles_with_incentive_permission_can_list(client, role):
    _setup(client, role=role)
    response = client.get(BASE, headers=HEADERS)
    assert response.status_code == 200


def test_list_returns_camelcase_rows_and_total(client):
    _setup(client)
    response = client.get(f"{BASE}?limit=1&offset=0", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["limit"] == 1
    assert data["offset"] == 0
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["primaryMarket"] == "United Kingdom"
    assert item["specialtyGenres"] == ["Horror"]
    assert "primary_market" not in item


def test_list_materializes_approved_changes_that_have_no_resource(client):
    fake = _setup(client)
    fake.store["pending_changes"].append(
        _change(
            id="pc9",
            status="approved",
            resource_id=None,
            field="name",
            detected_value="Discovered Films",
            source="https://discovered.example.com",
        )
    )
    response = client.get(BASE, headers=HEADERS)
    assert response.status_code == 200
    created = [r for r in fake.store["distributors"] if r.get("source_url") == "https://discovered.example.com"]
    assert len(created) == 1
    assert created[0]["name"] == "Discovered Films"
    pc9 = next(r for r in fake.store["pending_changes"] if r["id"] == "pc9")
    assert pc9["resource_id"] == created[0]["id"]


def test_create_maps_camelcase_payload_to_columns(client):
    fake = _setup(client)
    response = client.post(
        BASE,
        json={"payload": {"id": "", "name": "Neon", "primaryMarket": "United States"}},
        headers=HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Neon"
    assert data["primaryMarket"] == "United States"
    assert data["id"]
    stored = next(r for r in fake.store["distributors"] if r["name"] == "Neon")
    assert stored["primary_market"] == "United States"
    assert stored["created_at"] and stored["updated_at"]


def test_create_rejects_a_body_without_payload(client):
    _setup(client)
    response = client.post(BASE, json={"name": "Neon"}, headers=HEADERS)
    assert response.status_code == 422


def test_update_changes_only_the_target_row(client):
    fake = _setup(client)
    response = client.patch(
        f"{BASE}/d2", json={"payload": {"activeStatus": "inactive"}}, headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["activeStatus"] == "inactive"
    rows = {r["id"]: r for r in fake.store["distributors"]}
    assert rows["d2"]["active_status"] == "inactive"
    assert rows["d1"]["active_status"] == "active"


def test_delete_removes_the_row(client):
    fake = _setup(client)
    response = client.delete(f"{BASE}/d1", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["message"] == "distributor deleted"
    assert [r["id"] for r in fake.store["distributors"]] == ["d2"]


def test_sync_runs_the_scraper_for_distributors(client, monkeypatch):
    _setup(client)
    calls = []

    class StubScraper:
        def __init__(self, supabase, settings):
            pass

        def run_for_resource(self, resource_type, triggered_by):
            calls.append((resource_type, triggered_by))
            return {"status": "started", "resource": resource_type}

    monkeypatch.setattr("app.modules.scraper.service.ScraperService", StubScraper)
    response = client.post(f"{BASE}/sync", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"status": "started", "resource": "distributors"}
    assert calls == [("distributors", "admin")]


def test_sync_failure_is_reported_as_500(client, monkeypatch):
    _setup(client)

    class FailingScraper:
        def __init__(self, supabase, settings):
            pass

        def run_for_resource(self, resource_type, triggered_by):
            raise RuntimeError("scraper exploded")

    monkeypatch.setattr("app.modules.scraper.service.ScraperService", FailingScraper)
    response = client.post(f"{BASE}/sync", headers=HEADERS)
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to trigger sync"


def test_sync_status_counts_territories_and_pending_distributor_changes(client):
    fake = _setup(client)
    response = client.get(f"{BASE}/sync-status", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["territoriesSyncing"] == 2
    assert data["pendingChanges"] == 1
    assert data["daysSinceLastCheck"] == 0
    assert data["nextScheduledCheck"] is None
    assert len(fake.store["sync_settings"]) == 1


def test_pending_changes_lists_only_pending_distributor_changes(client):
    _setup(client)
    response = client.get(f"{BASE}/pending-changes", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert [c["id"] for c in data] == ["pc1"]
    assert data[0]["detectedValue"] == "inactive"
    assert data[0]["resourceId"] == "d1"


def test_approve_applies_detected_value_and_resolves_the_change(client):
    fake = _setup(client)
    response = client.post(f"{BASE}/pending-changes/pc1/approve", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["resolvedBy"] == "admin-1"
    assert data["resolvedAt"]
    d1 = next(r for r in fake.store["distributors"] if r["id"] == "d1")
    assert d1["active_status"] == "inactive"


def test_approve_of_an_unknown_change_is_404(client):
    _setup(client)
    response = client.post(f"{BASE}/pending-changes/missing/approve", headers=HEADERS)
    assert response.status_code == 404


def test_reject_resolves_the_change_without_touching_the_distributor(client):
    fake = _setup(client)
    response = client.post(f"{BASE}/pending-changes/pc1/reject", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"
    assert data["resolvedBy"] == "admin-1"
    d1 = next(r for r in fake.store["distributors"] if r["id"] == "d1")
    assert d1["active_status"] == "active"


def test_reject_of_an_unknown_change_is_404(client):
    _setup(client)
    response = client.post(f"{BASE}/pending-changes/missing/reject", headers=HEADERS)
    assert response.status_code == 404


def test_sync_settings_are_created_with_defaults_on_first_read(client):
    fake = _setup(client)
    response = client.get(f"{BASE}/sync-settings", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "schedule": "monthly",
        "enabled": True,
        "lastSyncAt": None,
        "nextScheduledCheck": None,
    }
    assert fake.store["sync_settings"][0]["resource_type"] == "distributors"


def test_updating_sync_settings_persists_and_schedules_next_check(client):
    fake = _setup(client)
    response = client.patch(
        f"{BASE}/sync-settings",
        json={"schedule": "quarterly", "enabled": False},
        headers=HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["schedule"] == "quarterly"
    assert data["enabled"] is False
    assert data["nextScheduledCheck"]
    stored = fake.store["sync_settings"][0]
    assert stored["schedule"] == "quarterly"
    assert stored["enabled"] is False
