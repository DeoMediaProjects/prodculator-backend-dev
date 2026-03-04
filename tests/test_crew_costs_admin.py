"""Unit and integration tests for the crew costs admin module.

Covers:
- CRUD endpoints (list, create, update, delete)
- camelCase ↔ snake_case conversion
- Sync status, pending changes, approve/reject, sync trigger, sync settings
- Auth enforcement (403 when not admin)
"""

from fastapi import HTTPException

from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser


# ── Fake infrastructure ──────────────────────────────────────────────────────


class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, table_name: str, store: dict[str, list[dict]]):
        self.table_name = table_name
        self.store = store
        self.filters = {}
        self._data = None
        self._count = False
        self._head = False
        self._single = False
        self._offset = 0
        self._end = None

    def select(self, *_args, **kwargs):
        if kwargs.get("count"):
            self._count = True
        if kwargs.get("head"):
            self._head = True
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def gte(self, *_args):
        return self

    def lte(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start: int, end: int):
        self._offset = start
        self._end = end
        return self

    def insert(self, payload):
        if isinstance(payload, list):
            payload = payload[0]
        row = {**payload}
        if "id" not in row:
            row["id"] = f"new-{len(self.store.get(self.table_name, [])) + 1}"
        self.store.setdefault(self.table_name, []).append(row)
        self._data = [row]
        return self

    def update(self, payload):
        self._data = payload
        return self

    def delete(self):
        rows = self._rows()
        self.store[self.table_name] = [
            r for r in self.store[self.table_name] if r not in rows
        ]
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        if self._count and self._head:
            return FakeResult(data=None, count=len(self._rows()))

        if isinstance(self._data, list):
            rows = self._data
        elif isinstance(self._data, dict):
            rows = self._rows()
            if rows:
                rows[0].update(self._data)
        else:
            rows = self._rows()

        if self._end is not None:
            rows = rows[self._offset : self._end + 1]

        if self._single:
            return FakeResult(data=rows[0] if rows else None)
        return FakeResult(data=rows)

    def _rows(self):
        return [
            row
            for row in self.store.get(self.table_name, [])
            if all(row.get(k) == v for k, v in self.filters.items())
        ]


class FakeSupabase:
    def __init__(self):
        self.store = {
            "crew_costs": [
                {
                    "id": "c1",
                    "territory": "United Kingdom",
                    "role": "Director of Photography",
                    "category": "Camera",
                    "day_rate": 850,
                    "week_rate": 3800,
                    "union": "BECTU",
                    "last_updated": "2026-01-15",
                    "source": "BECTU Rate Card 2025/26",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-15T00:00:00Z",
                },
                {
                    "id": "c2",
                    "territory": "United Kingdom",
                    "role": "Gaffer",
                    "category": "Lighting",
                    "day_rate": 650,
                    "week_rate": 2900,
                    "union": "BECTU",
                    "last_updated": "2026-01-15",
                    "source": "BECTU Rate Card 2025/26",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-15T00:00:00Z",
                },
                {
                    "id": "c3",
                    "territory": "Georgia (USA)",
                    "role": "Gaffer",
                    "category": "Lighting",
                    "day_rate": 700,
                    "week_rate": 3100,
                    "union": "IATSE",
                    "last_updated": "2026-02-01",
                    "source": "IATSE Scale 2025",
                    "created_at": "2026-02-01T00:00:00Z",
                    "updated_at": "2026-02-01T00:00:00Z",
                },
            ],
            "sync_settings": [],
            "pending_changes": [
                {
                    "id": "pc-c1",
                    "resource_type": "crew_costs",
                    "resource_id": "c1",
                    "territory": "United Kingdom",
                    "field": "day_rate",
                    "current_value": "850",
                    "detected_value": "900",
                    "confidence": "high",
                    "source": "BECTU 2026/27 update",
                    "status": "pending",
                    "created_at": "2026-03-01T00:00:00Z",
                    "resolved_at": None,
                    "resolved_by": None,
                },
                {
                    "id": "pc-i1",
                    "resource_type": "incentives",
                    "resource_id": "i1",
                    "territory": "UK",
                    "field": "rate",
                    "current_value": "25%",
                    "detected_value": "27%",
                    "confidence": "medium",
                    "source": "gov.uk",
                    "status": "pending",
                    "created_at": "2026-03-01T00:00:00Z",
                    "resolved_at": None,
                    "resolved_by": None,
                },
            ],
        }

    def table(self, name: str):
        self.store.setdefault(name, [])
        return FakeQuery(name, self.store)


