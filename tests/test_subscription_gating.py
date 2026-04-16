"""Integration tests for subscription-based feature gating across routes."""

from app.core.dependencies import get_current_user, get_supabase
from app.modules.auth.schemas import AuthUser
from app.modules.payments.router import get_stripe_service


def _make_user(plan: str = "free", **kwargs) -> AuthUser:
    defaults = dict(
        id="user-1",
        email="user@example.com",
        user_type="paid" if plan != "free" else "free",
        credits_remaining=0,
        plan=plan,
    )
    defaults.update(kwargs)
    return AuthUser(**defaults)


# ---------------------------------------------------------------------------
# FakeSupabase that returns configurable report data
# ---------------------------------------------------------------------------

class FakeQuery:
    def __init__(self, rows, table_name=""):
        self.rows = rows
        self.table_name = table_name
        self.filters = {}
        self._single = False

    def select(self, _v):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def gte(self, _k, _v):
        return self

    def lte(self, _k, _v):
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, _n):
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        filtered = [
            row for row in self.rows
            if all(row.get(k) == v for k, v in self.filters.items())
        ]
        if self._single:
            result_data = filtered[0] if filtered else None
        else:
            result_data = filtered

        class Result:
            data = result_data

        return Result()


class FakeSupabase:
    def __init__(self, tables=None):
        self._tables = tables or {}

    def table(self, name):
        rows = self._tables.get(name, [])
        return FakeQuery(rows, name)

    @property
    def storage(self):
        return self

    def from_(self, _bucket):
        return self

    def download(self, _path):
        return b"%PDF-fake-content"


class FakeStripeService:
    @staticmethod
    def create_checkout_session(price_id, user_email, user_id, metadata=None):
        return {"session_id": "cs_test", "url": "https://checkout.stripe.test"}

    @staticmethod
    def create_subscription_checkout(price_id, user_email, user_id, metadata=None):
        return {"session_id": "cs_sub", "url": "https://checkout.stripe.test/sub"}

    @staticmethod
    def cancel_subscription(subscription_id):
        return None

    @staticmethod
    def create_customer_portal_session(customer_id):
        return "https://billing.stripe.test"

    @staticmethod
    def update_payment_method(customer_id, payment_method_id):
        return None


# ---------------------------------------------------------------------------
# PDF Download Gating Tests
# ---------------------------------------------------------------------------

