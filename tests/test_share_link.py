"""Tests for the share link feature.

Endpoints covered:
  POST   /api/reports/{report_id}/share    → create/return share token (Studio only)
  DELETE /api/reports/{report_id}/share    → revoke share token (Studio only)
  GET    /api/reports/shared/{share_token} → public read (no auth)

Coverage goals:
- Plan gating: create/delete require Studio (free/professional/producer → 403)
- Studio user can create a share link (200, returns token + url)
- Share link is idempotent: calling create twice returns the same token
- Only the report owner can create/revoke (other user → 403)
- Report not found → 404
- After revocation: GET shared endpoint returns 404 for the old token
- GET shared endpoint requires no authentication
- GET shared endpoint returns full (producer-level) data
- shareToken field exposed in GET /api/reports/{id} response
- Service unit tests: create_share_link, revoke_share_link, get_report_by_share_token
"""
import pytest

from app.core.dependencies import get_current_user
from app.modules.auth.schemas import AuthUser
from app.modules.reports.router import get_report_service


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(plan: str, user_id: str = "user-1") -> AuthUser:
    return AuthUser(
        id=user_id,
        email=f"{plan}@example.com",
        name="Test",
        company="Co",
        role="Producer",
        user_type="paid" if plan != "free" else "free",
        credits_remaining=0,
        plan=plan,
    )


FULL_REPORT_DATA = {
    "genre": "Thriller",
    "tone": "Dark",
    "scale": "Medium",
    "complexity": "High",
    "locationRankings": [
        {
            "name": "London",
            "country": "UK",
            "score": 85,
            "costEfficiency": 70,
            "crewDepth": 90,
            "infrastructure": 88,
            "incentiveStrength": 82,
            "currencyAdvantage": 65,
            "reasoning": ["Strong crew base"],
        }
    ],
    "incentiveEstimates": [],
    "crewInsights": [],
    "comparables": [],
    "weatherLogistics": [],
    "fundingOpportunities": [],
}


class FakeShareReportService:
    """In-memory report store that supports share_token CRUD."""

    def __init__(self, reports: dict | None = None):
        self._reports: dict[str, dict] = reports or {}

    def get_report(self, report_id: str) -> dict | None:
        return self._reports.get(report_id)

    def get_report_by_share_token(self, share_token: str) -> dict | None:
        for r in self._reports.values():
            if r.get("share_token") == share_token:
                return r
        return None

    def create_share_link(self, report_id: str, user_id: str) -> str:
        report = self.get_report(report_id)
        if not report:
            raise ValueError("report_not_found")
        if report.get("user_id") != user_id:
            raise PermissionError("access_denied")
        if report.get("share_token"):
            return report["share_token"]
        import secrets
        token = secrets.token_urlsafe(32)
        report["share_token"] = token
        return token

    def revoke_share_link(self, report_id: str, user_id: str) -> None:
        report = self.get_report(report_id)
        if not report:
            raise ValueError("report_not_found")
        if report.get("user_id") != user_id:
            raise PermissionError("access_denied")
        report["share_token"] = None

    # Stubs for other methods used by the router
    def get_user_reports(self, user_id: str) -> list:
        return []

    def complete_report(self, *args, **kwargs) -> None:
        pass

    def fail_report(self, *args, **kwargs) -> None:
        pass


def _make_report(
    report_id: str = "rpt-1",
    user_id: str = "user-1",
    report_data: dict | None = None,
    share_token: str | None = None,
) -> dict:
    return {
        "id": report_id,
        "user_id": user_id,
        "script_title": "Test Film",
        "status": "completed",
        "report_type": "paid",
        "report_data": report_data,
        "pdf_url": None,
        "share_token": share_token,
        "created_at": "2026-01-01T00:00:00Z",
    }


# ── Plan gating — POST /share ─────────────────────────────────────────────────

@pytest.mark.parametrize("plan,expected_status", [
    ("free", 403),
    ("professional", 403),
    ("producer", 403),
    ("studio", 200),
])
def test_create_share_plan_gating(client, plan, expected_status):
    """Only Studio plan can create a share link."""
    report = _make_report(report_data=FULL_REPORT_DATA)
    service = FakeShareReportService({"rpt-1": report})
    user = _make_user(plan)

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.post("/api/reports/rpt-1/share", headers={"Authorization": "Bearer token"})
    assert resp.status_code == expected_status, (
        f"plan={plan}: expected {expected_status}, got {resp.status_code} — {resp.text}"
    )


# ── Plan gating — DELETE /share ───────────────────────────────────────────────

