import json

from app.core.dependencies import get_current_user, get_optional_user
from app.modules.reports import router as reports_router
from app.modules.reports.router import (
    _build_free_tier_report_data,
    get_email_gating_service,
    get_report_service,
)
from app.modules.scripts.service import ClaudeUnavailableError, ScriptAnalysisService


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

    def generate_preview_report(self, request_metadata, script_service):
        return {
            "genre": ", ".join(request_metadata.get("genre") or []),
            "tone": "Preview tone",
            "scale": request_metadata.get("format") or "Feature Film",
            "complexity": "Medium",
            "executiveSummary": {
                "keyInsights": "Preview generated from backend datasets.",
                "keyFlags": ["Weather risk"],
                "actionTimeline": [
                    {"action": "Verify incentive", "deadline": "Before shoot"}
                ],
            },
            "locationRankings": [
                {
                    "name": "United Kingdom",
                    "country": "United Kingdom",
                    "score": 80,
                    "costEfficiency": 70,
                    "crewDepth": 80,
                    "infrastructure": 85,
                    "incentiveStrength": 90,
                    "currencyAdvantage": 60,
                    "reasoning": ["Backend preview route"],
                }
            ],
            "incentiveEstimates": [
                {
                    "territory": "United Kingdom",
                    "program": "IFTC",
                    "rate": "25%",
                    "estimatedRebate": "GBP 100,000",
                }
            ],
            "nextSteps": [{"priority": "URGENT", "action": "Verify incentive"}],
            "scriptIntelligence": {
                "complexityDrivers": [{"flag": "Weather", "detail": "Rain"}]
            },
        }


class FakeEmailGatingService:
    def __init__(self):
        self.records: list[tuple[str, bool]] = []
        self.blocked: set[str] = set()

    def is_blocked(self, email: str) -> bool:
        return email in self.blocked

    def create_record(self, email: str, report_generated: bool = False):
        self.records.append((email, report_generated))
        return {"email": email, "report_generated": report_generated}


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


def test_free_tier_report_data_redacts_financial_and_action_detail():
    report_data = {
        "genre": "Drama",
        "tone": "Specific script tone",
        "scale": "Feature Film",
        "complexity": "High",
        "executiveSummary": {
            "keyInsights": (
                "**Production Overview**\nSpecific protagonist and location detail.\n\n"
                "**Primary Recommendation**\nUnited Kingdom FRS: 84 - Bankable with a net rate of 39.75%, estimated net rebate is £318,000.\n\n"
                "**Second Territory**\nFrance has a 30% rate and €277,752 rebate.\n\n"
                "**Third Territory**\nSpain has a 30% rate and €295,112 rebate.\n\n"
                "**Strategic Recommendations**\nApply for the BFI cultural test immediately."
            ),
            "headlineNetBudget": "approximately £682,000",
            "recommendedTerritory": "United Kingdom",
            "recommendedTerritoryRebate": "£318,000",
            "recommendedTerritoryPaymentSpeed": "6-12 months",
            "keyFlags": ["BFI cultural test timing", "Music rights clearance"],
            "actionTimeline": [{"action": "Apply for BFI cultural test", "deadline": "2 weeks"}],
        },
        "locationRankings": [
            {
                "name": "United Kingdom",
                "country": "United Kingdom",
                "score": 84,
                "costEfficiency": 70,
                "crewDepth": 80,
                "infrastructure": 85,
                "incentiveStrength": 90,
                "currencyAdvantage": 50,
                "incentiveReliability": 90,
                "bankabilityLabel": "BANKABLE",
                "rebatePercent": "39.75% net (53% gross)",
                "reasoning": ["Script-specific intelligence"],
                "keyRisks": ["Specific risk"],
                "paymentSpeed": "6-12 months",
            },
            {"name": "France", "country": "France", "score": 76, "bankabilityLabel": "VERIFY FIRST"},
            {"name": "Spain", "country": "Spain", "score": 72, "bankabilityLabel": "VERIFY FIRST"},
        ],
        "incentiveEstimates": [
            {
                "territory": "United Kingdom",
                "program": "IFTC",
                "rate": "39.75% net (53% gross)",
                "estimatedRebate": "£318,000",
                "requirements": ["Theatrical release"],
            }
        ],
        "financialAnalysis": {
            "budgetScenarios": [
                {
                    "territory": "United Kingdom",
                    "programme": "IFTC",
                    "netRebate": "£318,000",
                }
            ]
        },
        "nextSteps": [
            {"priority": "URGENT", "action": "Apply for BFI cultural test", "reason": "Blocking"}
        ],
        "scriptIntelligence": {
            "complexityDrivers": [
                {"flag": "Music rights", "detail": "Specific detail"},
                {"flag": "Location", "detail": "Specific detail"},
            ]
        },
        "alternativeStrategy": "Fallback to France if BFI fails.",
        "dimensionVerdicts": {"United Kingdom": {"costEfficiency": "Specific verdict"}},
        "comparables": [{"title": "Example"}],
        "fundingOpportunities": [{"name": "Grant"}],
    }

    result = _build_free_tier_report_data(report_data)

    summary = result["executiveSummary"]
    assert "headlineNetBudget" not in summary
    assert "recommendedTerritoryRebate" not in summary
    assert "actionTimeline" not in summary
    assert "keyFlags" not in summary
    assert "£318,000" not in summary["keyInsights"]
    assert "39.75%" not in summary["keyInsights"]
    assert "Third Territory" not in summary["keyInsights"]
    assert "Upgrade to see" in summary["keyInsights"]

    assert result["previewUrgentActionCount"] == 1
    assert result["previewComplexityFactorCount"] == 2
    assert result["nextSteps"] == []
    assert "scriptIntelligence" not in result
    assert "alternativeStrategy" not in result
    assert "dimensionVerdicts" not in result

    top = result["locationRankings"][0]
    assert top["name"] == "United Kingdom"
    assert "reasoning" not in top
    assert "rebatePercent" not in top
    assert result["locationRankings"][1]["lockedPreview"] is True
    assert result["locationRankings"][1]["name"] == "Territory #2"

    incentive = result["incentiveEstimates"][0]
    assert incentive == {"territory": "United Kingdom", "program": "IFTC"}
    scenario = result["financialAnalysis"]["budgetScenarios"][0]
    assert scenario == {"territory": "United Kingdom", "programme": "IFTC"}
    assert "comparables" not in result
    assert "fundingOpportunities" not in result


