from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy.exc import NoSuchTableError

from app.core.database_client import DatabaseClient
from app.modules.reports.service import ReportService
from app.modules.scripts.schemas import ScriptAnalysisResult


def _as_db(fake: Any) -> DatabaseClient:
    """Cast a test double to DatabaseClient to satisfy the type checker."""
    return cast(DatabaseClient, fake)


class FakeResult:
    def __init__(self, data=None):
        self.data = data


class FakeQuery:
    def __init__(self, table_name: str, rows):
        self.table_name = table_name
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def in_(self, *_args, **_kwargs):
        if self.table_name == "film_festivals":
            raise KeyError("status")
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        return FakeResult(self.rows)


class FakeSupabase:
    def __init__(self):
        self.tables = {
            "incentive_programs": [{"id": "i1", "territory": "UK", "status": "active"}],
            "grant_opportunities": [{"id": "g1", "status": "open"}],
            "film_festivals": [{"id": "f1", "status": "open"}],
        }

    def table(self, table_name: str):
        if table_name == "comparable_productions":
            raise NoSuchTableError(table_name)
        return FakeQuery(table_name, self.tables.get(table_name, []))


def test_load_analysis_datasets_tolerates_missing_optional_table():
    service = ReportService(_as_db(FakeSupabase()))

    datasets = service._load_analysis_datasets()

    assert datasets["comparables"] == []
    assert len(datasets["incentives"]) == 1
    assert len(datasets["grants"]) == 1
    assert len(datasets["festivals"]) == 1


def test_load_analysis_datasets_handles_festivals_without_status_column():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    class FestivalSupabase(FakeSupabase):
        def __init__(self):
            super().__init__()
            self.tables["film_festivals"] = [
                {
                    "id": "f-upcoming",
                    "name": "Open Festival",
                    "submission_deadline": tomorrow,
                },
                {
                    "id": "f-closed",
                    "name": "Closed Festival",
                    "submission_deadline": yesterday,
                },
            ]

    service = ReportService(_as_db(FestivalSupabase()))
    datasets = service._load_analysis_datasets()

    assert [f["id"] for f in datasets["festivals"]] == ["f-upcoming"]


class CaptureUpdateQuery:
    def __init__(self):
        self.updated_payload = None

    def update(self, payload):
        self.updated_payload = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        return FakeResult({"ok": True})


class CaptureSupabase:
    def __init__(self):
        self.query = CaptureUpdateQuery()

    def table(self, _table_name: str):
        return self.query


def test_fail_report_persists_error_context():
    supabase = CaptureSupabase()
    service = ReportService(_as_db(supabase))

    service.fail_report(
        "report-1",
        "script analysis failed",
        error_context={"step": "script_analysis", "scriptAnalysisMeta": {"mode": "single_pass_fallback"}},
    )

    assert supabase.query.updated_payload is not None
    assert supabase.query.updated_payload["status"] == "failed"
    assert supabase.query.updated_payload["report_data"]["error"] == "script analysis failed"
    assert supabase.query.updated_payload["report_data"]["errorContext"]["step"] == "script_analysis"


