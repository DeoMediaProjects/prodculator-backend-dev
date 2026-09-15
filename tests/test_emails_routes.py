import pytest

from app.core.dependencies import get_current_admin
from app.modules.admin.schemas import AdminUser
from app.modules.email.transactional_router import get_email_service


class FakeEmailService:
    def __init__(self):
        self.sent_payloads = []

    def render(self, template_name: str, context: dict):
        if template_name == "missing_template":
            raise ValueError("Unknown email template: missing_template")
        return f"subject:{template_name}", f"<html><body>{template_name}</body></html>"

    def send(self, to_email: str, template_name: str, context: dict, attachments=None):
        if template_name == "missing_template":
            raise ValueError("Unknown email template: missing_template")
        self.sent_payloads.append(
            {
                "to_email": to_email,
                "template_name": template_name,
                "context": context,
                "attachments": attachments or [],
            }
        )


@pytest.fixture(autouse=True)
def authenticated_email_admin(client):
    client.app.dependency_overrides[get_current_admin] = lambda: AdminUser(
        id="admin-1",
        email="admin@example.com",
        name="Admin",
        role="support_admin",
    )


def test_preview_transactional_email_success(client):
    fake_service = FakeEmailService()
    client.app.dependency_overrides[get_email_service] = lambda: fake_service

    response = client.post(
        "/api/emails/preview",
        json={
            "template": "report_ready",
            "data": {
                "userName": "Sarah",
                "scriptTitle": "THE LAST FRONTIER",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "subject": "subject:report_ready",
        "html": "<html><body>report_ready</body></html>",
    }


def test_transactional_email_requires_admin_authentication(client):
    client.app.dependency_overrides.pop(get_current_admin, None)
    response = client.post(
        "/api/emails/preview",
        json={"template": "report_ready", "data": {}},
    )
    assert response.status_code == 401


def test_preview_transactional_email_returns_400_for_unknown_template(client):
    fake_service = FakeEmailService()
    client.app.dependency_overrides[get_email_service] = lambda: fake_service

    response = client.post(
        "/api/emails/preview",
        json={
            "template": "missing_template",
            "data": {},
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "error": "Unknown email template: missing_template",
    }


def test_send_transactional_email_success(client):
    fake_service = FakeEmailService()
    client.app.dependency_overrides[get_email_service] = lambda: fake_service

    response = client.post(
        "/api/emails",
        json={
            "template": "report_ready",
            "to": "recipient@example.com",
            "data": {
                "userName": "Sarah",
                "scriptTitle": "THE LAST FRONTIER",
                "reportUrl": "https://example.com/reports/123",
            },
            "attachments": [
                {
                    "filename": "report.pdf",
                    "content": "SGVsbG8=",
                    "type": "application/pdf",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert fake_service.sent_payloads == [
        {
            "to_email": "recipient@example.com",
            "template_name": "report_ready",
            "context": {
                "userName": "Sarah",
                "scriptTitle": "THE LAST FRONTIER",
                "reportUrl": "https://example.com/reports/123",
            },
            "attachments": [
                {
                    "filename": "report.pdf",
                    "content": "SGVsbG8=",
                    "type": "application/pdf",
                }
            ],
        }
    ]


def test_send_transactional_email_returns_400_for_unknown_template(client):
    fake_service = FakeEmailService()
    client.app.dependency_overrides[get_email_service] = lambda: fake_service

    response = client.post(
        "/api/emails",
        json={
            "template": "missing_template",
            "to": "recipient@example.com",
            "data": {},
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "error": "Unknown email template: missing_template",
    }


def test_send_transactional_email_rejects_invalid_attachment_base64(client):
    response = client.post(
        "/api/emails",
        json={
            "template": "report_ready",
            "to": "recipient@example.com",
            "data": {},
            "attachments": [
                {
                    "filename": "report.pdf",
                    "content": "***not-base64***",
                    "type": "application/pdf",
                }
            ],
        },
    )

    assert response.status_code == 422
    detail = response.json().get("detail") or []
    assert any("Attachment content must be a valid base64 string" in str(item) for item in detail)
