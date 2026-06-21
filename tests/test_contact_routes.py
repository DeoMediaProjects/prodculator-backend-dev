from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.modules.contact.router import get_contact_service
from app.modules.contact.schemas import ContactMessageCreate, ContactMessageSubmitResponse
from app.modules.contact.service import ContactService


class FakeTable:
    def __init__(self, store, table_name):
        self.store = store
        self.table_name = table_name
        self.action = None
        self.payload = None
        self.filters = []

    def insert(self, payload):
        self.action = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self.action == "insert":
            row = dict(self.payload)
            rows.append(row)
            return SimpleNamespace(data=[row])
        if self.action == "update":
            updated = []
            for row in rows:
                if all(row.get(key) == value for key, value in self.filters):
                    row.update(self.payload)
                    updated.append(dict(row))
            return SimpleNamespace(data=updated)
        return SimpleNamespace(data=rows)


class FakeDb:
    def __init__(self):
        self.store = {}

    def table(self, table_name):
        return FakeTable(self.store, table_name)


class FakeEmailService:
    def __init__(self):
        self.sent = []

    def send(self, to_email, template_name, context, attachments=None):
        self.sent.append(
            {
                "to_email": to_email,
                "template_name": template_name,
                "context": context,
                "attachments": attachments or [],
            }
        )


class FakeContactService:
    def __init__(self):
        self.calls = []

    def submit_message(self, payload):
        self.calls.append({"payload": payload})
        return ContactMessageSubmitResponse(message_id="msg-1")


def _settings():
    return SimpleNamespace(CONTACT_EMAIL="support@prodculator.com")


def _test_client():
    app.dependency_overrides.clear()
    return TestClient(app)


def _valid_body():
    return {
        "name": "Jane Producer",
        "email": "jane@example.com",
        "company": "Acme Films",
        "category": "sales",
        "subject": "Enterprise demo",
        "message": "We'd like to discuss enterprise intelligence access.",
    }


def test_submit_contact_message_route_success():
    # Public endpoint: a valid submission succeeds without any authentication.
    service = FakeContactService()
    client = _test_client()
    client.app.dependency_overrides[get_contact_service] = lambda: service

    try:
        response = client.post("/api/contact", json=_valid_body())
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message_id": "msg-1",
        "message": "Message received",
    }
    assert service.calls[0]["payload"].email == "jane@example.com"
    assert service.calls[0]["payload"].category == "sales"


def test_submit_contact_message_validates_message():
    client = _test_client()
    try:
        body = _valid_body()
        body["message"] = "too short"
        response = client.post("/api/contact", json=body)
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_submit_contact_message_validates_email():
    client = _test_client()
    try:
        body = _valid_body()
        body["email"] = "not-an-email"
        response = client.post("/api/contact", json=body)
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_contact_service_stores_message_and_sends_emails():
    db = FakeDb()
    email_service = FakeEmailService()
    service = ContactService(db, _settings(), email_service)

    result = service.submit_message(
        ContactMessageCreate(
            name="Jane Producer",
            email="jane@example.com",
            company="Acme Films",
            category="sales",
            subject="Enterprise demo",
            message="We'd like to discuss enterprise intelligence access.",
            page_url="http://localhost:5173/b2b",
        ),
    )

    rows = db.store["contact_messages"]
    assert result.success is True
    assert rows[0]["id"] == result.message_id
    assert rows[0]["email"] == "jane@example.com"
    assert rows[0]["category"] == "sales"
    assert rows[0]["internal_email_sent"] is True
    assert rows[0]["auto_reply_sent"] is True
    assert rows[0]["email_error"] is None
    assert [sent["to_email"] for sent in email_service.sent] == [
        "support@prodculator.com",
        "jane@example.com",
    ]
    assert [sent["template_name"] for sent in email_service.sent] == [
        "contact_message",
        "contact_message_confirmation",
    ]
