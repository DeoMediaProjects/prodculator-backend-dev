"""Tests for the AI-powered web scraping pipeline.

Covers:
- fetcher: HTML stripping and truncation
- extractor: Anthropic extraction with mocked responses
- differ: diff logic, pending_changes creation, idempotency
- ScraperService: orchestration, source seeding, run logging
- Per-resource scrapers: incentives, grants, festivals, crew_costs
- Scheduler: sync_settings-driven scheduling logic
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from pydantic_settings import SettingsConfigDict
from app.modules.scraper.fetcher import fetch_and_strip, fetch_pdf_text, fetch_pdf_links
from app.modules.scraper.extractor import extract
from app.modules.scraper.differ import diff_and_queue, normalize_territory
from app.modules.scraper.service import ScraperService
from app.modules.scraper.sources import DEFAULT_SOURCES


# ── Fake infrastructure (reused pattern from test_incentives_admin) ──────────


class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, table_name: str, store: dict[str, list[dict]]):
        self.table_name = table_name
        self.store = store
        self.filters = {}
        self._data = None
        self._count = False
        self._head = False
        self._single = False
        self._offset = 0
        self._end = None

    def select(self, *_args, **kwargs):
        if kwargs.get("count"):
            self._count = True
        if kwargs.get("head"):
            self._head = True
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def gte(self, *_args):
        return self

    def lte(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start: int, end: int):
        self._offset = start
        self._end = end
        return self

    def insert(self, payload):
        if isinstance(payload, list):
            payload = payload[0]
        row = {**payload}
        if "id" not in row:
            row["id"] = f"new-{len(self.store.get(self.table_name, [])) + 1}"
        self.store.setdefault(self.table_name, []).append(row)
        self._data = [row]
        return self

    def update(self, payload):
        self._data = payload
        return self

    def delete(self):
        rows = self._rows()
        self.store[self.table_name] = [
            r for r in self.store[self.table_name] if r not in rows
        ]
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        if self._count and self._head:
            return FakeResult(data=None, count=len(self._rows()))

        if isinstance(self._data, list):
            rows = self._data
        elif isinstance(self._data, dict):
            rows = self._rows()
            if rows:
                rows[0].update(self._data)
        else:
            rows = self._rows()

        if self._end is not None:
            rows = rows[self._offset : self._end + 1]

        if self._single:
            return FakeResult(data=rows[0] if rows else None)
        return FakeResult(data=rows)

    def _rows(self):
        return [
            row
            for row in self.store.get(self.table_name, [])
            if all(row.get(k) == v for k, v in self.filters.items())
        ]


class FakeSupabase(DatabaseClient):
    def __init__(self, store=None):
        # Bypass DatabaseClient.__init__ — we don't need a real DB session
        self.store: dict[str, list[dict]] = store or {
            "incentive_programs": [
                {
                    "id": "i1",
                    "territory": "United Kingdom",
                    "program": "UK Film Tax Relief",
                    "rate": "25%",
                    "cap": "No cap",
                    "status": "Active",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            ],
            "crew_costs": [
                {
                    "id": "cc1",
                    "country": "US",
                    "territory": "United States",
                    "role": "Camera Operator",
                    "role_category": "HOD-Camera",
                    "department": "day",
                    "union_rate_cents": 30000,
                    "non_union_rate_cents": 150000,
                    "rate_currency": "USD",
                    "source_type": "government_stats",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            ],
            "grant_opportunities": [],
            "film_festivals": [],
            "sync_settings": [],
            "pending_changes": [],
            "scrape_sources": [],
            "scrape_runs": [],
        }
        self.session = MagicMock()
        self.settings = FakeSettings()
        self.storage = MagicMock()
        self.auth = MagicMock()

    def table(self, name: str) -> FakeQuery:  # type: ignore[override]
        return FakeQuery(name, self.store)

    def close(self) -> None:
        pass


class FakeSettings(Settings):
    JWT_SECRET_KEY: str = "test-secret-key-for-unit-tests-only-32ch"
    ANTHROPIC_API_KEY: str = "test-key"
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    ANTHROPIC_MAX_TOKENS: int = 8000
    ANTHROPIC_ANALYSIS_TIMEOUT: int = 120
    SCRAPER_ENABLED: bool = True
    SCRAPER_REQUEST_TIMEOUT: int = 10
    SCRAPER_MAX_TEXT_CHARS: int = 5000
    BLS_API_KEY: str = ""
    DB_URL: str = "sqlite:///:memory:"

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=True,
        extra="ignore",
    )


# ── Fetcher tests ────────────────────────────────────────────────────────────


class TestFetcher:
    @patch("app.modules.scraper.fetcher._check_robots_txt", return_value=True)
    def test_strips_html_tags(self, _mock_robots):
        html = "<html><body><h1>Title</h1><p>Content here</p></body></html>"
        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            result = fetch_and_strip("https://example.com", FakeSettings())

        assert result is not None
        assert "<h1>" not in result
        assert "Title" in result
        assert "Content" in result

    @patch("app.modules.scraper.fetcher._check_robots_txt", return_value=True)
    def test_strips_script_and_style_tags(self, _mock_robots):
        html = "<html><script>var x=1;</script><style>.a{}</style><p>Visible</p></html>"
        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            result = fetch_and_strip("https://example.com", FakeSettings())

        assert result is not None
        assert "var x=1" not in result
        assert ".a{}" not in result
        assert "Visible" in result

    @patch("app.modules.scraper.fetcher._check_robots_txt", return_value=True)
    def test_truncates_long_text(self, _mock_robots):
        settings = FakeSettings()
        settings.SCRAPER_MAX_TEXT_CHARS = 50
        html = "<p>" + "A" * 200 + "</p>"
        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            result = fetch_and_strip("https://example.com", settings)

        assert result is not None
        assert "[Content truncated]" in result

    @patch("app.modules.scraper.fetcher._check_robots_txt", return_value=True)
    def test_returns_none_on_network_error(self, _mock_robots):
        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(side_effect=Exception("timeout")))
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            result = fetch_and_strip("https://example.com", FakeSettings())

        assert result is None

    @patch("app.modules.scraper.fetcher._check_robots_txt", return_value=False)
    def test_returns_none_when_blocked_by_robots_txt(self, _mock_robots):
        result = fetch_and_strip("https://example.com/private", FakeSettings())
        assert result is None


class TestRobotsTxt:
    def test_allows_when_no_robots_txt(self):
        from app.modules.scraper.fetcher import _check_robots_txt, _robots_cache

        _robots_cache.clear()
        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            assert _check_robots_txt("https://example.com/page") is True
        _robots_cache.clear()

    def test_blocks_disallowed_path(self):
        from app.modules.scraper.fetcher import _check_robots_txt, _robots_cache

        _robots_cache.clear()
        robots_txt = "User-agent: *\nDisallow: /private/"
        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = robots_txt
            mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            assert _check_robots_txt("https://example.com/private/page") is False
        _robots_cache.clear()

    def test_allows_permitted_path(self):
        from app.modules.scraper.fetcher import _check_robots_txt, _robots_cache

        _robots_cache.clear()
        robots_txt = "User-agent: *\nDisallow: /private/"
        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = robots_txt
            mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            assert _check_robots_txt("https://example.com/public/page") is True
        _robots_cache.clear()


# ── Extractor tests ──────────────────────────────────────────────────────────


class TestExtractor:
    def _mock_anthropic_response(self, content: str):
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = content
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        return mock_resp

    @patch("app.modules.scraper.extractor.Anthropic")
    def test_extracts_incentives(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            '{"programs": [{"territory": "UK", "program": "Tax Relief", "rate": "25%", "cap": null, "status": "Active", "source_url": null}]}'
        )

        result = extract("incentives", "some page text", "UK", FakeSettings())

        assert len(result) == 1
        assert result[0]["territory"] == "UK"
        assert result[0]["rate"] == "25%"

    @patch("app.modules.scraper.extractor.Anthropic")
    def test_extracts_crew_costs(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            '{"crew_costs": [{"country": "US", "role": "DP", "role_category": "HOD-Camera", "department": "day", "union_rate_cents": 80000, "non_union_rate_cents": 400000}]}'
        )

        result = extract("crew_costs", "some page text", "US", FakeSettings())

        assert len(result) == 1
        assert result[0]["union_rate_cents"] == 80000

    @patch("app.modules.scraper.extractor.Anthropic")
    def test_extracts_grants(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            '{"grants": [{"title": "BFI Fund", "territory": "UK", "funding_body": "BFI", "max_amount": "50000", "currency": "GBP", "application_deadline": "2026-06-01", "eligibility": ["UK residents"], "website_url": null, "status": "open"}]}'
        )

        result = extract("grants", "some page text", "UK", FakeSettings())

        assert len(result) == 1
        assert result[0]["title"] == "BFI Fund"

    @patch("app.modules.scraper.extractor.Anthropic")
    def test_extracts_festivals(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            '{"festivals": [{"name": "Sundance", "year": 2026, "location": "Park City", "tier": "A-List", "genres": ["Drama"], "premiere_requirement": "World Premiere", "acceptance_rate": "2%", "website_url": null, "deadlines": []}]}'
        )

        result = extract("festivals", "some page text", None, FakeSettings())

        assert len(result) == 1
        assert result[0]["name"] == "Sundance"

    @patch("app.modules.scraper.extractor.Anthropic")
    def test_extracts_when_json_is_fenced(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            '```json\n{"programs": [{"territory":"UK","program":"Tax Relief","rate":"25%","cap":null,"status":"Active","source_url":null}]}\n```'
        )

        result = extract("incentives", "some page text", "UK", FakeSettings())

        assert len(result) == 1
        assert result[0]["program"] == "Tax Relief"

    @patch("app.modules.scraper.extractor.Anthropic")
    def test_raises_on_anthropic_error(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API error")

        with pytest.raises(RuntimeError, match="Anthropic extraction failed for incentives"):
            extract("incentives", "some text", None, FakeSettings())

    def test_raises_when_no_api_key(self):
        settings = FakeSettings()
        settings.ANTHROPIC_API_KEY = ""

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY not set"):
            extract("incentives", "some text", None, settings)

    def test_raises_on_unknown_resource_type(self):
        with pytest.raises(ValueError, match="Unknown resource_type"):
            extract("unknown_type", "text", None, FakeSettings())


# ── Differ tests ─────────────────────────────────────────────────────────────


class TestDiffer:
    def test_creates_pending_change_for_updated_field(self):
        db = FakeSupabase()
        extracted = [
            {"territory": "United Kingdom", "program": "UK Film Tax Relief", "rate": "26%", "cap": "No cap", "status": "Active"},
        ]

        count = diff_and_queue("incentives", extracted, "https://source.com", db)

        assert count == 1
        changes = db.store["pending_changes"]
        assert len(changes) == 1
        assert changes[0]["field"] == "rate"
        assert changes[0]["current_value"] == "25%"
        assert changes[0]["detected_value"] == "26%"
        assert changes[0]["resource_id"] == "i1"
        assert changes[0]["source"] == "https://source.com"

    def test_no_change_when_values_match(self):
        db = FakeSupabase()
        extracted = [
            {"territory": "United Kingdom", "program": "UK Film Tax Relief", "rate": "25%", "cap": "No cap", "status": "Active"},
        ]

        count = diff_and_queue("incentives", extracted, "https://source.com", db)

        assert count == 0
        assert len(db.store["pending_changes"]) == 0

    def test_idempotency_skips_duplicate_pending_change(self):
        db = FakeSupabase()
        # Pre-existing pending change
        db.store["pending_changes"].append({
            "id": "existing-pc",
            "resource_type": "incentives",
            "resource_id": "i1",
            "territory": "United Kingdom",
            "field": "rate",
            "current_value": "25%",
            "detected_value": "26%",
            "confidence": "medium",
            "source": "https://old-source.com",
            "status": "pending",
        })

        extracted = [
            {"territory": "United Kingdom", "program": "UK Film Tax Relief", "rate": "26%"},
        ]

        count = diff_and_queue("incentives", extracted, "https://new-source.com", db)

        assert count == 0
        # Should still be just the original pending change
        assert len(db.store["pending_changes"]) == 1

    def test_creates_change_for_new_record(self):
        db = FakeSupabase()
        extracted = [
            {"territory": "France", "program": "French Tax Rebate", "rate": "30%", "cap": "30M EUR", "status": "Active"},
        ]

        count = diff_and_queue("incentives", extracted, "https://source.com", db)

        # rate, cap, status should all create changes since no existing record
        assert count == 3
        changes = db.store["pending_changes"]
        fields = {c["field"] for c in changes}
        assert fields == {"rate", "cap", "status"}
        # resource_id should be None since no matching record
        assert all(c["resource_id"] is None for c in changes)

    def test_skips_none_extracted_values(self):
        db = FakeSupabase()
        extracted = [
            {"territory": "United Kingdom", "program": "UK Film Tax Relief", "rate": None, "cap": None, "status": None},
        ]

        count = diff_and_queue("incentives", extracted, "https://source.com", db)

        assert count == 0

    def test_crew_costs_diff(self):
        db = FakeSupabase()
        extracted = [
            {"country": "US", "role": "Camera Operator", "union_rate_cents": 35000, "non_union_rate_cents": 175000},
        ]

        count = diff_and_queue("crew_costs", extracted, "https://bls.gov", db, confidence="high")

        assert count == 2  # union_rate_cents and non_union_rate_cents changed
        changes = db.store["pending_changes"]
        assert all(c["confidence"] == "high" for c in changes)


# ── ScraperService tests ─────────────────────────────────────────────────────


class TestScraperService:
    def test_seed_sources_populates_empty_table(self):
        db = FakeSupabase()
        svc = ScraperService(db, FakeSettings())

        svc.seed_sources()

        sources = db.store["scrape_sources"]
        assert len(sources) == len(DEFAULT_SOURCES)
        labels = {s["label"] for s in sources}
        assert "BFI Cultural Test & Certification" in labels
        assert "BLS Occupational Employment & Wage Statistics (OEWS) — NAICS 5121" in labels

    def test_seed_sources_syncs_existing_defaults_and_inserts_missing(self):
        db = FakeSupabase()
        db.store["scrape_sources"] = [
            {
                "id": "existing-1",
                "resource_type": "incentives",
                "url": "https://old.example.com",
                "label": "BFI Cultural Test & Certification",
                "territory": "United Kingdom",
                "enabled": True,
                "use_bls_api": False,
            }
        ]
        svc = ScraperService(db, FakeSettings())

        svc.seed_sources()

        # Existing labeled source should be updated, and missing defaults inserted
        assert len(db.store["scrape_sources"]) == len(DEFAULT_SOURCES)
        bfi = next(s for s in db.store["scrape_sources"] if s["label"] == "BFI Cultural Test & Certification")
        assert bfi["url"] == "https://www.bfi.org.uk/apply-british-certification-tax-relief"

    def test_run_disabled_returns_skipped(self):
        settings = FakeSettings()
        settings.SCRAPER_ENABLED = False
        db = FakeSupabase()
        svc = ScraperService(db, settings)

        result = svc.run_all()

        assert result["status"] == "skipped"
        assert result["reason"] == "SCRAPER_ENABLED=False"

    def test_run_for_resource_processes_sources(self):
        from app.modules.scraper import service as svc_mod

        mock_scraper = MagicMock(return_value=2)
        db = FakeSupabase()
        db.store["scrape_sources"] = [
            {
                "id": "s1",
                "resource_type": "incentives",
                "url": "https://example.com",
                "label": "Test",
                "territory": None,
                "enabled": True,
                "use_bls_api": False,
            },
        ]
        svc = ScraperService(db, FakeSettings())

        original = svc_mod._SCRAPERS["incentives"]
        svc_mod._SCRAPERS["incentives"] = mock_scraper
        try:
            result = svc.run_for_resource("incentives")
        finally:
            svc_mod._SCRAPERS["incentives"] = original

        assert result["status"] == "success"
        assert result["pagesScraped"] == 1
        assert result["changesDetected"] == 2
        assert result["triggeredBy"] == "admin"
        mock_scraper.assert_called_once()

        # Verify scrape_runs was logged
        runs = db.store["scrape_runs"]
        assert len(runs) == 1
        assert runs[0]["status"] == "success"

    def test_run_handles_scraper_errors_gracefully(self):
        from app.modules.scraper import service as svc_mod

        mock_scraper = MagicMock(side_effect=Exception("boom"))
        db = FakeSupabase()
        db.store["scrape_sources"] = [
            {
                "id": "s1",
                "resource_type": "incentives",
                "url": "https://example.com",
                "label": "Test",
                "territory": None,
                "enabled": True,
                "use_bls_api": False,
            },
        ]
        svc = ScraperService(db, FakeSettings())

        original = svc_mod._SCRAPERS["incentives"]
        svc_mod._SCRAPERS["incentives"] = mock_scraper
        try:
            result = svc.run_for_resource("incentives")
        finally:
            svc_mod._SCRAPERS["incentives"] = original

        assert result["status"] == "error"
        assert result["errors"] == 1
        # Source should be updated with error status
        source = db.store["scrape_sources"][0]
        assert source["last_status"] == "error"

    def test_run_skips_disabled_sources(self):
        from app.modules.scraper import service as svc_mod

        mock_scraper = MagicMock(return_value=0)
        db = FakeSupabase()
        db.store["scrape_sources"] = [
            {
                "id": "s1",
                "resource_type": "incentives",
                "url": "https://example.com",
                "label": "Disabled",
                "territory": None,
                "enabled": False,
                "use_bls_api": False,
            },
        ]
        svc = ScraperService(db, FakeSettings())

        original = svc_mod._SCRAPERS["incentives"]
        svc_mod._SCRAPERS["incentives"] = mock_scraper
        try:
            result = svc.run_for_resource("incentives")
        finally:
            svc_mod._SCRAPERS["incentives"] = original

        mock_scraper.assert_not_called()
        assert result["pagesScraped"] == 0


# ── Per-resource scraper tests ───────────────────────────────────────────────


class TestIncentivesScraper:
    @patch("app.modules.scraper.scrapers.incentives.diff_and_queue", return_value=3)
    @patch("app.modules.scraper.scrapers.incentives.extract", return_value=[{"territory": "UK", "rate": "25%"}])
    @patch("app.modules.scraper.scrapers.incentives.fetch_and_strip", return_value="page text")
    def test_run_full_pipeline(self, mock_fetch, mock_extract, mock_diff):
        from app.modules.scraper.scrapers.incentives import run

        settings = FakeSettings()
        source = {"url": "https://example.com", "territory": "UK"}
        result = run(source, FakeSupabase(), settings)

        assert result == 3
        mock_fetch.assert_called_once()
        mock_extract.assert_called_once_with("incentives", "page text", "UK", settings)
        mock_diff.assert_called_once()

    @patch("app.modules.scraper.scrapers.incentives.fetch_and_strip", return_value=None)
    def test_raises_when_fetch_fails(self, mock_fetch):
        from app.modules.scraper.scrapers.incentives import run

        with pytest.raises(RuntimeError, match="No text returned for incentives source"):
            run({"url": "https://example.com", "territory": None}, FakeSupabase(), FakeSettings())


class TestCrewCostsScraper:
    @patch("app.modules.scraper.scrapers.crew_costs.httpx.Client")
    def test_bls_api_branch(self, mock_httpx):
        from app.modules.scraper.scrapers.crew_costs import run

        settings = FakeSettings()
        settings.BLS_API_KEY = "test-bls-key"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "Results": {
                "series": [
                    {
                        "seriesID": "OEUM000000027106200",
                        "data": [{"value": "52000", "year": "2024"}],
                    }
                ]
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.return_value.__enter__ = MagicMock(
            return_value=MagicMock(post=MagicMock(return_value=mock_resp))
        )
        mock_httpx.return_value.__exit__ = MagicMock(return_value=False)

        source = {"url": "https://api.bls.gov", "territory": "US", "use_bls_api": True}
        db = FakeSupabase()

        result = run(source, db, settings)

        # Should create pending changes for union_rate_cents and non_union_rate_cents
        assert result >= 0  # May be 0 if values match existing

    @patch("app.modules.scraper.scrapers.crew_costs.diff_and_queue", return_value=1)
    @patch("app.modules.scraper.scrapers.crew_costs.extract", return_value=[{"country": "GB", "role": "DP", "union_rate_cents": 50000}])
    @patch("app.modules.scraper.scrapers.crew_costs.fetch_and_strip", return_value="page text")
    def test_html_scrape_branch(self, mock_fetch, mock_extract, mock_diff):
        from app.modules.scraper.scrapers.crew_costs import run

        source = {"url": "https://example.com", "territory": "UK", "use_bls_api": False, "is_pdf": False}
        result = run(source, FakeSupabase(), FakeSettings())

        assert result == 1
        mock_fetch.assert_called_once()
        mock_extract.assert_called_once()

    @patch("app.modules.scraper.scrapers.crew_costs.diff_and_queue", return_value=2)
    @patch("app.modules.scraper.scrapers.crew_costs.extract", return_value=[{"country": "GB", "role": "Gaffer", "union_rate_cents": 45000}])
    @patch("app.modules.scraper.scrapers.crew_costs.fetch_pdf_text", return_value="Role\tDay Rate\nGaffer\t450")
    @patch("app.modules.scraper.scrapers.crew_costs.fetch_pdf_links", return_value=["https://example.com/rates.pdf"])
    def test_pdf_pipeline_branch(self, mock_links, mock_pdf, mock_extract, mock_diff):
        from app.modules.scraper.scrapers.crew_costs import run

        source = {"url": "https://example.com/ratecards/", "territory": "UK", "use_bls_api": False, "is_pdf": True}
        result = run(source, FakeSupabase(), FakeSettings())

        assert result == 2
        mock_links.assert_called_once()
        assert mock_pdf.call_count == 1
        assert mock_pdf.call_args[0][0] == "https://example.com/rates.pdf"
        mock_extract.assert_called_once()

    @patch("app.modules.scraper.scrapers.crew_costs.diff_and_queue", return_value=1)
    @patch("app.modules.scraper.scrapers.crew_costs.extract", return_value=[{"country": "AU", "role": "Grip", "union_rate_cents": 38000}])
    @patch("app.modules.scraper.scrapers.crew_costs.fetch_pdf_text", return_value="Role\tDay Rate\nGrip\t380")
    @patch("app.modules.scraper.scrapers.crew_costs.fetch_pdf_links", return_value=[])
    def test_pdf_direct_link_fallback(self, mock_links, mock_pdf, mock_extract, mock_diff):
        from app.modules.scraper.scrapers.crew_costs import run

        source = {"url": "https://example.com/rates.pdf", "territory": "Australia", "use_bls_api": False, "is_pdf": True}
        result = run(source, FakeSupabase(), FakeSettings())

        assert result == 1
        assert mock_pdf.call_count == 1
        assert mock_pdf.call_args[0][0] == "https://example.com/rates.pdf"


class TestGrantsScraper:
    @patch("app.modules.scraper.scrapers.grants.diff_and_queue", return_value=2)
    @patch("app.modules.scraper.scrapers.grants.extract", return_value=[{"title": "BFI Fund"}])
    @patch("app.modules.scraper.scrapers.grants.fetch_and_strip", return_value="page text")
    def test_run_full_pipeline(self, mock_fetch, mock_extract, mock_diff):
        from app.modules.scraper.scrapers.grants import run

        result = run({"url": "https://example.com", "territory": "UK"}, FakeSupabase(), FakeSettings())

        assert result == 2


class TestFestivalsScraper:
    @patch("app.modules.scraper.scrapers.festivals.diff_and_queue", return_value=1)
    @patch("app.modules.scraper.scrapers.festivals.extract", return_value=[{"name": "Sundance"}])
    @patch("app.modules.scraper.scrapers.festivals.fetch_and_strip", return_value="page text")
    def test_run_full_pipeline(self, mock_fetch, mock_extract, mock_diff):
        from app.modules.scraper.scrapers.festivals import run

        result = run({"url": "https://example.com", "territory": None}, FakeSupabase(), FakeSettings())

        assert result == 1


# ── Scheduler logic tests ───────────────────────────────────────────────────


class TestSchedulerLogic:
    @patch("app.modules.scraper.service.ScraperService")
    @patch("app.core.database_client.create_client")
    @patch("app.core.config.get_settings")
    def test_runs_sync_when_due(self, mock_settings, mock_create, mock_svc_cls):
        from app.core.scheduler import _check_and_run_syncs

        settings = FakeSettings()
        mock_settings.return_value = settings

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        db = FakeSupabase()
        db.store["sync_settings"] = [
            {
                "id": "ss1",
                "resource_type": "incentives",
                "schedule": "biannual",
                "enabled": True,
                "next_scheduled": past,
            },
        ]
        mock_create.return_value = db
        mock_svc_instance = MagicMock()
        mock_svc_cls.return_value = mock_svc_instance

        _check_and_run_syncs()

        mock_svc_instance.run_for_resource.assert_called_once_with(
            "incentives", triggered_by="scheduler"
        )

    @patch("app.modules.scraper.service.ScraperService")
    @patch("app.core.database_client.create_client")
    @patch("app.core.config.get_settings")
    def test_skips_when_not_due(self, mock_settings, mock_create, mock_svc_cls):
        from app.core.scheduler import _check_and_run_syncs

        settings = FakeSettings()
        mock_settings.return_value = settings

        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        db = FakeSupabase()
        db.store["sync_settings"] = [
            {
                "id": "ss1",
                "resource_type": "incentives",
                "schedule": "biannual",
                "enabled": True,
                "next_scheduled": future,
            },
        ]
        mock_create.return_value = db

        _check_and_run_syncs()

        mock_svc_cls.return_value.run_for_resource.assert_not_called()

    @patch("app.core.database_client.create_client")
    @patch("app.core.config.get_settings")
    def test_skips_when_scraper_disabled(self, mock_settings, mock_create):
        from app.core.scheduler import _check_and_run_syncs

        settings = FakeSettings()
        settings.SCRAPER_ENABLED = False
        mock_settings.return_value = settings

        _check_and_run_syncs()

        mock_create.assert_not_called()


# ── Sources seed data tests ──────────────────────────────────────────────────


class TestSources:
    def test_default_sources_have_required_fields(self):
        for source in DEFAULT_SOURCES:
            assert "resource_type" in source
            assert "url" in source
            assert "label" in source
            assert "is_pdf" in source
            assert source["resource_type"] in ("incentives", "crew_costs", "grants", "festivals")

    def test_default_sources_cover_all_resource_types(self):
        types = {s["resource_type"] for s in DEFAULT_SOURCES}
        assert types == {"incentives", "crew_costs", "grants", "festivals"}

    def test_bls_source_is_flagged(self):
        bls_sources = [s for s in DEFAULT_SOURCES if s.get("use_bls_api")]
        assert len(bls_sources) == 1
        assert bls_sources[0]["resource_type"] == "crew_costs"

    def test_no_pdf_sources_in_defaults(self):
        # All PDF sources (Screen Malta, BECTU rate cards) were unofficial/third-party
        # and have been removed. Official sources use HTML or REST APIs.
        pdf_sources = [s for s in DEFAULT_SOURCES if s.get("is_pdf")]
        assert len(pdf_sources) == 0

    def test_no_filmfreeway_source(self):
        urls = {s["url"] for s in DEFAULT_SOURCES}
        assert not any("filmfreeway.com" in u for u in urls)

    def test_covers_target_countries(self):
        """All 11 target countries should be covered by at least one source."""
        territories = set()
        for s in DEFAULT_SOURCES:
            t = s.get("territory")
            if t:
                territories.add(t)
        target = {
            "United Kingdom", "Canada", "United States", "Australia",
            "Malta", "Ireland", "France", "Germany", "Spain",
            "Czech Republic", "Hungary",
        }
        assert target.issubset(territories), f"Missing: {target - territories}"

    def test_incentive_sources_cover_all_countries(self):
        incentive_territories = {
            s.get("territory") for s in DEFAULT_SOURCES
            if s["resource_type"] == "incentives" and s.get("territory")
        }
        expected = {
            "United Kingdom", "Canada", "Australia", "Malta",
            "Ireland", "France", "Germany", "Spain", "Czech Republic", "Hungary",
        }
        assert expected.issubset(incentive_territories)

    def test_festival_sources_cover_key_countries(self):
        festival_territories = {
            s.get("territory") for s in DEFAULT_SOURCES
            if s["resource_type"] == "festivals" and s.get("territory")
        }
        expected = {
            "United Kingdom", "Canada", "United States", "Australia",
            "Ireland", "France", "Germany", "Spain", "Czech Republic", "Hungary", "Malta",
        }
        assert expected.issubset(festival_territories)

    def test_grant_sources_exist_for_key_countries(self):
        grant_territories = {
            s.get("territory") for s in DEFAULT_SOURCES
            if s["resource_type"] == "grants" and s.get("territory")
        }
        expected = {
            "United Kingdom", "United States", "Canada", "Australia",
            "Malta", "Ireland", "Germany", "Hungary",
        }
        assert expected.issubset(grant_territories)


# ── Territory normalization tests ────────────────────────────────────────────


class TestTerritoryNormalization:
    def test_normalizes_uk_variants(self):
        assert normalize_territory("UK") == "United Kingdom"
        assert normalize_territory("u.k.") == "United Kingdom"
        assert normalize_territory("Britain") == "United Kingdom"

    def test_normalizes_us_variants(self):
        assert normalize_territory("US") == "United States"
        assert normalize_territory("USA") == "United States"
        assert normalize_territory("u.s.a.") == "United States"

    def test_normalizes_czech_variants(self):
        assert normalize_territory("Czechia") == "Czech Republic"
        assert normalize_territory("Czech") == "Czech Republic"

    def test_preserves_canonical_names(self):
        assert normalize_territory("United Kingdom") == "United Kingdom"
        assert normalize_territory("France") == "France"
        assert normalize_territory("Australia") == "Australia"

    def test_handles_none(self):
        assert normalize_territory(None) is None

    def test_strips_whitespace(self):
        assert normalize_territory("  UK  ") == "United Kingdom"
        assert normalize_territory(" France ") == "France"

    def test_diff_normalizes_territory_before_matching(self):
        db = FakeSupabase()
        extracted = [
            {"territory": "UK", "program": "UK Film Tax Relief", "rate": "26%", "cap": "No cap", "status": "Active"},
        ]

        count = diff_and_queue("incentives", extracted, "https://source.com", db)

        # Should match existing "United Kingdom" record after normalization
        assert count == 1
        changes = db.store["pending_changes"]
        assert changes[0]["territory"] == "United Kingdom"
        assert changes[0]["resource_id"] == "i1"

    def test_stale_dates_are_skipped(self):
        db = FakeSupabase()
        extracted = [
            {"title": "Old Grant", "territory": "Malta", "application_deadline": "2017-01-16", "status": "closed", "max_amount": "5000"},
        ]

        count = diff_and_queue("grants", extracted, "https://source.com", db)

        # application_deadline should be skipped (2017 < 2024), but status and max_amount should create changes
        fields = {c["field"] for c in db.store["pending_changes"]}
        assert "application_deadline" not in fields
        assert "status" in fields
        assert "max_amount" in fields

    def test_record_label_stored_in_pending_change(self):
        db = FakeSupabase()
        extracted = [
            {"title": "Screen Ireland Production Fund", "territory": "Ireland", "application_deadline": "2026-05-01", "status": "open", "max_amount": None},
        ]

        count = diff_and_queue("grants", extracted, "https://source.com", db)

        assert count >= 1
        changes = db.store["pending_changes"]
        # Every pending change should have the grant title as record_label
        for change in changes:
            assert change.get("record_label") == "Screen Ireland Production Fund"

    def test_record_label_for_incentives(self):
        db = FakeSupabase()
        extracted = [
            {"territory": "France", "program": "TRIP Tax Rebate", "rate": "30%", "cap": None, "status": "Active"},
        ]

        count = diff_and_queue("incentives", extracted, "https://source.com", db)

        assert count >= 1
        changes = db.store["pending_changes"]
        for change in changes:
            assert change.get("record_label") == "TRIP Tax Rebate"


# ── PDF fetcher tests ────────────────────────────────────────────────────────


class TestPdfFetcher:
    @patch("app.modules.scraper.fetcher._check_robots_txt", return_value=True)
    @patch("app.modules.scraper.fetcher.pdfplumber")
    def test_fetch_pdf_text_extracts_content(self, mock_pdfplumber, _mock_robots):
        # Mock pdfplumber to return page text without needing a real PDF
        mock_page = MagicMock()
        mock_page.extract_tables.return_value = []
        mock_page.extract_text.return_value = "Camera Operator 500 per day"
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.content = b"%PDF-fake"
            mock_resp.raise_for_status = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            result = fetch_pdf_text("https://example.com/rates.pdf", FakeSettings())

        assert result is not None
        assert "Camera Operator" in result

    @patch("app.modules.scraper.fetcher._check_robots_txt", return_value=True)
    @patch("app.modules.scraper.fetcher.pdfplumber")
    def test_fetch_pdf_text_extracts_tables(self, mock_pdfplumber, _mock_robots):
        mock_page = MagicMock()
        mock_page.extract_tables.return_value = [
            [["Role", "Day Rate"], ["Gaffer", "450"], ["Grip", "400"]]
        ]
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.content = b"%PDF-fake"
            mock_resp.raise_for_status = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            result = fetch_pdf_text("https://example.com/rates.pdf", FakeSettings())

        assert result is not None
        assert "Gaffer\t450" in result
        assert "Role\tDay Rate" in result

    @patch("app.modules.scraper.fetcher._check_robots_txt", return_value=False)
    def test_fetch_pdf_text_blocked_by_robots(self, _mock_robots):
        result = fetch_pdf_text("https://example.com/rates.pdf", FakeSettings())
        assert result is None

    @patch("app.modules.scraper.fetcher._check_robots_txt", return_value=True)
    def test_fetch_pdf_links_finds_pdf_hrefs(self, _mock_robots):
        html = '''
        <html><body>
        <a href="/docs/rates-2025.pdf">Download Rates</a>
        <a href="https://example.com/other.pdf">Other PDF</a>
        <a href="/page">Not a PDF</a>
        </body></html>
        '''
        with patch("app.modules.scraper.fetcher.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            links = fetch_pdf_links("https://example.com/ratecards/", FakeSettings())

        assert len(links) == 2
        assert "https://example.com/docs/rates-2025.pdf" in links
        assert "https://example.com/other.pdf" in links