class TestPDFDownloadGating:
    """PDF download requires professional plan or higher."""

    def test_free_user_blocked_from_pdf_download(self, client):
        free_user = _make_user(plan="free")
        report = {
            "id": "report-1",
            "user_id": "user-1",
            "script_title": "Test Script",
            "status": "completed",
            "report_type": "free",
            "pdf_url": "reports/user-1/report-1.pdf",
            "report_data": {"locationRankings": []},
            "created_at": "2026-01-01",
        }
        db = FakeSupabase({"reports": [report]})
        client.app.dependency_overrides[get_current_user] = lambda: free_user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/reports/report-1/pdf",
            headers={"Authorization": "Bearer token"},
        )
        assert response.status_code == 403
        assert "professional" in response.json()["detail"].lower()

    def test_professional_user_can_download_pdf(self, client):
        pro_user = _make_user(plan="professional")
        report = {
            "id": "report-1",
            "user_id": "user-1",
            "script_title": "Test Script",
            "status": "completed",
            "report_type": "paid",
            "pdf_url": "reports/user-1/report-1.pdf",
            "report_data": {"locationRankings": []},
            "created_at": "2026-01-01",
        }
        db = FakeSupabase({"reports": [report]})
        client.app.dependency_overrides[get_current_user] = lambda: pro_user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/reports/report-1/pdf",
            headers={"Authorization": "Bearer token"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"

    def test_studio_user_can_download_pdf(self, client):
        studio_user = _make_user(plan="studio")
        report = {
            "id": "report-1",
            "user_id": "user-1",
            "script_title": "Test Script",
            "status": "completed",
            "report_type": "paid",
            "pdf_url": "reports/user-1/report-1.pdf",
            "report_data": {"locationRankings": []},
            "created_at": "2026-01-01",
        }
        db = FakeSupabase({"reports": [report]})
        client.app.dependency_overrides[get_current_user] = lambda: studio_user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/reports/report-1/pdf",
            headers={"Authorization": "Bearer token"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Report Data Filtering Tests
# ---------------------------------------------------------------------------

class TestReportDataFiltering:
    """Free users get limited data; professional users get full data."""

    _FULL_REPORT = {
        "id": "report-1",
        "user_id": "user-1",
        "script_title": "Test Script",
        "status": "completed",
        "report_type": "paid",
        "pdf_url": "reports/user-1/report-1.pdf",
        "created_at": "2026-01-01",
        "report_data": {
            "locationRankings": [
                {"name": "UK", "score": 95},
                {"name": "Canada", "score": 90},
                {"name": "Ireland", "score": 85},
                {"name": "Germany", "score": 80},
                {"name": "France", "score": 75},
                {"name": "Australia", "score": 70},
            ],
            "financialAnalysis": {"netBudget": 1000000},
            "crewInsights": [{"role": "Director"}],
            "comparables": [{"title": "Film A"}],
            "weatherLogistics": {"bestMonth": "June"},
            "fundingOpportunities": [{"name": "Grant A"}],
            "investorSummary": {"roi": "12%"},
            "scriptSummary": {"genre": "Drama"},
        },
    }

    def test_free_user_gets_only_3_territories(self, client):
        free_user = _make_user(plan="free")
        db = FakeSupabase({"reports": [self._FULL_REPORT]})
        client.app.dependency_overrides[get_current_user] = lambda: free_user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/reports/report-1",
            headers={"Authorization": "Bearer token"},
        )
        assert response.status_code == 200
        data = response.json()
        analysis = data["analysis"]
        assert len(analysis["locationRankings"]) == 3

    def test_free_user_gets_no_premium_sections(self, client):
        free_user = _make_user(plan="free")
        db = FakeSupabase({"reports": [self._FULL_REPORT]})
        client.app.dependency_overrides[get_current_user] = lambda: free_user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/reports/report-1",
            headers={"Authorization": "Bearer token"},
        )
        analysis = response.json()["analysis"]
        assert "financialAnalysis" not in analysis
        assert "crewInsights" not in analysis
        assert "comparables" not in analysis
        assert "weatherLogistics" not in analysis
        assert "fundingOpportunities" not in analysis
        assert "investorSummary" not in analysis

    def test_free_user_gets_no_pdf_url(self, client):
        free_user = _make_user(plan="free")
        db = FakeSupabase({"reports": [self._FULL_REPORT]})
        client.app.dependency_overrides[get_current_user] = lambda: free_user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/reports/report-1",
            headers={"Authorization": "Bearer token"},
        )
        assert response.json()["pdfUrl"] is None

    def test_free_user_still_gets_non_premium_sections(self, client):
        free_user = _make_user(plan="free")
        db = FakeSupabase({"reports": [self._FULL_REPORT]})
        client.app.dependency_overrides[get_current_user] = lambda: free_user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/reports/report-1",
            headers={"Authorization": "Bearer token"},
        )
        analysis = response.json()["analysis"]
        # scriptSummary is NOT a premium section — should be preserved
        assert "scriptSummary" in analysis

    def test_professional_user_gets_full_data(self, client):
        pro_user = _make_user(plan="professional")
        db = FakeSupabase({"reports": [self._FULL_REPORT]})
        client.app.dependency_overrides[get_current_user] = lambda: pro_user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/reports/report-1",
            headers={"Authorization": "Bearer token"},
        )
        data = response.json()
        analysis = data["analysis"]
        assert len(analysis["locationRankings"]) == 6
        assert "financialAnalysis" in analysis
        assert "crewInsights" in analysis
        assert "comparables" in analysis
        assert data["pdfUrl"] is not None or data["pdfUrl"] == ""  # not None for pro

    def test_response_includes_user_plan(self, client):
        pro_user = _make_user(plan="professional")
        db = FakeSupabase({"reports": [self._FULL_REPORT]})
        client.app.dependency_overrides[get_current_user] = lambda: pro_user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/reports/report-1",
            headers={"Authorization": "Bearer token"},
        )
        assert response.json()["userPlan"] == "professional"


