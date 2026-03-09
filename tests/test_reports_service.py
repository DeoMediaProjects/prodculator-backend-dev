from datetime import date, timedelta

from sqlalchemy.exc import NoSuchTableError

from app.modules.reports.service import ReportService


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
            "crew_costs": [{"id": "c1", "territory": "UK"}],
            "grant_opportunities": [{"id": "g1", "status": "open"}],
            "film_festivals": [{"id": "f1", "status": "open"}],
        }

    def table(self, table_name: str):
        if table_name == "comparable_productions":
            raise NoSuchTableError(table_name)
        return FakeQuery(table_name, self.tables.get(table_name, []))


def test_load_analysis_datasets_tolerates_missing_optional_table():
    service = ReportService(FakeSupabase())

    datasets = service._load_analysis_datasets()

    assert datasets["comparables"] == []
    assert len(datasets["incentives"]) == 1
    assert len(datasets["crew_costs"]) == 1
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

    service = ReportService(FestivalSupabase())
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
    service = ReportService(supabase)

    service.fail_report(
        "report-1",
        "script analysis failed",
        error_context={"step": "script_analysis", "scriptAnalysisMeta": {"mode": "single_pass_fallback"}},
    )

    assert supabase.query.updated_payload["status"] == "failed"
    assert supabase.query.updated_payload["report_data"]["error"] == "script analysis failed"
    assert supabase.query.updated_payload["report_data"]["errorContext"]["step"] == "script_analysis"


def test_fail_report_hardens_context_schema_and_redacts_sensitive_values():
    supabase = CaptureSupabase()
    service = ReportService(supabase)

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