HEADERS = {"Authorization": "Bearer token"}


def _admin_user() -> AdminUser:
    return AdminUser(id="admin-1", email="admin@example.com", name="Admin")


def _setup(client):
    fake = FakeSupabase()
    client.app.dependency_overrides[get_current_admin] = _admin_user
    client.app.dependency_overrides[get_supabase] = lambda: fake
    return fake


# ── Unit tests: camelCase conversion ─────────────────────────────────────────


def test_crew_camel_to_snake_conversion():
    from app.modules.crew_costs.service import _crew_to_db

    payload = {
        "territory": "UK",
        "role": "DP",
        "dayRate": 850,
        "weekRate": 3800,
        "lastUpdated": "2026-01-01",
    }
    result = _crew_to_db(payload)
    assert result["day_rate"] == 850
    assert result["week_rate"] == 3800
    assert result["last_updated"] == "2026-01-01"
    assert result["territory"] == "UK"
    assert result["role"] == "DP"


def test_crew_snake_to_camel_conversion():
    from app.modules.crew_costs.service import _crew_from_db

    row = {
        "id": "c1",
        "territory": "UK",
        "day_rate": 850,
        "week_rate": 3800,
        "last_updated": "2026-01-01",
        "created_at": "2026-01-01",
        "updated_at": "2026-02-01",
    }
    result = _crew_from_db(row)
    assert result["dayRate"] == 850
    assert result["weekRate"] == 3800
    assert result["lastUpdated"] == "2026-01-01"
    assert result["createdAt"] == "2026-01-01"
    assert result["updatedAt"] == "2026-02-01"
    assert result["territory"] == "UK"


def test_crew_to_db_strips_empty_id():
    from app.modules.crew_costs.service import _crew_to_db

    payload = {"id": "", "territory": "UK", "dayRate": 500}
    result = _crew_to_db(payload)
    assert "id" not in result
    assert result["day_rate"] == 500


def test_crew_compute_next_scheduled():
    from app.modules.crew_costs.service import CrewCostsService

    result = CrewCostsService._compute_next_scheduled("annual")
    from datetime import datetime

    dt = datetime.fromisoformat(result)
    assert dt.year >= 2026


# ── Integration tests: CRUD endpoints ────────────────────────────────────────