@pytest.mark.parametrize("plan,expected_status", [
    ("free", 403),
    ("professional", 403),
    ("producer", 403),
    ("studio", 204),
])
def test_revoke_share_plan_gating(client, plan, expected_status):
    """Only Studio plan can revoke a share link."""
    report = _make_report(report_data=FULL_REPORT_DATA, share_token="existing-token")
    service = FakeShareReportService({"rpt-1": report})
    user = _make_user(plan)

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.delete("/api/reports/rpt-1/share", headers={"Authorization": "Bearer token"})
    assert resp.status_code == expected_status


# ── Create share link ─────────────────────────────────────────────────────────

def test_create_share_link_returns_token_and_url(client):
    """Studio user gets a share_token and a share_url in the response."""
    report = _make_report(report_data=FULL_REPORT_DATA)
    service = FakeShareReportService({"rpt-1": report})
    user = _make_user("studio")

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.post("/api/reports/rpt-1/share", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    data = resp.json()
    assert "share_token" in data
    assert "share_url" in data
    assert data["share_token"] in data["share_url"]
    assert len(data["share_token"]) >= 20


def test_create_share_link_is_idempotent(client):
    """Calling create twice returns the same token without changing it."""
    report = _make_report(report_data=FULL_REPORT_DATA, share_token="fixed-token-abc123")
    service = FakeShareReportService({"rpt-1": report})
    user = _make_user("studio")

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp1 = client.post("/api/reports/rpt-1/share", headers={"Authorization": "Bearer token"})
    resp2 = client.post("/api/reports/rpt-1/share", headers={"Authorization": "Bearer token"})
    assert resp1.json()["share_token"] == resp2.json()["share_token"] == "fixed-token-abc123"


def test_create_share_link_denied_for_other_users_report(client):
    """Studio user cannot create a share link for a report they don't own."""
    report = _make_report(user_id="owner-user", report_data=FULL_REPORT_DATA)
    service = FakeShareReportService({"rpt-1": report})
    user = _make_user("studio", user_id="attacker-user")

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.post("/api/reports/rpt-1/share", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 403


def test_create_share_link_404_on_missing_report(client):
    service = FakeShareReportService({})
    user = _make_user("studio")

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.post("/api/reports/nonexistent/share", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 404


# ── Revoke share link ─────────────────────────────────────────────────────────

def test_revoke_share_link_removes_token(client):
    """After revocation the share_token is cleared on the report."""
    report = _make_report(report_data=FULL_REPORT_DATA, share_token="tok-abc")
    service = FakeShareReportService({"rpt-1": report})
    user = _make_user("studio")

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.delete("/api/reports/rpt-1/share", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 204
    # Verify the token was cleared in the in-memory store
    assert service._reports["rpt-1"]["share_token"] is None


def test_revoke_share_link_denied_for_other_user(client):
    report = _make_report(user_id="owner-user", report_data=FULL_REPORT_DATA, share_token="tok-abc")
    service = FakeShareReportService({"rpt-1": report})
    user = _make_user("studio", user_id="attacker-user")

    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.delete("/api/reports/rpt-1/share", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 403


# ── Public GET /api/reports/shared/{token} ────────────────────────────────────

def test_get_shared_report_requires_no_auth(client):
    """Public share endpoint returns 200 without any auth header."""
    report = _make_report(report_data=FULL_REPORT_DATA, share_token="pub-tok-xyz")
    service = FakeShareReportService({"rpt-1": report})

    client.app.dependency_overrides[get_report_service] = lambda: service
    # No get_current_user override — ensuring no auth is needed

    resp = client.get("/api/reports/shared/pub-tok-xyz")
    assert resp.status_code == 200


def test_get_shared_report_returns_full_data(client):
    """Shared report endpoint returns all sections at producer-level fidelity."""
    report = _make_report(report_data=FULL_REPORT_DATA, share_token="pub-tok-xyz")
    service = FakeShareReportService({"rpt-1": report})

    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.get("/api/reports/shared/pub-tok-xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "rpt-1"
    assert data["analysis"] is not None
    # All location rankings present (producer-level = no territory cap)
    assert len(data["analysis"]["locationRankings"]) == 1


def test_get_shared_report_404_for_invalid_token(client):
    """Unknown share token → 404."""
    service = FakeShareReportService({})
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.get("/api/reports/shared/nonexistent-token")
    assert resp.status_code == 404


def test_get_shared_report_404_after_revocation(client):
    """After revocation the old share token should no longer work."""
    report = _make_report(report_data=FULL_REPORT_DATA, share_token="tok-revoke-me")
    service = FakeShareReportService({"rpt-1": report})
    studio_user = _make_user("studio")

    client.app.dependency_overrides[get_current_user] = lambda: studio_user
    client.app.dependency_overrides[get_report_service] = lambda: service

    # Public access works before revocation
    resp_before = client.get("/api/reports/shared/tok-revoke-me")
    assert resp_before.status_code == 200

    # Revoke
    revoke_resp = client.delete("/api/reports/rpt-1/share", headers={"Authorization": "Bearer token"})
    assert revoke_resp.status_code == 204

    # Public access should now fail
    resp_after = client.get("/api/reports/shared/tok-revoke-me")
    assert resp_after.status_code == 404


# ── shareToken in report GET response ────────────────────────────────────────

def test_report_get_exposes_share_token(client, auth_user):
    """GET /api/reports/{id} includes shareToken in the response."""
    report = _make_report(
        user_id=auth_user.id,
        report_data=FULL_REPORT_DATA,
        share_token="exposed-tok",
    )
    service = FakeShareReportService({"rpt-1": report})

    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.get("/api/reports/rpt-1", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    assert resp.json().get("shareToken") == "exposed-tok"


def test_report_get_share_token_null_when_no_share_link(client, auth_user):
    """shareToken is null when no share link exists."""
    report = _make_report(user_id=auth_user.id, report_data=FULL_REPORT_DATA, share_token=None)
    service = FakeShareReportService({"rpt-1": report})

    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_report_service] = lambda: service

    resp = client.get("/api/reports/rpt-1", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    assert resp.json().get("shareToken") is None


# ── Service unit tests ────────────────────────────────────────────────────────

class _MinimalDB:
    """Minimal DB stub for service-level tests."""
    def __init__(self, report: dict):
        self._report = report
        self._table = None

    def table(self, name: str) -> "_MinimalDB":
        self._table = name
        self._op = None
        self._filters = {}
        return self

    def select(self, *_) -> "_MinimalDB":
        return self

    def update(self, data: dict) -> "_MinimalDB":
        self._report.update(data)
        return self

    def eq(self, field: str, value) -> "_MinimalDB":
        self._filters[field] = value
        return self

    def single(self) -> "_MinimalDB":
        return self

    def execute(self):
        return type("R", (), {"data": self._report})()


def test_service_create_share_link_generates_token():
    from app.modules.reports.service import ReportService

    report = _make_report(report_data={}, share_token=None)
    db = _MinimalDB(report)
    svc = ReportService(db)

    token = svc.create_share_link("rpt-1", "user-1")
    assert token is not None
    assert len(token) >= 20
    assert report["share_token"] == token


def test_service_create_share_link_is_idempotent():
    from app.modules.reports.service import ReportService

    report = _make_report(report_data={}, share_token="already-set-token")
    db = _MinimalDB(report)
    svc = ReportService(db)

    token = svc.create_share_link("rpt-1", "user-1")
    assert token == "already-set-token"


def test_service_create_share_link_raises_on_wrong_owner():
    from app.modules.reports.service import ReportService

    report = _make_report(user_id="owner", report_data={})
    db = _MinimalDB(report)
    svc = ReportService(db)

    with pytest.raises(PermissionError):
        svc.create_share_link("rpt-1", "attacker")


def test_service_revoke_share_link_clears_token():
    from app.modules.reports.service import ReportService

    report = _make_report(report_data={}, share_token="tok-to-clear")
    db = _MinimalDB(report)
    svc = ReportService(db)

    svc.revoke_share_link("rpt-1", "user-1")
    assert report["share_token"] is None


def test_service_get_report_by_share_token_returns_none_after_revoke():
    """After revoke, querying by the old token should return None."""
    from app.modules.reports.service import ReportService

    report = _make_report(report_data={}, share_token="tok-abc")

    class MatchingDB:
        def __init__(self, r): self._r = r; self._token = None
        def table(self, *_): return self
        def select(self, *_): return self
        def update(self, data): self._r.update(data); return self
        def eq(self, field, value):
            if field == "share_token": self._token = value
            return self
        def single(self): return self
        def execute(self):
            if self._token is not None:
                match = self._r if self._r.get("share_token") == self._token else None
            else:
                match = self._r
            return type("R", (), {"data": match})()

    db = MatchingDB(report)
    svc = ReportService(db)

    assert svc.get_report_by_share_token("tok-abc") is not None
    svc.revoke_share_link("rpt-1", "user-1")
    assert svc.get_report_by_share_token("tok-abc") is None