def test_fail_report_hardens_context_schema_and_redacts_sensitive_values():
    supabase = CaptureSupabase()
    service = ReportService(_as_db(supabase))

    service.fail_report(
        "report-2",
        "bad token sk-ant-supersecretvalue-123456789",
        error_context={
            "step": "not_a_real_step",
            "unexpected": "drop-me",
            "scriptAnalysisMeta": {
                "mode": "chunked",
                "fallbackUsed": True,
                "chunkedFailed": True,
                "fallbackToSinglePass": False,
                "reason": "Authorization=Bearer abcdefghijklmnopq",
                "chunkedError": "api_key=sk-ant-anothersecret-987654321",
                "chunkTelemetry": {
                    "totalChunks": "9999",
                    "generatedChunks": "700",
                    "usedChunks": 5,
                    "failedChunks": -1,
                    "droppedChunks": "12",
                    "successRatio": 2.7,
                    "stopReasons": {"max_tokens": "3", "rogue": 99},
                    "extra": "drop",
                },
                "overallConfidence": "0.77",
                "sectionConfidence": {
                    "budget": 0.9,
                    "metadata": "0.4",
                    "rogue": 100,
                },
                "rogueField": "remove",
            },
        },
    )

    payload = supabase.query.updated_payload
    assert payload is not None
    report_data = payload["report_data"]
    assert payload["status"] == "failed"
    assert "[REDACTED]" in report_data["error"]
    assert "sk-ant" not in report_data["error"]

    context = report_data["errorContext"]
    assert context["step"] == "unknown"
    assert "unexpected" not in context

    meta = context["scriptAnalysisMeta"]
    assert "rogueField" not in meta
    assert "[REDACTED]" in meta["reason"]
    assert "[REDACTED]" in meta["chunkedError"]
    assert "Bearer" not in meta["reason"]
    assert "sk-ant" not in meta["chunkedError"]
    assert meta["chunkTelemetry"]["totalChunks"] == 500
    assert meta["chunkTelemetry"]["generatedChunks"] == 500
    assert meta["chunkTelemetry"]["failedChunks"] == 0
    assert meta["chunkTelemetry"]["droppedChunks"] == 12
    assert meta["chunkTelemetry"]["successRatio"] == 1.0
    assert meta["chunkTelemetry"]["stopReasons"]["max_tokens"] == 3
    assert "rogue" not in meta["chunkTelemetry"]["stopReasons"]
    assert set(meta["sectionConfidence"].keys()) == {"budget", "metadata"}


def test_redact_sensitive_text_helper_masks_tokens():
    raw = "Authorization=Bearer abcdefghijklmnop sk-ant-secretvalue-123456789"
    redacted = ReportService.redact_sensitive_text(raw)
    assert "[REDACTED]" in redacted
    assert "Bearer abcdefghijklmnop" not in redacted
    assert "sk-ant-secretvalue-123456789" not in redacted


# ── _compute_shoot_months ──────────────────────────────────────────────────────

def _make_service():
    return ReportService(_as_db(FakeSupabase()))


def test_compute_shoot_months_basic():
    """Feb start + 6 weeks → months [2, 3]."""
    service = _make_service()
    months = service._compute_shoot_months("2026-02-01", 6)
    assert months == [2, 3]


def test_compute_shoot_months_year_wrap():
    """Nov start + 12 weeks → months spanning Nov, Dec, Jan."""
    service = _make_service()
    months = service._compute_shoot_months("2026-11-01", 12)
    assert months is not None
    assert 11 in months
    assert 12 in months
    assert 1 in months
    assert months == sorted(months)


def test_compute_shoot_months_no_input():
    """Missing start date → None."""
    service = _make_service()
    assert service._compute_shoot_months(None, 4) is None
    assert service._compute_shoot_months("", 4) is None


def test_compute_shoot_months_invalid_date():
    """Unparseable date → None."""
    service = _make_service()
    assert service._compute_shoot_months("not-a-date", 4) is None


def test_compute_shoot_months_default_duration():
    """No duration given → defaults to 4 weeks."""
    service = _make_service()
    months = service._compute_shoot_months("2026-07-01", None)
    assert months is not None
    assert 7 in months


# ── _classify_season ──────────────────────────────────────────────────────────

def test_classify_season_summer():
    assert ReportService._classify_season([6, 7, 8]) == "summer"
    assert ReportService._classify_season([5, 6]) == "summer"


def test_classify_season_winter():
    assert ReportService._classify_season([12, 1, 2]) == "winter"
    assert ReportService._classify_season([11, 12]) == "winter"


def test_classify_season_mixed():
    assert ReportService._classify_season([3, 4, 5, 6, 7]) == "mixed"
    assert ReportService._classify_season([10, 11, 12, 1]) == "mixed"


# ── _inject_derived_data ──────────────────────────────────────────────────────

def test_inject_derived_data_shoot_window():
    """Shoot window is populated when start date + duration are in metadata."""
    service = _make_service()
    datasets: dict = {}
    service._inject_derived_data(
        datasets,
        script_analysis=None,
        request_metadata={"filming_start_date": "2026-06-01", "filming_duration": 8},
    )
    assert datasets["_shoot_months"] is not None
    assert 6 in datasets["_shoot_months"]
    assert datasets["_shoot_window"] is not None
    assert datasets["_shoot_window"]["season"] == "summer"


