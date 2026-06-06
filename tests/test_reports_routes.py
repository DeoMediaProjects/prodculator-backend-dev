import json

from app.core.dependencies import get_current_user, get_optional_user
from app.modules.reports import router as reports_router
from app.modules.reports.router import get_report_service


class _StubResult:
    def __init__(self, data):
        self.data = data


class _StubQuery:
    """Minimal query stub for the subscription/usage check the create-report
    route runs via SubscriptionService(service.supabase). Models an empty
    account that owns one pay-per-report credit, so a paid report is allowed."""

    def __init__(self, table_name):
        self._table = table_name
        self._single = False

    def select(self, *_): return self
    def eq(self, *_): return self
    def in_(self, *_): return self
    def gte(self, *_): return self
    def lte(self, *_): return self
    def limit(self, *_): return self
    def update(self, *_): return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        if self._table == "users":
            row = {"credits_remaining": 1}
            return _StubResult(row if self._single else [row])
        # subscriptions / reports: empty — no active sub, no prior reports.
        return _StubResult(None if self._single else [])


class _StubSupabase:
    def table(self, name):
        return _StubQuery(name)


class FakeReportService:
    def __init__(self):
        self._reports = {}
        self._counter = 0
        # The route builds SubscriptionService(service.supabase) for the usage
        # gate; the real ReportService exposes .supabase, so the fake must too.
        self.supabase = _StubSupabase()

    def create_report(
        self,
        user_id: str,
        script_title: str,
        report_type: str,
        script_file_path=None,
        request_metadata=None,
    ):
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
        return [r for r in self._reports.values() if r["user_id"] == user_id and r["report_type"] != "preview"]

    def get_report(self, report_id: str):
        return self._reports.get(report_id)

    def get_report_by_share_token(self, share_token: str):
        return None


VALID_REPORT_PAYLOAD = {
    "script_title": "My Script",
    "report_type": "paid",
    "script_file_path": "user-1/123.txt",
    "genre": ["Drama"],
    "budget_amount": 3000000,
    "budget_currency": "GBP",
    "format": "Feature Film",
    "country": "UK",
    "location_strategy": "open",
    "production_priority": "full",
}


def test_report_create_triggers_background_and_status_transitions(client, auth_user, monkeypatch):
    service = FakeReportService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_optional_user] = lambda: auth_user
    client.app.dependency_overrides[get_report_service] = lambda: service

    def fake_task(report_id, *_args, **_kwargs):
        service._reports[report_id]["status"] = "completed"
        service._reports[report_id]["completed_at"] = "2026-01-01T00:01:00Z"

    monkeypatch.setattr(reports_router, "process_report_task", fake_task)

    create_response = client.post(
        "/api/reports",
        headers={"Authorization": "Bearer token"},
        data={"body": json.dumps(VALID_REPORT_PAYLOAD)},
        files={"script_file": ("script.txt", b"INT. HOUSE - DAY\nHello world.", "text/plain")},
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
        report_type="paid",
        script_file_path="another-user/123.txt",
    )
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_report_service] = lambda: service

    response = client.get(f"/api/reports/{report_id}", headers={"Authorization": "Bearer token"})
    assert response.status_code == 403
