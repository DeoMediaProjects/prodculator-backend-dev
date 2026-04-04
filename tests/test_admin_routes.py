from datetime import date

from fastapi import HTTPException

from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser
from app.modules.email.router import get_email_service


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
        self._count = bool(kwargs.get("count"))
        self._head = bool(kwargs.get("head"))
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
        row = {"id": f"new-{len(self.store[self.table_name]) + 1}", **payload}
        self.store[self.table_name].append(row)
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

        if self._single:
            return FakeResult(data=rows[0] if rows else None)
        return FakeResult(data=rows)

    def _rows(self):
        return [
            row
            for row in self.store[self.table_name]
            if all(row.get(k) == v for k, v in self.filters.items())
        ]


class FakeSupabase:
    def __init__(self):
        self.store = {
            "users": [{"id": "u1", "email": "a@example.com"}],
            "reports": [{"id": "r1", "user_id": "u1", "created_at": "2026-02-01T00:00:00Z"}],
            "subscriptions": [{"id": "s1", "status": "active", "amount_cents": 1000, "currency": "usd"}],
            "production_signals": [{"id": "p1", "territory": "UK", "submission_date": date(2026, 1, 1)}],
            "incentive_programs": [{"id": "i1", "program_name": "Incentive 1"}],
            "crew_costs": [{"id": "c1", "role": "Producer"}],
            "comparable_productions": [{"id": "cp1", "title": "Comp"}],
            "grant_opportunities": [{"id": "g1", "title": "Grant"}],
            "film_festivals": [{"id": "f1", "name": "Festival"}],
        }

    def table(self, name: str):
        self.store.setdefault(name, [])
        return FakeQuery(name, self.store)


class FakeEmailService:
    @staticmethod
    def render(template_name: str, context: dict):
        if template_name == "missing":
            raise ValueError("Unknown email template: missing")
        return "Subject", "<p>Test</p>"

    @staticmethod
    def send(to_email: str, template_name: str, context: dict):
        if template_name == "missing":
            raise ValueError("Unknown email template: missing")
        return None


def _admin_user() -> AdminUser:
    return AdminUser(
        id="admin-1",
        email="admin@example.com",
        name="Admin",
    )


def test_admin_metrics_and_resource_crud(client):
    client.app.dependency_overrides[get_current_admin] = _admin_user
    fake_supabase = FakeSupabase()
    client.app.dependency_overrides[get_supabase] = lambda: fake_supabase

    metrics = client.get("/api/admin/metrics", headers={"Authorization": "Bearer token"})
    assert metrics.status_code == 200
    assert metrics.json()["total_users"] == 1

    production_signals = client.get(
        "/api/admin/production-signals",
        headers={"Authorization": "Bearer token"},
    )
    assert production_signals.status_code == 200
    assert production_signals.json()["total"] == 1
    assert production_signals.json()["items"][0]["id"] == "p1"
    assert production_signals.json()["items"][0]["submission_date"] == "2026-01-01"

    list_response = client.get("/api/admin/incentives", headers={"Authorization": "Bearer token"})
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1

    create_response = client.post(
        "/api/admin/incentives",
        headers={"Authorization": "Bearer token"},
        json={"payload": {"program_name": "New Program"}},
    )
    assert create_response.status_code == 200
    new_id = create_response.json()["id"]

    update_response = client.patch(
        f"/api/admin/incentives/{new_id}",
        headers={"Authorization": "Bearer token"},
        json={"payload": {"program_name": "Updated Program"}},
    )
    assert update_response.status_code == 200
    assert update_response.json()["program_name"] == "Updated Program"

    delete_response = client.delete(
        f"/api/admin/incentives/{new_id}",
        headers={"Authorization": "Bearer token"},
    )
    assert delete_response.status_code == 200


def test_admin_requires_admin(client):
    def deny_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    client.app.dependency_overrides[get_current_admin] = deny_admin
    response = client.get("/api/admin/users", headers={"Authorization": "Bearer token"})
    assert response.status_code == 403


def test_admin_email_preview_and_send(client):
    client.app.dependency_overrides[get_current_admin] = _admin_user
    client.app.dependency_overrides[get_email_service] = lambda: FakeEmailService()

    preview = client.post(
        "/api/admin/email/preview",
        headers={"Authorization": "Bearer token"},
        json={"template_name": "welcome", "context": {"name": "Admin"}},
    )
    assert preview.status_code == 200
    assert preview.json()["subject"] == "Subject"

    send = client.post(
        "/api/admin/email/send-test",
        headers={"Authorization": "Bearer token"},
        json={"to_email": "admin@example.com", "template_name": "welcome", "context": {}},
    )
    assert send.status_code == 200