def test_inject_derived_data_no_dates():
    """Without dates, shoot_months and shoot_window are None."""
    service = _make_service()
    datasets: dict = {}
    service._inject_derived_data(datasets, script_analysis=None, request_metadata={})
    assert datasets["_shoot_months"] is None
    assert datasets["_shoot_window"] is None


def test_inject_derived_data_producer_country():
    service = _make_service()
    datasets: dict = {}
    service._inject_derived_data(
        datasets,
        script_analysis=None,
        request_metadata={"producer_country": "ZA", "co_production_status": "sole_producer"},
    )
    assert datasets["_producer_country"] == "ZA"
    assert datasets["_co_production_status"] == "sole_producer"


def _sample_script_analysis() -> ScriptAnalysisResult:
    return ScriptAnalysisResult.model_validate(
        {
            "locations": [
                {
                    "name": "Lagos",
                    "country": "Nigeria",
                    "territory": "Nigeria",
                    "frequency": 5,
                    "isMainLocation": True,
                }
            ],
            "budgetEstimate": {
                "range": "medium",
                "minUSD": 5000000,
                "maxUSD": 30000000,
                "confidence": 0.8,
                "indicators": ["large cast"],
            },
            "productionScale": {
                "crewSize": "large",
                "principalCast": "medium",
                "supportingCast": "large",
                "backgroundExtras": "extra_large",
                "estimatedShootingDays": 45,
            },
            "equipment": {
                "cameraEquipment": "arri",
                "specialEquipment": [],
                "vfxRequirements": "moderate",
            },
            "metadata": {
                "genres": ["Drama", "Thriller"],
                "format": "feature",
                "tone": "Dark",
                "targetAudience": "Adults",
            },
            "challenges": {
                "weatherDependent": False,
                "historicalPeriod": False,
                "specialPermits": False,
                "stunts": False,
                "animalWrangling": False,
                "waterWork": False,
                "nightShooting": False,
                "notes": [],
            },
        }
    )


class CaptureUpsertQuery:
    """Captures the v2 signal write chain: a select-for-existing lookup followed
    by insert (no prior row) or update (dedupe path)."""

    def __init__(self):
        self.upsert_payload: dict | None = None
        self.deleted: bool = False

    def insert(self, payload):
        self.upsert_payload = payload
        return self

    def update(self, payload):
        self.upsert_payload = payload
        return self

    def delete(self):
        self.deleted = True
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        # First call (existing-row lookup) sees upsert_payload=None -> no prior
        # row; the write call then returns the captured payload.
        return FakeResult(self.upsert_payload)


class CaptureUpsertSupabase:
    def __init__(self):
        self.query = CaptureUpsertQuery()

    def table(self, table_name: str):
        assert table_name == "production_signals"
        return self.query


def test_upsert_production_signal_prefers_request_metadata_values():
    supabase = CaptureUpsertSupabase()
    service = ReportService(_as_db(supabase))
    analysis = _sample_script_analysis()

    result = service.upsert_production_signal(
        report_id="report-123",
        report_row={"id": "report-123", "created_at": "2026-04-01T12:30:00+00:00"},
        request_metadata={
            "country": "United Kingdom",
            "state_province": "England",
            "camera_equipment": ["sony", "arri"],
            "crew_size": "140",
            "principal_cast": "10",
            "supporting_cast": 35,
            "genre": ["Action", "Thriller"],
            "format": "Feature Film",
            "b2b_consent": True,  # v2: un-consented signals are never persisted
        },
        script_analysis=analysis,
    )

    payload = supabase.query.upsert_payload
    assert result is not None
    assert payload is not None
    assert payload["id"] == "report-123"
    assert payload["script_id"] == "report-123"
    # v2: territory mirrors home_country (legacy compatibility)
    assert payload["home_country"] == "United Kingdom"
    assert payload["territory"] == "United Kingdom"
    assert payload["state"] == "England"
    assert payload["submission_date"] == "2026-04-01"
    assert payload["camera_equipment"] == ["sony", "arri"]
    assert payload["crew_size"] == 140
    assert payload["principal_cast"] == 10
    assert payload["supporting_cast"] == 35
    assert payload["background_extras"] == 500
    assert payload["budget_range"] == "medium"
    # v2: format and genres are canonicalised on write
    assert payload["format"] == "feature"
    assert payload["genres"] == ["action", "thriller"]
    assert payload["b2b_consent"] is True
    assert payload["schema_version"] == 2


