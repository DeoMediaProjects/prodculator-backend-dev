from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.main import app
from app.modules.support.router import get_support_service
from app.modules.support.schemas import SupportInquiryCreate, SupportInquirySubmitResponse
from app.modules.support.service import SupportService


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


class FakeSupportService:
    def __init__(self):
        self.calls = []

    def submit_inquiry(self, user, inquiry):
        self.calls.append({"user": user, "inquiry": inquiry})
        return SupportInquirySubmitResponse(inquiry_id="inq-1")


def _settings():
    return SimpleNamespace(CONTACT_EMAIL="support@prodculator.com")


def _test_client():
    app.dependency_overrides.clear()
    return TestClient(app)


def test_submit_support_inquiry_route_success(auth_user):
    service = FakeSupportService()
    client = _test_client()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_support_service] = lambda: service

    try:
        response = client.post(
            "/api/support/contact",
            json={
                "category": "technical",
                "message": "I need help understanding a dashboard error.",
                "selected_faq_question": "Is my script safe? Do you store it?",
                "page_url": "http://localhost:5173/dashboard",
            },
        )
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "inquiry_id": "inq-1",
        "message": "Support inquiry received",
    }
    assert service.calls[0]["user"].email == "user@example.com"
    assert service.calls[0]["inquiry"].category == "technical"


def test_submit_support_inquiry_requires_auth():
    client = _test_client()
    try:
        response = client.post(
            "/api/support/contact",
            json={"category": "general", "message": "I need support with my account."},
        )
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code in (401, 403)


def test_submit_support_inquiry_validates_message(auth_user):
    client = _test_client()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user

    try:
        response = client.post(
            "/api/support/contact",
            json={"category": "general", "message": "too short"},
        )
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_support_service_stores_inquiry_and_sends_emails(auth_user):
    db = FakeDb()
    email_service = FakeEmailService()
    service = SupportService(db, _settings(), email_service)

    result = service.submit_inquiry(
        auth_user,
        SupportInquiryCreate(
            category="report",
            message="Please help me understand why my report failed to generate.",
            selected_faq_question="What is a Scripteligence Report?",
            selected_faq_answer="A Scripteligence Report is the full output of our analysis.",
            page_url="http://localhost:5173/dashboard",
        ),
    )

    rows = db.store["support_inquiries"]
    assert result.success is True
    assert rows[0]["id"] == result.inquiry_id
    assert rows[0]["user_email"] == "user@example.com"
    assert rows[0]["category"] == "report"
    assert rows[0]["internal_email_sent"] is True
    assert rows[0]["auto_reply_sent"] is True
    assert rows[0]["email_error"] is None
    assert [sent["to_email"] for sent in email_service.sent] == [
        "support@prodculator.com",
        "user@example.com",
    ]
    assert [sent["template_name"] for sent in email_service.sent] == [
        "support_inquiry",
        "support_inquiry_confirmation",
    ]