# ---------------------------------------------------------------------------
# Subscription Status Endpoint Tests
# ---------------------------------------------------------------------------

class TestSubscriptionStatus:
    def test_returns_plan_and_generation_status(self, client):
        user = _make_user(plan="professional")
        db = FakeSupabase({
            "subscriptions": [{
                "id": "sub-1",
                "user_id": "user-1",
                "status": "active",
                "plan_type": "professional",
                "report_limit": 3,
                "current_period_start": "2026-04-01",
                "current_period_end": "2026-05-01",
            }],
            "reports": [],
        })
        client.app.dependency_overrides[get_current_user] = lambda: user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/subscriptions/status",
            headers={"Authorization": "Bearer token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["plan"] == "professional"
        assert data["can_generate"] is True

    def test_free_user_with_no_reports_can_generate(self, client):
        user = _make_user(plan="free")
        db = FakeSupabase({"subscriptions": [], "reports": []})
        client.app.dependency_overrides[get_current_user] = lambda: user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/subscriptions/status",
            headers={"Authorization": "Bearer token"},
        )
        data = response.json()
        assert data["plan"] == "free"
        assert data["can_generate"] is True

    def test_free_user_with_used_report_cannot_generate(self, client):
        user = _make_user(plan="free")
        db = FakeSupabase({
            "subscriptions": [],
            "reports": [{"id": "r-1", "user_id": "user-1", "report_type": "free"}],
        })
        client.app.dependency_overrides[get_current_user] = lambda: user
        client.app.dependency_overrides[get_supabase] = lambda: db

        response = client.get(
            "/api/subscriptions/status",
            headers={"Authorization": "Bearer token"},
        )
        data = response.json()
        assert data["can_generate"] is False


# ---------------------------------------------------------------------------
# Subscription Checkout with plan_type Tests
# ---------------------------------------------------------------------------

class TestSubscriptionCheckoutPlanType:
    def test_subscription_checkout_passes_plan_type(self, client):
        user = _make_user(plan="free")
        db = FakeSupabase({})
        calls = []

        class TrackingStripeService:
            @staticmethod
            def create_subscription_checkout(price_id, user_email, user_id, metadata=None):
                calls.append({"price_id": price_id, "metadata": metadata})
                return {"session_id": "cs_sub", "url": "https://checkout.stripe.test/sub"}

        client.app.dependency_overrides[get_current_user] = lambda: user
        client.app.dependency_overrides[get_supabase] = lambda: db
        client.app.dependency_overrides[get_stripe_service] = lambda: TrackingStripeService()

        response = client.post(
            "/api/payments/subscription-checkout",
            headers={"Authorization": "Bearer token"},
            json={"price_id": "price_xxx", "plan_type": "professional"},
        )
        assert response.status_code == 200
        assert len(calls) == 1
        assert calls[0]["metadata"] == {"planType": "professional"}

    def test_subscription_checkout_defaults_to_professional(self, client):
        user = _make_user(plan="free")
        db = FakeSupabase({})
        calls = []

        class TrackingStripeService:
            @staticmethod
            def create_subscription_checkout(price_id, user_email, user_id, metadata=None):
                calls.append({"metadata": metadata})
                return {"session_id": "cs_sub", "url": "https://checkout.stripe.test/sub"}

        client.app.dependency_overrides[get_current_user] = lambda: user
        client.app.dependency_overrides[get_supabase] = lambda: db
        client.app.dependency_overrides[get_stripe_service] = lambda: TrackingStripeService()

        response = client.post(
            "/api/payments/subscription-checkout",
            headers={"Authorization": "Bearer token"},
            json={"price_id": "price_xxx"},
        )
        assert response.status_code == 200
        assert calls[0]["metadata"] == {"planType": "professional"}
