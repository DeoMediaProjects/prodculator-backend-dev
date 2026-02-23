from app.core.dependencies import get_current_user
from app.modules.reports import router as reports_router
from app.modules.reports.router import get_report_service


class FakeReportService:
    def __init__(self):
        self._reports = {}
        self._counter = 0

    def create_report(self, user_id: str, script_title: str, report_type: str, script_file_path=None):
        self._counter += 1
        report_id = f"report-{self._counter}"
        self._reports[report_id] = {
            "id": report_id,
            "user_id": user_id,
            "script_title": script_title,
            "status": "processing",
            "report_type": report_type,
            "report_data": None,
            "pdf_url": None,
            "created_at": "2026-01-01T00:00:00Z",
            "completed_at": None,
        }
        return report_id

    def get_user_reports(self, user_id: str):
        return [r for r in self._reports.values() if r["user_id"] == user_id]

    def get_report(self, report_id: str):
        return self._reports.get(report_id)

    def get_report_by_share_token(self, share_token: str):
        return None


def test_report_create_triggers_background_and_status_transitions(client, auth_user, monkeypatch):
    service = FakeReportService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_report_service] = lambda: service

    def fake_task(report_id, *_args, **_kwargs):
        service._reports[report_id]["status"] = "completed"
        service._reports[report_id]["completed_at"] = "2026-01-01T00:01:00Z"

    monkeypatch.setattr(reports_router, "process_report_task", fake_task)

    create_response = client.post(
        "/api/reports",
        headers={"Authorization": "Bearer token"},
        json={
            "script_title": "My Script",
            "report_type": "free",
            "script_file_path": "user-1/123.txt",
        },
    )
    assert create_response.status_code == 200
    report_id = create_response.json()["report_id"]

    status_response = client.get(
        f"/api/reports/{report_id}/status",
        headers={"Authorization": "Bearer token"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"


def test_report_access_denied_for_other_user(client, auth_user):
    service = FakeReportService()
    report_id = service.create_report(
        user_id="another-user",
        script_title="Other Script",
        report_type="free",
        script_file_path="another-user/123.txt",
    )
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_report_service] = lambda: service

    response = client.get(f"/api/reports/{report_id}", headers={"Authorization": "Bearer token"})
    assert response.status_code == 403