def test_report_create_triggers_background_and_status_transitions(client, auth_user, monkeypatch):
    service = FakeReportService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_optional_user] = lambda: auth_user
    client.app.dependency_overrides[get_report_service] = lambda: service

    # Pre-flight Claude probe passes (Scriptelligence reachable).
    monkeypatch.setattr(ScriptAnalysisService, "check_available", lambda self: None)

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


def test_preview_report_accepts_json_and_records_email_gating(client):
    service = FakeReportService()
    gating = FakeEmailGatingService()
    client.app.dependency_overrides[get_report_service] = lambda: service
    client.app.dependency_overrides[get_email_gating_service] = lambda: gating

    response = client.post(
        "/api/reports/preview",
        json={
            **VALID_REPORT_PAYLOAD,
            "report_type": "preview",
            "email": "preview@example.com",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reportType"] == "preview"
    assert body["analysis"]["locationRankings"][0]["name"] == "United Kingdom"
    assert gating.records == [("preview@example.com", True)]


def test_multipart_endpoint_rejects_preview_pointing_to_json_endpoint(client):
    """Previews are served only by POST /api/reports/preview now; the multipart
    endpoint rejects report_type=preview with a clear pointer."""
    service = FakeReportService()
    client.app.dependency_overrides[get_report_service] = lambda: service

    response = client.post(
        "/api/reports",
        data={"body": json.dumps({**VALID_REPORT_PAYLOAD, "report_type": "preview"})},
    )
    assert response.status_code == 400
    assert "/api/reports/preview" in response.json()["detail"]


def test_report_create_enqueues_on_rq_when_queue_enabled(client, auth_user, monkeypatch):
    """When the durable queue is enabled, report generation is enqueued onto RQ
    and is NOT run in-process — the API only creates the job and returns."""
    service = FakeReportService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_optional_user] = lambda: auth_user
    client.app.dependency_overrides[get_report_service] = lambda: service
    monkeypatch.setattr(ScriptAnalysisService, "check_available", lambda self: None)

    class FakeQueue:
        name = "reports"

        def __init__(self):
            self.calls = []

        def enqueue(self, func, args=None, **kwargs):
            self.calls.append((func, args, kwargs))

    fake_queue = FakeQueue()
    monkeypatch.setattr(reports_router, "get_report_queue", lambda settings: fake_queue)

    # If the inline path ever runs while queueing is enabled, fail loudly.
    def fail_task(*_a, **_kw):
        raise AssertionError("process_report_task must not run inline when queued")

    monkeypatch.setattr(reports_router, "process_report_task", fail_task)

    response = client.post(
        "/api/reports",
        headers={"Authorization": "Bearer token"},
        data={"body": json.dumps(VALID_REPORT_PAYLOAD)},
        files={"script_file": ("script.txt", b"INT. HOUSE - DAY\nHello world.", "text/plain")},
    )
    assert response.status_code == 200
    report_id = response.json()["report_id"]

    # Exactly one job enqueued, with the task reference and plain-string args
    # (no Settings object — the worker re-resolves settings itself).
    assert len(fake_queue.calls) == 1
    func, args, kwargs = fake_queue.calls[0]
    assert func is fail_task
    assert args == (
        report_id,
        auth_user.id,
        auth_user.email,
        "INT. HOUSE - DAY\nHello world.",
        "script.txt",
    )
    assert all(isinstance(a, str) for a in args)

    # Nothing ran inline, so the report is still processing.
    status_response = client.get(
        f"/api/reports/{report_id}/status",
        headers={"Authorization": "Bearer token"},
    )
    assert status_response.json()["status"] == "processing"


def test_report_create_blocked_when_claude_unavailable(client, auth_user, monkeypatch):
    """When Scriptelligence (Claude) is unreachable, the report is NOT created
    and the user is NOT charged — a 503 with a display message is returned."""
    service = FakeReportService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_optional_user] = lambda: auth_user
    client.app.dependency_overrides[get_report_service] = lambda: service

    def boom(self):
        raise ClaudeUnavailableError("Anthropic API unavailable (connection)")

    monkeypatch.setattr(ScriptAnalysisService, "check_available", boom)

    # If the pre-flight ever let this through, the background task would explode —
    # makes an accidental charge loud rather than silent.
    def fail_task(*_a, **_kw):
        raise AssertionError("process_report_task must not run when Claude is down")

    monkeypatch.setattr(reports_router, "process_report_task", fail_task)

    response = client.post(
        "/api/reports",
        headers={"Authorization": "Bearer token"},
        data={"body": json.dumps(VALID_REPORT_PAYLOAD)},
        files={"script_file": ("script.txt", b"INT. HOUSE - DAY\nHello world.", "text/plain")},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Scriptelligence is currently not available"
    # No report row created -> no quota charge.
    assert service._reports == {}


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
