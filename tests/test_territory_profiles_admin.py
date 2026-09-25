import pytest

from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser
from tests.admin_fakes import FakeSupabase

HEADERS = {"Authorization": "Bearer token"}
BASE = "/api/admin/territory-profiles"


def _admin(role: str):
    return lambda: AdminUser(id="admin-1", email="admin@example.com", name="Admin", role=role)


def _profile(**overrides):
    base = {
        "id": "tp1",
        "territory": "United Kingdom",
        "iso_code": "GB",
        "region": "Europe",
        "crew_depth_tier": "deep",
        "crew_depth_score": 9,
        "cert_weeks_min": 4.0,
        "bankability_real_world_confirms": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _setup(client, role: str = "data_admin") -> FakeSupabase:
    fake = FakeSupabase(
        {
            "territory_profiles": [
                _profile(),
                _profile(id="tp2", territory="Canada", iso_code="CA", region="North America"),
            ]
        }
    )
    client.app.dependency_overrides[get_current_admin] = _admin(role)
    client.app.dependency_overrides[get_supabase] = lambda: fake
    return fake


ROUTES = [
    ("get", BASE, None),
    ("post", BASE, {"payload": {"territory": "Ireland"}}),
    ("patch", f"{BASE}/tp1", {"payload": {"region": "Elsewhere"}}),
    ("delete", f"{BASE}/tp1", None),
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
    rows = fake.store["territory_profiles"]
    assert [r["id"] for r in rows] == ["tp1", "tp2"]
    assert rows[0]["region"] == "Europe"


@pytest.mark.parametrize("role", ["data_admin", "senior_admin", "master_admin"])
def test_roles_with_incentive_permission_can_list(client, role):
    _setup(client, role=role)
    response = client.get(BASE, headers=HEADERS)
    assert response.status_code == 200


def test_list_returns_camelcase_rows_and_total(client):
    _setup(client)
    response = client.get(f"{BASE}?limit=1", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["limit"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["isoCode"] == "GB"
    assert item["crewDepthScore"] == 9
    assert item["bankabilityRealWorldConfirms"] is None
    assert "iso_code" not in item


def test_create_stores_a_canonical_territory(client):
    fake = _setup(client)
    response = client.post(
        BASE,
        json={"payload": {"id": "", "territory": "Ireland", "isoCode": "IE", "paymentWeeksMax": 12}},
        headers=HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["territory"] == "Ireland"
    assert data["isoCode"] == "IE"
    assert data["paymentWeeksMax"] == 12
    assert data["id"]
    stored = next(r for r in fake.store["territory_profiles"] if r["territory"] == "Ireland")
    assert stored["iso_code"] == "IE"
    assert stored["payment_weeks_max"] == 12


def test_create_requires_a_territory(client):
    fake = _setup(client)
    response = client.post(BASE, json={"payload": {"territory": "  "}}, headers=HEADERS)
    assert response.status_code == 400
    assert response.json()["detail"] == "territory is required"
    assert len(fake.store["territory_profiles"]) == 2


def test_create_rejects_a_non_canonical_territory(client):
    fake = _setup(client)
    response = client.post(BASE, json={"payload": {"territory": "Atlantis"}}, headers=HEADERS)
    assert response.status_code == 400
    assert "not a canonical territory" in response.json()["detail"]
    assert len(fake.store["territory_profiles"]) == 2


def test_create_rejects_a_duplicate_territory(client):
    fake = _setup(client)
    response = client.post(BASE, json={"payload": {"territory": "Canada"}}, headers=HEADERS)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]
    assert len(fake.store["territory_profiles"]) == 2


def test_update_changes_fields_but_never_the_id(client):
    fake = _setup(client)
    response = client.patch(
        f"{BASE}/tp2",
        json={"payload": {"id": "hijack", "crewDepthTier": "moderate", "reviewNotes": "Checked"}},
        headers=HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "tp2"
    assert data["crewDepthTier"] == "moderate"
    assert data["reviewNotes"] == "Checked"
    rows = {r["id"]: r for r in fake.store["territory_profiles"]}
    assert set(rows) == {"tp1", "tp2"}
    assert rows["tp2"]["crew_depth_tier"] == "moderate"
    assert rows["tp1"]["crew_depth_tier"] == "deep"


def test_delete_removes_the_row(client):
    fake = _setup(client)
    response = client.delete(f"{BASE}/tp1", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["message"] == "territory profile deleted"
    assert [r["id"] for r in fake.store["territory_profiles"]] == ["tp2"]