def test_upsert_production_signal_falls_back_to_analysis_values():
    supabase = CaptureUpsertSupabase()
    service = ReportService(_as_db(supabase))
    analysis = _sample_script_analysis()

    result = service.upsert_production_signal(
        report_id="report-456",
        report_row={"id": "report-456", "created_at": "2026-02-10T09:00:00Z"},
        request_metadata={"country": "United Kingdom", "b2b_consent": True},
        script_analysis=analysis,
    )

    payload = supabase.query.upsert_payload
    assert result is not None
    assert payload is not None
    assert payload["submission_date"] == "2026-02-10"
    assert payload["camera_equipment"] == ["arri"]
    assert payload["crew_size"] == 120
    assert payload["principal_cast"] == 6
    assert payload["supporting_cast"] == 35
    assert payload["background_extras"] == 500
    assert payload["format"] == "feature"
    # v2: genres are canonicalised (lowercased) on write
    assert payload["genres"] == ["drama", "thriller"]


def test_upsert_production_signal_without_script_analysis_uses_report_and_metadata_fallbacks():
    supabase = CaptureUpsertSupabase()
    service = ReportService(_as_db(supabase))

    result = service.upsert_production_signal(
        report_id="report-789",
        report_row={
            "id": "report-789",
            "created_at": "2026-03-20T08:00:00Z",
            "report_data": {
                "productionDetails": {
                    "format": "Feature Film",
                    "genres": ["Mystery"],
                    "crewSize": "medium",
                    "castSize": "large",
                }
            },
        },
        request_metadata={
            "country": "United Kingdom",
            "budget_amount": 2_000_000,
            "b2b_consent": True,
        },
        script_analysis=None,
    )

    payload = supabase.query.upsert_payload
    assert result is not None
    assert payload is not None
    assert payload["submission_date"] == "2026-03-20"
    assert payload["territory"] == "United Kingdom"
    assert payload["budget_range"] == "low"
    # v2: GBP amount stored alongside the band (currency defaults to GBP)
    assert payload["budget_amount_gbp"] == 2_000_000
    # v2: format and genres are canonicalised on write
    assert payload["format"] == "feature"
    assert payload["genres"] == ["mystery"]
    assert payload["crew_size"] == 60
    assert payload["principal_cast"] == 12


# --- intake contract v2 fields (intake_schema.json) ---


def test_signal_payload_co_production_interest_string_mapping():
    """yes/no/undecided from intake must map to True/False/None — bool("no") is
    True, which is exactly the bug this guards against."""
    service = ReportService(_as_db(CaptureUpsertSupabase()))
    for raw, expected in (("yes", True), ("no", False), ("undecided", None), (None, None)):
        payload = service._build_production_signal_payload(
            report_id="r1",
            report_row={"id": "r1", "created_at": "2026-05-01T00:00:00Z"},
            request_metadata={"country": "United Kingdom", "co_production_interest": raw},
            script_analysis=None,
        )
        assert payload["co_production_interest"] is expected, f"{raw!r} -> {expected!r}"


def test_create_report_request_accepts_intake_contract_fields():
    from app.modules.reports.schemas import CreateReportRequest

    req = CreateReportRequest(
        script_title="Test",
        genre=["Drama"],
        budget_amount=1_000_000,
        format="TV Pilot",
        country="United Kingdom",
        location_strategy="open",
        completion_date="2027-03-01",
        must_film_in="Scotland",
        co_production_interest="undecided",
        primary_languages=["English", "Yoruba"],
        target_audience=["adults_25_plus"],
        audience_segments=["lgbtq_audience"],
        audience_skew="female_leaning",
    )
    assert req.format == "TV Pilot"
    assert req.completion_date == "2027-03-01"
    assert req.must_film_in == "Scotland"
    assert req.co_production_interest == "undecided"
    assert req.primary_languages == ["English", "Yoruba"]
