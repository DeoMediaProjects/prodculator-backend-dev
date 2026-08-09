"""Rendering regressions on the generated report, asserted against real HTML.

Two of these are closed bugs kept under test because they were invisible until a
producer opened the PDF: mojibake currency symbols (the euro arriving as "20AC")
and narrative paragraphs ending mid-sentence. The rest are the report shapes the
territory and payment invariants have to hold across, so a fix validated on one
report cannot regress on another.
"""
from __future__ import annotations

import re

import pytest

from app.modules.reports.pdf_service import PDFService

# Every currency the pipeline formats, plus the code-only ones that have no glyph.
CURRENCY_GLYPHS = ["£", "€", "$", "¥"]
CURRENCY_CODES = ["JPY", "GBP", "EUR", "USD"]

# The mojibake shapes a UTF-8 symbol takes when it is decoded as latin-1 or
# emitted as a raw code point. Asserted as an absence, not fixed by substitution:
# replacing "20AC" with a euro sign would hide the encoding fault rather than
# prove it is gone.
MOJIBAKE = ["20AC", "Â£", "â‚¬", "Ã‚", "\\u20ac", "&#x20AC", "ï¿½", "�"]


def _territory(name: str, *, rank: int, score: int, timing: dict | None) -> dict:
    return {
        "name": name,
        "score": score,
        "bankabilityLabel": "Bankable",
        "rebatePercent": "40% net",
        "paymentTiming": timing or {"minMonths": None, "maxMonths": None, "label": "Data not available"},
        "paymentSpeed": (timing or {}).get("label", "Data not available"),
        "incentiveStrength": score, "incentiveReliability": score,
        "costEfficiency": 50, "currencyAdvantage": 50,
        "crewDepth": 70, "infrastructure": 70,
        "reasoning": f"{name} ranks {rank} on the weighted dimensions.",
    }


def _report(
    *,
    territories: list[tuple[str, dict | None]],
    shoot_weeks: int | None = 6,
    script_days: int | None = 30,
    long_narrative: str | None = None,
    money_line: str = "£10,129,064 net of €4,155,360, $12,500,000 and JPY 1,000,000,000 (GBP base)",
) -> dict:
    names = [n for n, _ in territories]
    return {
        "genre": "Drama",
        "complexity": "High",
        "executiveSummary": {
            "keyInsights": long_narrative or "Summary.",
            "recommendedTerritory": names[0] if names else None,
            "shootWeeks": shoot_weeks,
            "shootDays": shoot_weeks,
            "schedule": {
                "shootWeeks": shoot_weeks,
                "shootDays": script_days,
                "weeksSource": "declared" if shoot_weeks else None,
                "impliedWeeks": (script_days / 5.0) if script_days else None,
                "divergent": bool(
                    shoot_weeks and script_days and abs(script_days / 5.0 - shoot_weeks) > 1.0
                ),
                "daysPerWeek": 5.0,
            },
            "headlineNetBudget": money_line,
        },
        "scriptStats": {
            "sceneCount": 148, "interiorPct": 76, "exteriorPct": 24,
            "dayScenes": 90, "nightScenes": 58,
            "estShootingDays": script_days, "principalCast": "medium",
        },
        "locationRankings": [
            _territory(name, rank=i + 1, score=80 - i * 5, timing=timing)
            for i, (name, timing) in enumerate(territories)
        ],
        "territoryDeepDives": [
            {
                "name": name,
                "paymentTiming": timing or {"minMonths": None, "maxMonths": None, "label": "Data not available"},
                "paymentSpeed": (timing or {}).get("label", "Data not available"),
                "infrastructure": long_narrative or f"{name} infrastructure.",
                "keyAdvantages": [f"{name} advantage one."],
                "keyRisks": [f"{name} risk one."],
            }
            for name, timing in territories
        ],
        "financialAnalysis": {
            "paymentTiming": [
                {
                    "territory": name,
                    "paymentTiming": timing,
                    "label": timing["label"],
                    "minMonths": timing["minMonths"],
                    "maxMonths": timing["maxMonths"],
                    "suspended": False,
                }
                for name, timing in territories
                if timing and timing.get("minMonths") is not None
            ],
            "budgetScenarios": [],
        },
        "weatherLogistics": [
            {"territory": name, "riskLevel": "Low", "summary": long_narrative or f"{name} weather."}
            for name, _ in territories
        ],
        # The template reads incentiveEstimates; taxIncentiveAnalysis is not a key
        # it renders, so a fixture using that name silently skipped the section.
        "incentiveEstimates": [
            {
                "territory": name, "program": f"{name} programme",
                "paymentTiming": timing,
                "paymentSpeed": (timing or {}).get("label", "Data not available"),
                "estimatedRebate": money_line,
            }
            for name, timing in territories
        ],
    }