def test_list_crew_costs(client):
    _setup(client)
    response = client.get("/api/admin/crew-costs", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3
    assert data["limit"] == 50
    assert data["offset"] == 0


def test_list_crew_costs_pagination(client):
    _setup(client)
    response = client.get("/api/admin/crew-costs?limit=2&offset=0", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["limit"] == 2


def test_create_crew_cost(client):
    _setup(client)
    payload = {
        "payload": {
            "territory": "France",
            "role": "Sound Mixer",
            "category": "Sound",
            "dayRate": 700,
            "weekRate": 3100,
            "union": "SNTPCT",
            "source": "SNTPCT Rate Card 2026",
        }
    }
    response = client.post("/api/admin/crew-costs", headers=HEADERS, json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["territory"] == "France"
    assert data["role"] == "Sound Mixer"
    assert "id" in data


def test_update_crew_cost(client):
    _setup(client)
    response = client.patch(
        "/api/admin/crew-costs/c1",
        headers=HEADERS,
        json={"payload": {"dayRate": 900, "weekRate": 4000}},
    )
    assert response.status_code == 200
    data = response.json()
    # The update merges the camelCase-converted payload into the row
    assert data.get("dayRate") == 900 or data.get("day_rate") == 900


def test_delete_crew_cost(client):
    fake = _setup(client)
    response = client.delete("/api/admin/crew-costs/c1", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["success"] is True

    remaining = [r for r in fake.store["crew_costs"] if r["id"] == "c1"]
    assert len(remaining) == 0


# ── Integration tests: Sync status ───────────────────────────────────────────


def test_get_crew_costs_sync_status(client):
    _setup(client)
    response = client.get("/api/admin/crew-costs/sync-status", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["territoriesSyncing"] == 2  # UK + Georgia
    assert data["pendingChanges"] == 1  # only crew_costs pending changes
    assert isinstance(data["daysSinceLastCheck"], int)


# ── Integration tests: Pending changes ───────────────────────────────────────


def test_get_crew_costs_pending_changes(client):
    _setup(client)
    response = client.get("/api/admin/crew-costs/pending-changes", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    change = data[0]
    assert change["territory"] == "United Kingdom"
    assert change["field"] == "day_rate"
    assert change["detectedValue"] == "900"
    assert change["confidence"] == "high"


def test_approve_crew_cost_pending_change(client):
    fake = _setup(client)
    response = client.post(
        "/api/admin/crew-costs/pending-changes/pc-c1/approve", headers=HEADERS
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"

    # Verify the crew cost was updated
    c1 = next(r for r in fake.store["crew_costs"] if r["id"] == "c1")
    assert c1["day_rate"] == "900"  # applied from detected_value (string)


def test_reject_crew_cost_pending_change(client):
    fake = _setup(client)
    response = client.post(
        "/api/admin/crew-costs/pending-changes/pc-c1/reject", headers=HEADERS
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"

    # Verify the crew cost was NOT updated
    c1 = next(r for r in fake.store["crew_costs"] if r["id"] == "c1")
    assert c1["day_rate"] == 850  # unchanged


# ── Integration tests: Sync trigger ──────────────────────────────────────────


def test_trigger_crew_costs_sync(client):
    _setup(client)
    response = client.post("/api/admin/crew-costs/sync", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Sync triggered successfully"
    assert data["resourceType"] == "crew_costs"
    assert "triggeredAt" in data


# ── Integration tests: Sync settings ─────────────────────────────────────────


def test_get_crew_costs_sync_settings_creates_default(client):
    _setup(client)
    response = client.get("/api/admin/crew-costs/sync-settings", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["schedule"] == "monthly"
    assert data["enabled"] is True


def test_update_crew_costs_sync_settings(client):
    _setup(client)
    # First get to create default
    client.get("/api/admin/crew-costs/sync-settings", headers=HEADERS)

    response = client.patch(
        "/api/admin/crew-costs/sync-settings",
        headers=HEADERS,
        json={"schedule": "biannual"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["schedule"] == "biannual"


# ── Auth enforcement ─────────────────────────────────────────────────────────


def test_crew_costs_endpoints_require_admin(client):
    def deny():
        raise HTTPException(status_code=403, detail="Admin access required")

    client.app.dependency_overrides[get_current_admin] = deny

    endpoints = [
        ("GET", "/api/admin/crew-costs"),
        ("POST", "/api/admin/crew-costs"),
        ("PATCH", "/api/admin/crew-costs/c1"),
        ("DELETE", "/api/admin/crew-costs/c1"),
        ("GET", "/api/admin/crew-costs/sync-status"),
        ("GET", "/api/admin/crew-costs/pending-changes"),
        ("POST", "/api/admin/crew-costs/pending-changes/pc1/approve"),
        ("POST", "/api/admin/crew-costs/pending-changes/pc1/reject"),
        ("POST", "/api/admin/crew-costs/sync"),
        ("GET", "/api/admin/crew-costs/sync-settings"),
        ("PATCH", "/api/admin/crew-costs/sync-settings"),
    ]

    for method, path in endpoints:
        if method == "GET":
            r = client.get(path, headers=HEADERS)
        elif method == "POST":
            r = client.post(path, headers=HEADERS, json={"payload": {}})
        elif method == "PATCH":
            r = client.patch(path, headers=HEADERS, json={"payload": {}})
        elif method == "DELETE":
            r = client.delete(path, headers=HEADERS)
        assert r.status_code == 403, f"{method} {path} should require admin, got {r.status_code}"
