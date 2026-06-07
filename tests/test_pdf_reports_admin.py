import pytest

from app.core.dependencies import get_current_admin
from app.modules.admin.schemas import AdminUser
from app.modules.pdf_reports.admin_router import get_pdf_reports_service

HEADERS = {"Authorization": "Bearer token"}


class _FakeEmailService:
    sent: list[tuple] = []

    def __init__(self, *_args, **_kwargs):
        pass

    def send(self, to_email, template_name, context):
        _FakeEmailService.sent.append((to_email, template_name, context))


class FakePdfService:
    def __init__(self, reports=None, user_emails=None, file_size="1.2 MB", pdf_bytes=b"%PDF fake"):
        self.reports = reports or {}
        self.user_emails = user_emails or {}
        self.file_size = file_size
        self.pdf_bytes = pdf_bytes
        self.downloaded_marked: list[str] = []

    def list_reports(self, *, limit=25, offset=0):
        rows = list(self.reports.values())
        return rows, len(rows)

    def get_file_size(self, user_id, report_id, settings):
        return self.file_size

    def get_report(self, report_id):
        return self.reports.get(report_id)

    def download_pdf(self, user_id, report_id, settings):
        return self.pdf_bytes

    def mark_downloaded(self, report_id):
        self.downloaded_marked.append(report_id)

    def get_user_email(self, user_id):
        return self.user_emails.get(user_id)


def _report(**overrides):
    base = {
        "id": "rp1",
        "user_id": "u1",
        "script_title": "My Script",
        "email": "owner@example.com",
        "created_at": "2026-03-01T00:00:00Z",
        "downloaded": False,
        "status": "completed",
        "pdf_url": "https://files.example.com/u1/rp1.pdf",
    }
    base.update(overrides)
    return base


def _admin_user() -> AdminUser:
    return AdminUser(id="admin-1", email="admin@example.com", name="Admin", role="master_admin")


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    _FakeEmailService.sent = []
    monkeypatch.setattr(
        "app.modules.pdf_reports.admin_router.EmailService", _FakeEmailService
    )


def _setup(client, service: FakePdfService):
    client.app.dependency_overrides[get_current_admin] = _admin_user
    client.app.dependency_overrides[get_pdf_reports_service] = lambda: service


def test_list_pdf_reports(client):
    service = FakePdfService(reports={"rp1": _report()})
    _setup(client, service)
    response = client.get("/api/admin/pdf-reports", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["title"] == "My Script"
    assert item["email"] == "owner@example.com"
    assert item["size"] == "1.2 MB"


def test_preview_completed_report(client):
    service = FakePdfService(reports={"rp1": _report()})
    _setup(client, service)
    response = client.get("/api/admin/pdf-reports/rp1/preview", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["url"] == "https://files.example.com/u1/rp1.pdf"


def test_preview_incomplete_report_returns_404(client):
    service = FakePdfService(reports={"rp1": _report(status="processing", pdf_url=None)})
    _setup(client, service)
    response = client.get("/api/admin/pdf-reports/rp1/preview", headers=HEADERS)
    assert response.status_code == 404


def test_preview_missing_report_returns_404(client):
    _setup(client, FakePdfService())
    response = client.get("/api/admin/pdf-reports/missing/preview", headers=HEADERS)
    assert response.status_code == 404


def test_download_pdf_report(client):
    service = FakePdfService(reports={"rp1": _report(script_title="My Script: Final!")})
    _setup(client, service)
    response = client.get("/api/admin/pdf-reports/rp1/download", headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="My Script Final.pdf"'
    assert response.content == b"%PDF fake"
    assert service.downloaded_marked == ["rp1"]


def test_resend_with_explicit_email(client):
    service = FakePdfService(reports={"rp1": _report()})
    _setup(client, service)
    response = client.post(
        "/api/admin/pdf-reports/rp1/resend",
        headers=HEADERS,
        json={"payload": {"email": "new@example.com"}},
    )
    assert response.status_code == 200
    assert "new@example.com" in response.json()["message"]
    assert _FakeEmailService.sent[0][0] == "new@example.com"


def test_resend_without_email_falls_back_to_user_email(client):
    service = FakePdfService(
        reports={"rp1": _report()}, user_emails={"u1": "fallback@example.com"}
    )
    _setup(client, service)
    response = client.post(
        "/api/admin/pdf-reports/rp1/resend",
        headers=HEADERS,
        json={"payload": {}},
    )
    assert response.status_code == 200
    assert _FakeEmailService.sent[0][0] == "fallback@example.com"


def test_resend_without_any_email_returns_400(client):
    service = FakePdfService(reports={"rp1": _report()}, user_emails={})
    _setup(client, service)
    response = client.post(
        "/api/admin/pdf-reports/rp1/resend",
        headers=HEADERS,
        json={"payload": {}},
    )
    assert response.status_code == 400


def test_resend_missing_report_returns_404(client):
    _setup(client, FakePdfService())
    response = client.post(
        "/api/admin/pdf-reports/missing/resend",
        headers=HEADERS,
        json={"payload": {"email": "x@example.com"}},
    )
    assert response.status_code == 404


def test_pdf_reports_require_permission(client):
    def insufficient_role() -> AdminUser:
        return AdminUser(id="a2", email="data@example.com", name="Data", role="data_admin")

    client.app.dependency_overrides[get_current_admin] = insufficient_role
    client.app.dependency_overrides[get_pdf_reports_service] = lambda: FakePdfService()
    response = client.get("/api/admin/pdf-reports", headers=HEADERS)
    assert response.status_code == 403