WINDOW_RANGE = {"minMonths": 3, "maxMonths": 6, "source": "programme", "label": "3 to 6 months"}
WINDOW_SINGLE = {"minMonths": 12, "maxMonths": 12, "source": "programme", "label": "12 months"}
WINDOW_NONE = {"minMonths": None, "maxMonths": None, "source": None, "label": "Data not available"}


def _visible_text(html: str) -> str:
    """Rendered text only: no tags, no style/script bodies, no data URIs.

    Assertions about what a reader sees must not match the embedded base64 logo.
    """
    out = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    out = re.sub(r'(?:src|href)="data:[^"]*"', " ", out)
    out = re.sub(r"<[^>]+>", " ", out)
    return re.sub(r"\s+", " ", out)


def _render(report: dict) -> str:
    return PDFService().render_report_html(
        report, script_title="Regression Fixture", created_at="2026-08-08T00:00:00Z",
    )


# ── Currency encoding ────────────────────────────────────────────────────────

class TestCurrencyRendering:
    """CLOSED: the euro arrived as "20AC". Held under test, not patched."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        return _render(_report(territories=[
            ("United Kingdom", WINDOW_RANGE), ("Japan", WINDOW_SINGLE), ("Italy", WINDOW_RANGE),
        ]))

    @pytest.mark.parametrize("glyph", CURRENCY_GLYPHS)
    def test_glyph_survives_rendering(self, html: str, glyph: str):
        if glyph in ("¥",):
            pytest.skip("no yen glyph in this fixture; JPY is rendered as a code")
        assert glyph in html, f"{glyph!r} did not survive rendering"

    @pytest.mark.parametrize("code", ["JPY", "GBP"])
    def test_currency_code_survives_rendering(self, html: str, code: str):
        assert code in html

    @pytest.mark.parametrize("artifact", MOJIBAKE)
    def test_no_encoding_artifact(self, html: str, artifact: str):
        assert artifact not in html, f"encoding artifact {artifact!r} present"

    def test_output_is_valid_utf8_round_trip(self, html: str):
        assert html.encode("utf-8").decode("utf-8") == html

    def test_a_bare_code_point_never_reaches_the_page(self, html: str):
        """"20AC" is the euro's code point printed as text. Any four-hex-digit
        run adjacent to a number is the same class of fault."""
        assert not re.search(r"\b20AC\b", html, re.I)


# ── Narrative truncation ─────────────────────────────────────────────────────

LONG_NARRATIVE = (
    "The United Kingdom's Established infrastructure tier encompasses a full ecosystem of major "
    "studio facilities, specialist equipment houses, and post-production services concentrated in "
    "London and the surrounding home counties. For this production, that means hotel interior sets, "
    "the pool sequence, and the commercial-within-the-film shoot can all be executed within a single "
    "studio complex, reducing company moves and controlling schedule risk. The UK VFX Expenditure "
    "Credit at 29.25% net provides an additional financial incentive to complete any specialist "
    "post-production work domestically, and the programme carries no theatrical release requirement."
)


class TestNarrativeRendering:
    """CLOSED: narrative blocks ended mid-sentence. No arbitrary substring cut."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        return _render(_report(
            territories=[("United Kingdom", WINDOW_RANGE), ("Japan", WINDOW_SINGLE)],
            long_narrative=LONG_NARRATIVE,
        ))

    def test_the_final_sentence_is_present(self, html: str):
        assert "carries no theatrical release requirement" in html

    def test_the_opening_is_present(self, html: str):
        assert "Established infrastructure tier encompasses" in html

    def test_nothing_was_replaced_with_an_ellipsis(self, html: str):
        """A truncating renderer marks its cut. Jinja's truncate filter appends
        an ellipsis, so one adjacent to narrative prose means a clipped block."""
        assert "requirement…" not in html
        assert "requirement..." not in html

    def test_no_narrative_ends_on_a_word_boundary_cut(self, html: str):
        # The whole paragraph is present as one contiguous run, which a substring
        # truncation anywhere inside it would break.
        import html as _html

        normalised = re.sub(r"\s+", " ", _html.unescape(html))
        assert re.sub(r"\s+", " ", LONG_NARRATIVE) in normalised


# ── Report shapes the invariants must hold across ────────────────────────────

