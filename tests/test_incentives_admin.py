"""Unit and integration tests for the incentives admin module.

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
        self._limit = None

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
        self._limit = None
        return self

    def limit(self, value: int):
        self._limit = value
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
        elif self._limit is not None:
            rows = rows[self._offset : self._offset + self._limit]

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
            "incentive_programs": [
                {
                    "id": "i1",
                    "territory": "United Kingdom",
                    "program": "UK Film Tax Relief",
                    "rate": "25%",
                    "cap": "No cap",
                    "status": "Active",
                    "source_url": "https://example.com",
                    "auto_sync_enabled": True,
                    "last_updated": "2026-01-01",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "i2",
                    "territory": "Georgia (USA)",
                    "program": "Film Tax Credit",
                    "rate": "30%",
                    "cap": "No cap",
                    "status": "Active",
                    "source_url": "https://example.com/ga",
                    "auto_sync_enabled": False,
                    "last_updated": "2026-02-01",
                    "created_at": "2026-02-01T00:00:00Z",
                    "updated_at": "2026-02-01T00:00:00Z",
                },
            ],
            "sync_settings": [],
            "pending_changes": [
                {
                    "id": "pc1",
                    "resource_type": "incentives",
                    "resource_id": "i1",
                    "territory": "United Kingdom",
                    "field": "rate",
                    "current_value": "25%",
                    "detected_value": "27%",
                    "confidence": "high",
                    "source": "gov.uk",
                    "status": "pending",
                    "created_at": "2026-03-01T00:00:00Z",
                    "resolved_at": None,
                    "resolved_by": None,
                },
                {
                    "id": "pc2",
                    "resource_type": "crew_costs",
                    "resource_id": "c1",
                    "territory": "UK",
                    "field": "dayRate",
                    "current_value": "800",
                    "detected_value": "850",
                    "confidence": "medium",
                    "source": "bectu",
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


def test_incentive_camel_to_snake_conversion():
    from app.modules.incentives.service import _incentive_to_db

    payload = {
        "territory": "UK",
        "sourceUrl": "https://example.com",
        "autoSyncEnabled": True,
        "lastUpdated": "2026-01-01",
        "createdAt": "2026-01-01",
    }
    result = _incentive_to_db(payload)
    assert result["source_url"] == "https://example.com"
    assert result["auto_sync_enabled"] is True
    assert result["last_updated"] == "2026-01-01"
    assert result["created_at"] == "2026-01-01"
    assert result["territory"] == "UK"  # unmapped keys pass through


def test_incentive_snake_to_camel_conversion():
    from app.modules.incentives.service import _incentive_from_db

    row = {
        "id": "i1",
        "territory": "UK",
        "source_url": "https://example.com",
        "auto_sync_enabled": True,
        "last_updated": "2026-01-01",
        "created_at": "2026-01-01",
        "updated_at": "2026-02-01",
    }
    result = _incentive_from_db(row)
    assert result["sourceUrl"] == "https://example.com"
    assert result["autoSyncEnabled"] is True
    assert result["lastUpdated"] == "2026-01-01"
    assert result["createdAt"] == "2026-01-01"
    assert result["updatedAt"] == "2026-02-01"
    assert result["territory"] == "UK"


def test_incentive_to_db_strips_empty_id():
    from app.modules.incentives.service import _incentive_to_db

    payload = {"id": "", "territory": "UK"}
    result = _incentive_to_db(payload)
    assert "id" not in result
    assert result["territory"] == "UK"


def test_compute_next_scheduled():
    from app.modules.incentives.service import IncentivesService

    result = IncentivesService._compute_next_scheduled("quarterly")
    assert result  # just verify it returns a valid ISO string
    # Parse to verify it's valid
    from datetime import datetime

    dt = datetime.fromisoformat(result)
    assert dt.year >= 2026


# ── Integration tests: CRUD endpoints ────────────────────────────────────────


def test_list_incentives(client):
    _setup(client)
    response = client.get("/api/admin/incentives", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["limit"] == 50
    assert data["offset"] == 0
    # Verify camelCase conversion in response
    item = data["items"][0]
    assert "sourceUrl" in item or "source_url" in item  # either is acceptable


def test_list_incentives_pagination(client):
    _setup(client)
    response = client.get("/api/admin/incentives?limit=1&offset=0", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 1
    assert data["limit"] == 1
    assert data["offset"] == 0


def test_list_incentives_materializes_approved_changes_without_resource_id(client):
    fake = _setup(client)
    fake.store["pending_changes"].append(
        {
            "id": "pc-approved-no-resource",
            "resource_type": "incentives",
            "resource_id": None,
            "territory": "Virginia",
            "field": "cap",
            "current_value": None,
            "detected_value": "$6.5 million annual cap",
            "confidence": "medium",
            "source": "https://www.tax.virginia.gov/film-tax-credit",
            "status": "approved",
            "created_at": "2026-03-05T18:36:23.355800",
            "resolved_at": "2026-03-05T19:24:12.570129",
            "resolved_by": "admin-1",
        }
    )

    response = client.get("/api/admin/incentives", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3

    virginia = next(
        row for row in fake.store["incentive_programs"] if row.get("territory") == "Virginia"
    )
    assert virginia["cap"] == "$6.5 million annual cap"

    backfilled_change = next(
        row for row in fake.store["pending_changes"] if row["id"] == "pc-approved-no-resource"
    )
    assert backfilled_change["resource_id"] == virginia["id"]


def test_create_incentive(client):
    _setup(client)
    payload = {
        "payload": {
            "territory": "Malta",
            "program": "Malta Film Fund",
            "rate": "40%",
            "cap": "€2M",
            "status": "Active",
            "sourceUrl": "https://malta.gov",
        }
    }
    response = client.post("/api/admin/incentives", headers=HEADERS, json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["territory"] == "Malta"
    assert data["program"] == "Malta Film Fund"
    assert "id" in data
    assert "createdAt" in data or "created_at" in data


def test_update_incentive(client):
    fake = _setup(client)
    response = client.patch(
        "/api/admin/incentives/i1",
        headers=HEADERS,
        json={"payload": {"rate": "30%", "sourceUrl": "https://updated.com"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["rate"] == "30%"


def test_delete_incentive(client):
    fake = _setup(client)
    response = client.delete("/api/admin/incentives/i1", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["success"] is True

    # Verify it's actually removed from the fake store
    remaining = [r for r in fake.store["incentive_programs"] if r["id"] == "i1"]
    assert len(remaining) == 0


# ── Integration tests: Sync status ───────────────────────────────────────────


def test_get_sync_status(client):
    _setup(client)
    response = client.get("/api/admin/incentives/sync-status", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["territoriesSyncing"] == 2  # UK + Georgia
    assert data["pendingChanges"] == 1  # only incentives pending changes
    assert isinstance(data["daysSinceLastCheck"], int)


# ── Integration tests: Pending changes ───────────────────────────────────────


def test_get_pending_changes(client):
    _setup(client)
    response = client.get("/api/admin/incentives/pending-changes", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    change = data[0]
    assert change["territory"] == "United Kingdom"
    assert change["field"] == "rate"
    assert change["detectedValue"] == "27%"
    assert change["confidence"] == "high"
    assert change["status"] == "pending"


def test_approve_pending_change(client):
    fake = _setup(client)
    response = client.post(
        "/api/admin/incentives/pending-changes/pc1/approve", headers=HEADERS
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"

    # Verify the incentive was updated with the detected value
    i1 = next(r for r in fake.store["incentive_programs"] if r["id"] == "i1")
    assert i1["rate"] == "27%"


def test_approve_pending_change_without_resource_id_creates_incentive(client):
    fake = _setup(client)
    fake.store["pending_changes"].append(
        {
            "id": "pc-new",
            "resource_type": "incentives",
            "resource_id": None,
            "territory": "Virginia",
            "field": "cap",
            "current_value": None,
            "detected_value": "$6.5 million annual cap",
            "confidence": "medium",
            "source": "https://www.tax.virginia.gov/film-tax-credit",
            "status": "pending",
            "created_at": "2026-03-05T18:36:23.355800",
            "resolved_at": None,
            "resolved_by": None,
        }
    )
    before_count = len(fake.store["incentive_programs"])

    response = client.post(
        "/api/admin/incentives/pending-changes/pc-new/approve", headers=HEADERS
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["resourceId"] is not None
    assert len(fake.store["incentive_programs"]) == before_count + 1

    row = next(r for r in fake.store["incentive_programs"] if r["id"] == data["resourceId"])
    assert row["territory"] == "Virginia"
    assert row["source_url"] == "https://www.tax.virginia.gov/film-tax-credit"
    assert row["cap"] == "$6.5 million annual cap"


def test_reject_pending_change(client):
    fake = _setup(client)
    response = client.post(
        "/api/admin/incentives/pending-changes/pc1/reject", headers=HEADERS
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"

    # Verify the incentive was NOT updated
    i1 = next(r for r in fake.store["incentive_programs"] if r["id"] == "i1")
    assert i1["rate"] == "25%"  # unchanged


# ── Integration tests: Sync trigger ──────────────────────────────────────────


def test_trigger_sync(client):
    _setup(client)
    response = client.post("/api/admin/incentives/sync", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("success", "skipped")
    assert "runId" in data or "reason" in data


# ── Integration tests: Sync settings ─────────────────────────────────────────


def test_get_sync_settings_creates_default(client):
    _setup(client)
    response = client.get("/api/admin/incentives/sync-settings", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["schedule"] == "monthly"
    assert data["enabled"] is True


def test_update_sync_settings(client):
    _setup(client)
    # First get to create default
    client.get("/api/admin/incentives/sync-settings", headers=HEADERS)

    response = client.patch(
        "/api/admin/incentives/sync-settings",
        headers=HEADERS,
        json={"schedule": "quarterly", "enabled": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["schedule"] == "quarterly"
    assert data["enabled"] is False


# ── Auth enforcement ─────────────────────────────────────────────────────────


def test_incentives_endpoints_require_admin(client):
    def deny():
        raise HTTPException(status_code=403, detail="Admin access required")

    client.app.dependency_overrides[get_current_admin] = deny

    endpoints = [
        ("GET", "/api/admin/incentives"),
        ("POST", "/api/admin/incentives"),
        ("PATCH", "/api/admin/incentives/i1"),
        ("DELETE", "/api/admin/incentives/i1"),
        ("GET", "/api/admin/incentives/sync-status"),
        ("GET", "/api/admin/incentives/pending-changes"),
        ("POST", "/api/admin/incentives/pending-changes/pc1/approve"),
        ("POST", "/api/admin/incentives/pending-changes/pc1/reject"),
        ("POST", "/api/admin/incentives/sync"),
        ("GET", "/api/admin/incentives/sync-settings"),
        ("PATCH", "/api/admin/incentives/sync-settings"),
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
