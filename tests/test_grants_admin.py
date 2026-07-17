from fastapi import HTTPException

from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser


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
        self.store[self.table_name] = [r for r in self.store[self.table_name] if r not in rows]
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
            "grant_opportunities": [
                {
                    "id": "g1",
                    "title": "BFI Film Fund",
                    "territory": "United Kingdom",
                    "max_amount": "50000",
                    "application_deadline": "2026-06-01",
                    "status": "open",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
            "pending_changes": [
                {
                    "id": "pc-g1",
                    "resource_type": "grants",
                    "resource_id": "g1",
                    "territory": "United Kingdom",
                    "field": "max_amount",
                    "current_value": "50000",
                    "detected_value": "60000",
                    "confidence": "high",
                    "source": "https://example.com/grants",
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
                    "detected_value": "30%",
                    "confidence": "medium",
                    "source": "https://example.com/incentives",
                    "status": "pending",
                    "created_at": "2026-03-01T00:00:00Z",
                    "resolved_at": None,
                    "resolved_by": None,
                },
            ],
            "sync_settings": [],
        }

    def table(self, name: str):
        self.store.setdefault(name, [])
        return FakeQuery(name, self.store)


HEADERS = {"Authorization": "Bearer token"}


def _admin_user() -> AdminUser:
    return AdminUser(id="admin-1", email="admin@example.com", name="Admin", role="master_admin")


def _setup(client):
    fake = FakeSupabase()
    client.app.dependency_overrides[get_current_admin] = _admin_user
    client.app.dependency_overrides[get_supabase] = lambda: fake
    return fake


def test_get_grants_pending_changes(client):
    _setup(client)
    response = client.get("/api/admin/grants/pending-changes", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "pc-g1"
    assert data[0]["field"] == "max_amount"
    assert data[0]["detectedValue"] == "60000"
    assert data[0]["status"] == "pending"


def test_get_grants_sync_status(client):
    _setup(client)
    response = client.get("/api/admin/grants/sync-status", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["territoriesSyncing"] == 1
    assert data["pendingChanges"] == 1
    assert isinstance(data["daysSinceLastCheck"], int)


def test_get_grants_sync_settings_creates_default(client):
    _setup(client)
    response = client.get("/api/admin/grants/sync-settings", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["schedule"] == "monthly"
    assert data["enabled"] is True


def test_update_grants_sync_settings(client):
    _setup(client)
    client.get("/api/admin/grants/sync-settings", headers=HEADERS)
    response = client.patch(
        "/api/admin/grants/sync-settings",
        headers=HEADERS,
        json={"schedule": "quarterly", "enabled": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["schedule"] == "quarterly"
    assert data["enabled"] is False


def test_approve_grant_pending_change_without_resource_id_creates_grant(client):
    fake = _setup(client)
    fake.store["pending_changes"].append(
        {
            "id": "pc-new",
            "resource_type": "grants",
            "resource_id": None,
            "territory": "United States",
            "field": "max_amount",
            "current_value": None,
            "detected_value": "75000",
            "confidence": "medium",
            "source": "https://www.arts.gov/grants",
            "status": "pending",
            "created_at": "2026-03-05T18:36:23.355800",
            "resolved_at": None,
            "resolved_by": None,
        }
    )
    before_count = len(fake.store["grant_opportunities"])

    response = client.post("/api/admin/grants/pending-changes/pc-new/approve", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["resourceId"] is not None
    assert len(fake.store["grant_opportunities"]) == before_count + 1

    row = next(r for r in fake.store["grant_opportunities"] if r["id"] == data["resourceId"])
    assert row["territory"] == "United States"
    assert row["website_url"] == "https://www.arts.gov/grants"
    assert row["max_amount"] == "75000"


def test_list_grants_materializes_approved_changes_without_resource_id(client):
    fake = _setup(client)
    fake.store["pending_changes"].append(
        {
            "id": "pc-approved-no-resource",
            "resource_type": "grants",
            "resource_id": None,
            "territory": "United Kingdom",
            "field": "status",
            "current_value": None,
            "detected_value": "open",
            "confidence": "medium",
            "source": "https://www.bfi.org.uk/get-funding-support",
            "status": "approved",
            "created_at": "2026-03-05T18:36:23.355800",
            "resolved_at": "2026-03-05T19:24:12.570129",
            "resolved_by": "admin-1",
        }
    )

    response = client.get("/api/admin/grants", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2

    row = next(
        r
        for r in fake.store["grant_opportunities"]
        if r.get("website_url") == "https://www.bfi.org.uk/get-funding-support"
    )
    assert row["status"] == "open"

    backfilled = next(
        r for r in fake.store["pending_changes"] if r["id"] == "pc-approved-no-resource"
    )
    assert backfilled["resource_id"] == row["id"]


def test_approve_grant_pending_change(client):
    fake = _setup(client)
    response = client.post("/api/admin/grants/pending-changes/pc-g1/approve", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"

    row = next(r for r in fake.store["grant_opportunities"] if r["id"] == "g1")
    assert row["max_amount"] == "60000"


def test_reject_grant_pending_change(client):
    fake = _setup(client)
    response = client.post("/api/admin/grants/pending-changes/pc-g1/reject", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"

    row = next(r for r in fake.store["grant_opportunities"] if r["id"] == "g1")
    assert row["max_amount"] == "50000"


def test_grant_pending_change_endpoints_require_admin(client):
    def deny():
        raise HTTPException(status_code=403, detail="Admin access required")

    client.app.dependency_overrides[get_current_admin] = deny
    endpoints = [
        ("GET", "/api/admin/grants/sync-status"),
        ("GET", "/api/admin/grants/sync-settings"),
        ("PATCH", "/api/admin/grants/sync-settings"),
        ("GET", "/api/admin/grants/pending-changes"),
        ("POST", "/api/admin/grants/pending-changes/pc-g1/approve"),
        ("POST", "/api/admin/grants/pending-changes/pc-g1/reject"),
    ]
    for method, path in endpoints:
        if method == "GET":
            response = client.get(path, headers=HEADERS)
        elif method == "PATCH":
            response = client.patch(path, headers=HEADERS, json={"schedule": "monthly"})
        else:
            response = client.post(path, headers=HEADERS)
        assert response.status_code == 403