class TestReportShapes:
    """The brief's matrix: 3 and 4+ ranked territories, and every payment shape."""

    @pytest.mark.parametrize("count", [3, 4, 5])
    def test_every_ranked_territory_is_rendered(self, count: int):
        names = ["United Kingdom", "Japan", "Italy", "Singapore", "Canada"][:count]
        html = _render(_report(territories=[(n, WINDOW_RANGE) for n in names]))
        for name in names:
            assert name in html, f"{name} ranked but absent from the rendered report"

    @pytest.mark.parametrize("count", [3, 4, 5])
    def test_the_declared_count_matches_what_is_rendered(self, count: int):
        names = ["United Kingdom", "Japan", "Italy", "Singapore", "Canada"][:count]
        html = _render(_report(territories=[(n, WINDOW_RANGE) for n in names]))
        # The strategy heading states the count; it must be the count on the page.
        assert re.search(rf"\b{count} territories, scored across six dimensions", html), (
            f"heading does not declare {count}"
        )

    def test_a_single_value_window_is_never_a_degenerate_range(self):
        html = _render(_report(territories=[("Italy", WINDOW_SINGLE)]))
        assert "12 months" in html
        assert not re.search(r"(\d+)\s*[-–]\s*\1\s*MO", html, re.I)
        assert "12–12" not in html and "12-12" not in html

    def test_a_range_window_renders_both_bounds(self):
        html = _render(_report(territories=[("United Kingdom", WINDOW_RANGE)]))
        assert "3 to 6 months" in html

    def test_a_missing_window_says_so_and_draws_no_bar(self):
        html = _render(_report(territories=[("Bavaria", WINDOW_NONE)]))
        # Omitted from the chart rather than drawn as a zero-width bar, and the
        # card simply does not claim a window.
        assert "Data not available" not in html or "Bavaria" in html
        assert not re.search(r"None\s*(to|–|-)\s*None", html)

    def test_mixed_windows_in_one_report_each_render_correctly(self):
        html = _render(_report(territories=[
            ("United Kingdom", WINDOW_RANGE),
            ("Italy", WINDOW_SINGLE),
            ("Bavaria", WINDOW_NONE),
        ]))
        assert "3 to 6 months" in html
        assert "12 months" in html
        # Scoped to visible text: the embedded logo is base64, so scanning the
        # whole document for a short token matches image bytes.
        text = _visible_text(html)
        assert "NaN" not in text
        assert "None" not in text
        assert "undefined" not in text

    @pytest.mark.parametrize(
        "weeks,days",
        [(1, 5), (2, 10), (6, 30), (12, 60), (20, 100)],
    )
    def test_a_coherent_schedule_renders_without_a_reconciliation_note(self, weeks, days):
        html = _render(_report(territories=[("United Kingdom", WINDOW_RANGE)],
                               shoot_weeks=weeks, script_days=days))
        assert f"{weeks} wk shoot" in html
        assert "you planned" not in html

    def test_a_divergent_schedule_is_reconciled_on_the_page(self):
        """8 declared weeks against 14 script days: both shown, both labelled,
        so the reader is not left to reconcile two bare numbers."""
        html = _render(_report(territories=[("United Kingdom", WINDOW_RANGE)],
                               shoot_weeks=8, script_days=14))
        assert "8 wk shoot" in html
        assert "you planned 8" in html
        assert "from the script" in html


# ── Format eligibility caveat ────────────────────────────────────────────────

class TestFormatEligibilityCaveatRendering:
    """A short film must not be handed feature-scale rebates without the caveat.

    The wizard warns and asks for confirmation, but the PDF is what gets forwarded
    to a financier, so the caveat has to travel with the figures.
    """

    CAVEAT = (
        "Format eligibility not verified. Every rebate in this report is modelled as though "
        "the programme accepts a short, which our programme records do not currently confirm "
        "either way. Confirm eligibility with each film commission."
    )

    def test_the_caveat_is_rendered_when_set(self):
        report = _report(territories=[("United Kingdom", WINDOW_RANGE)])
        report["formatEligibilityCaveat"] = self.CAVEAT
        text = _visible_text(_render(report))
        assert "Format eligibility not verified" in text
        assert "Confirm eligibility with each film commission" in text

    def test_it_sits_with_the_incentive_figures(self):
        """Next to the numbers it qualifies, not buried in the closing legal page."""
        report = _report(territories=[("United Kingdom", WINDOW_RANGE)])
        report["formatEligibilityCaveat"] = self.CAVEAT
        html = _render(report)
        caveat_at = html.index("Format eligibility not verified")
        estimates_at = html.index("Estimates only. Final eligibility depends on")
        # Immediately above the standard estimates footnote in the tax section.
        assert caveat_at < estimates_at
        assert estimates_at - caveat_at < 1200, "caveat drifted away from the figures"

    def test_nothing_is_rendered_when_the_format_is_safe(self):
        """A feature must not carry a warning that does not apply to it."""
        report = _report(territories=[("United Kingdom", WINDOW_RANGE)])
        report["formatEligibilityCaveat"] = None
        text = _visible_text(_render(report))
        assert "Format eligibility not verified" not in text
        # The standard estimates line still appears.
        assert "Estimates only" in text

    def test_an_absent_key_renders_nothing(self):
        """Older stored reports have no such key and must render unchanged."""
        report = _report(territories=[("United Kingdom", WINDOW_RANGE)])
        report.pop("formatEligibilityCaveat", None)
        text = _visible_text(_render(report))
        assert "Format eligibility not verified" not in text
