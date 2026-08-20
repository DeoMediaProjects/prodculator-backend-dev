"""Explorer (free) reports carry eight of the thirteen sections.

Explorer was gated down to roughly three usable sections, which read as a locked
document rather than a preview. The owner-approved set is:

    01 Executive Summary            06 Financial Readiness
    03 Production Location Strategy 07 Weather & Logistics
    09 Grant & Funding              10 Comparable Productions
    12 Distributors                 13 Next Steps

Still paid, and rendered as locked teasers: Script Intelligence, Territory
Analysis, Financial Analysis, Tax Incentive Analysis, Festival Recommendations.

The rule that survives the change: monetary values stay behind the paywall in
every section, including the newly opened ones. Financial Readiness is included
for its verdicts, statuses, checks and flags — not its figures.
"""
from __future__ import annotations

import re

import pytest

from app.modules.reports.router import (
    _EXPLORER_SECTIONS,
    _build_free_tier_report_data,
    _strip_readiness_financials,
)

_CURRENCY = re.compile(r"[£€$]\s?\d")
_RATE = re.compile(r"\b\d+(?:\.\d+)?%")


def _readiness() -> dict:
    """A realistic readiness object. The sample report's is a marketing stub with
    no components, so it cannot exercise the stripper."""
    return {
        "verdict": "CONDITIONAL",
        "verdictReason": "Soft money covers £2,400,000 of a £4,100,000 gap (59%).",
        "rule": "conditional_when_gap_partially_covered",
        "score": 62,
        "territory": "United Kingdom",
        "programme": "AVEC (Enhanced/IFTC)",
        "currencySymbol": "£",
        "components": [
            {
                "key": "budget_vs_cost_base",
                "label": "Budget vs cost base",
                "status": "conditional",
                "weight": 40,
                "headline": "Budget of £18,700,000 leaves a £4,100,000 gap.",
                "figures": [
                    {"label": "Budget", "value": "£18,700,000", "basis": "submitted"},
                    {"label": "Gap", "value": "£4,100,000", "basis": "derived"},
                ],
                "checks": [
                    {
                        "name": "Gap under 25% of budget",
                        "result": "warn",
                        "detail": "Gap is £4,100,000, which is 21.9% of budget.",
                    },
                ],
                "note": "Verify the £2,400,000 soft-money commitments in writing.",
            },
            {
                "key": "incentive_confidence",
                "label": "Incentive confidence",
                "status": "ready",
                "weight": 30,
                "headline": "IFTC eligibility confirmed on all dimensions.",
                "figures": [],
                "checks": [
                    {"name": "Producer eligible", "result": "pass", "detail": "UK company."},
                ],
                "note": None,
            },
        ],
        "flags": [
            {
                "severity": "warning",
                "input": "incentive_programs.last_verified_at",
                "detail": "Record older than 180 days; rate of 53% may have moved.",
                "action": "Re-verify against HMRC before relying on the £6,360,000 ceiling.",
            },
        ],
        "flagCounts": {"critical": 0, "warning": 1, "info": 0},
        "methodology": "Weighted from component statuses; gap measured against £18,700,000.",
        "computedOn": "2026-08-19",
    }


def _report_data() -> dict:
    """Full report_data carrying every section Explorer might see or be denied."""
    return {
        "executiveSummary": {
            "keyInsights": "**Production Overview**\nA feature shooting in the UK.",
            "budget": "£18,700,000",
            "headlineNetBudget": "£12,340,000",
            "recommendedTerritory": "United Kingdom",
            "recommendedTerritoryRebate": "£6,360,000",
            "recommendedTerritoryPaymentSpeed": "6-8 weeks",
            "actionTimeline": [{"step": "BFI certification"}],
            "keyFlags": ["Theatrical release required"],
        },
        "locationRankings": [
            {
                "name": "United Kingdom", "country": "UK", "score": 88,
                "reasoning": "Deep crew base; AVEC pays £6,360,000 at a 53% rate.",
                "keyAdvantages": ["Crew depth", "Rebate worth £6,360,000"],
                "keyRisks": ["Cultural test required"],
                "rebatePercent": 53.0, "rebateAmount": "£6,360,000",
                "paymentSpeed": "6-8 weeks", "culturalTestLikelihood": "High",
                "adminComplexity": "Medium", "financialReturnScore": 81,
                "financialReturnVerdict": "Bankable",
            },
            {
                "name": "Hungary", "country": "HU", "score": 79,
                "reasoning": "30% cash rebate, fast payment.",
                "keyAdvantages": ["Speed"], "keyRisks": ["Language"],
                "rebatePercent": 30.0, "rebateAmount": "€4,200,000",
            },
            {
                "name": "Malta", "country": "MT", "score": 71,
                "reasoning": "Coastal exteriors.",
                "keyAdvantages": ["Water tank"], "keyRisks": ["Crew depth"],
                "rebatePercent": 40.0, "rebateAmount": "€3,000,000",
            },
            {"name": "Should Not Appear", "country": "XX", "score": 60},
        ],
        "financialReadiness": _readiness(),
        "weatherLogistics": [
            {
                "territory": "United Kingdom", "bestMonths": ["May", "Jun"],
                "weatherRisk": "Medium", "infrastructure": "Excellent",
                "travelVisa": "None for EU", "exteriorExposure": "High (72% exterior scenes)",
                "avgTempRange": "8-18C", "estimatedDelayDays": 4,
            },
        ],
        "fundingOpportunities": [
            {
                "type": "Fund", "name": "BFI Filmmaking Fund", "genre": ["Drama"],
                "deadline": "2026-10-01", "notes": "Awards up to £500,000 per project.",
                "website": "https://bfi.org.uk", "tier": "National",
            },
        ],
        "comparables": [
            {
                "title": "A Comparable Film", "genre": "Drama",
                "budgetRange": "£15M-£20M", "budgetUSD": 19000000,
                "visualScale": "Mid", "location": "UK", "year": 2024,
                "source": "BFI", "relevanceDescription": "Similar scale and territory.",
            },
        ],
        "distributorRecommendations": [
            {
                "name": "A Distributor", "primaryMarket": "UK",
                "territoryReach": ["UK", "IE"], "rightsType": "All rights",
                "budgetTierFit": "Mid-budget", "whyMatched": "Track record in UK drama.",
                "verified": True,
            },
        ],
        "nextSteps": [
            {"action": "Apply for BFI cultural test", "deadline": "12-16 weeks out", "priority": "URGENT"},
            {"action": "Confirm the £2,400,000 soft money", "deadline": "On approval", "priority": "HIGH"},
        ],
        # Withheld
        "scriptIntelligence": {"complexityDrivers": ["Stunts"]},
        "scriptStats": {"scenes": 120},
        "territoryDeepDives": [{"name": "United Kingdom"}],
        "festivalRecommendations": [{"name": "Cannes"}],
        "investorSummary": {"irr": "18%"},
        "alternativeStrategy": "Lead with Hungary.",
        "dimensionVerdicts": {"United Kingdom": {}},
        # Paid teasers — kept, stripped to labels
        "incentiveEstimates": [
            {"territory": "United Kingdom", "program": "AVEC", "estimatedRebate": "£6,360,000"},
        ],
        "financialAnalysis": {
            "budgetScenarios": [
                {"territory": "United Kingdom", "programme": "AVEC", "netCost": "£12,340,000"},
            ],
        },
    }


@pytest.fixture()
def explorer() -> dict:
    return _build_free_tier_report_data(_report_data())


# ── Which sections exist ─────────────────────────────────────────────────────


class TestExplorerSectionSet:
    @pytest.mark.parametrize("section", sorted(_EXPLORER_SECTIONS))
    def test_every_explorer_section_is_present_and_non_empty(self, explorer, section):
        assert section in explorer, f"{section} is missing from an Explorer report"
        assert explorer[section], f"{section} is present but empty"

    @pytest.mark.parametrize(
        "section",
        [
            "scriptIntelligence",
            "scriptStats",
            "territoryDeepDives",
            "festivalRecommendations",
            "investorSummary",
            "alternativeStrategy",
            "dimensionVerdicts",
        ],
    )
    def test_withheld_sections_are_absent(self, explorer, section):
        assert section not in explorer

    def test_paid_teasers_survive_as_labels_only(self, explorer):
        """Locked teasers need the territory/programme labels to render a heading,
        and must carry no figures."""
        assert explorer["incentiveEstimates"] == [
            {"territory": "United Kingdom", "program": "AVEC"},
        ]
        assert explorer["financialAnalysis"]["budgetScenarios"] == [
            {"territory": "United Kingdom", "programme": "AVEC"},
        ]

    def test_the_source_report_is_not_mutated(self):
        """The filter is applied to live report_data on the read path."""
        source = _report_data()
        _build_free_tier_report_data(source)
        assert "scriptIntelligence" in source
        assert source["nextSteps"][1]["action"] == "Confirm the £2,400,000 soft money"


# ── Money stays paid ─────────────────────────────────────────────────────────


class TestNoMonetaryLeak:
    @pytest.mark.parametrize("section", sorted(_EXPLORER_SECTIONS - {"executiveSummary"}))
    def test_no_currency_amount_appears_in_any_explorer_section(self, explorer, section):
        leaks = []

        def walk(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for idx, value in enumerate(node):
                    walk(value, f"{path}[{idx}]")
            elif isinstance(node, str) and _CURRENCY.search(node):
                leaks.append((path, node))

        walk(explorer[section], section)
        assert not leaks, f"currency leaked into {section}: {leaks}"

    def test_executive_summary_keeps_the_producers_own_budget_only(self, explorer):
        """The submitted budget is the producer's own input, so it stays. Every
        DERIVED figure goes."""
        summary = explorer["executiveSummary"]
        assert summary["budget"] == "£18,700,000"
        for derived in (
            "headlineNetBudget",
            "recommendedTerritoryRebate",
            "recommendedTerritoryPaymentSpeed",
            "actionTimeline",
            "keyFlags",
        ):
            assert derived not in summary

    def test_comparables_lose_their_budget_columns(self, explorer):
        comp = explorer["comparables"][0]
        assert "budgetRange" not in comp
        assert "budgetUSD" not in comp
        # ...and keep everything that made the section worth reading.
        assert comp["title"] == "A Comparable Film"
        assert comp["relevanceDescription"] == "Similar scale and territory."

    def test_funding_notes_are_redacted_but_the_fund_survives(self, explorer):
        opp = explorer["fundingOpportunities"][0]
        assert opp["name"] == "BFI Filmmaking Fund"
        assert opp["deadline"] == "2026-10-01"
        assert opp["website"] == "https://bfi.org.uk"
        assert "£500,000" not in opp["notes"]


# ── Financial Readiness: judgement without figures ───────────────────────────


class TestFinancialReadiness:
    def test_the_verdict_and_score_survive(self, explorer):
        readiness = explorer["financialReadiness"]
        assert readiness["verdict"] == "CONDITIONAL"
        assert readiness["score"] == 62
        assert readiness["territory"] == "United Kingdom"

    def test_component_statuses_and_checks_survive(self, explorer):
        components = explorer["financialReadiness"]["components"]
        assert [c["key"] for c in components] == [
            "budget_vs_cost_base", "incentive_confidence",
        ]
        assert [c["status"] for c in components] == ["conditional", "ready"]
        assert components[0]["checks"][0]["result"] == "warn"

    def test_the_figures_list_is_removed_from_every_component(self, explorer):
        for component in explorer["financialReadiness"]["components"]:
            assert "figures" not in component

    def test_flags_survive_with_their_severity_and_input(self, explorer):
        flag = explorer["financialReadiness"]["flags"][0]
        assert flag["severity"] == "warning"
        assert flag["input"] == "incentive_programs.last_verified_at"
        assert explorer["financialReadiness"]["flagCounts"]["warning"] == 1

    def test_no_currency_survives_anywhere_in_the_section(self, explorer):
        import json

        blob = json.dumps(explorer["financialReadiness"])
        assert not _CURRENCY.search(blob), blob

    def test_no_computed_rate_survives_in_the_narrative_fields(self, explorer):
        """Percentages describing THIS production are redacted.

        Static rule thresholds are not: a check named "Gap under 25% of budget"
        states the rule being applied, not the producer's own number, and
        redacting it would leave "Gap under [upgrade to see rate] of budget" —
        less informative with nothing gained. So the assertion covers the fields
        that carry computed values, not every string in the section.
        """
        readiness = explorer["financialReadiness"]
        narrative = [readiness["verdictReason"], readiness["methodology"]]
        for component in readiness["components"]:
            narrative.append(component["headline"])
            if component.get("note"):
                narrative.append(component["note"])
            narrative.extend(check["detail"] for check in component["checks"])
        for flag in readiness["flags"]:
            narrative.extend([flag["detail"], flag["action"]])

        for text in narrative:
            assert not _RATE.search(text), f"computed rate leaked: {text!r}"

    def test_a_missing_section_yields_none_rather_than_a_stub(self):
        assert _strip_readiness_financials(None) is None
        assert _strip_readiness_financials("not a dict") is None

    def test_a_stub_readiness_does_not_crash(self):
        """The marketing sample carries a readiness object with no components."""
        result = _strip_readiness_financials(
            {"verdict": "CONDITIONAL", "score": 0, "components": [], "flags": []},
        )
        assert result["components"] == []
        assert result["flags"] == []


# ── Production Location Strategy ─────────────────────────────────────────────


class TestLocationStrategy:
    def test_all_three_territories_are_named(self, explorer):
        names = [loc["name"] for loc in explorer["locationRankings"]]
        assert names == ["United Kingdom", "Hungary", "Malta"]

    def test_no_locked_placeholders_remain(self, explorer):
        for loc in explorer["locationRankings"]:
            assert not loc.get("lockedPreview")
            assert loc.get("country") != "Locked"
            assert loc.get("score") is not None

    def test_reasoning_and_risks_are_present_for_every_territory(self, explorer):
        for loc in explorer["locationRankings"]:
            assert loc.get("reasoning")
            assert loc.get("keyAdvantages")
            assert loc.get("keyRisks")

    def test_the_three_territory_cap_still_holds(self, explorer):
        """Professional's five and Producer's unlimited must stay worth paying for."""
        assert len(explorer["locationRankings"]) == 3
        assert "Should Not Appear" not in [
            loc["name"] for loc in explorer["locationRankings"]
        ]

    def test_rebate_values_and_derived_scores_are_removed(self, explorer):
        for loc in explorer["locationRankings"]:
            for paid in (
                "rebatePercent",
                "rebateAmount",
                "estimatedRebate",
                "financialReturnScore",
                "financialReturnVerdict",
            ):
                assert paid not in loc, f"{paid} leaked on {loc['name']}"

    def test_rows_are_still_marked_assessment_only(self, explorer):
        for loc in explorer["locationRankings"]:
            assert loc["isAssessmentOnly"] is True


# ── Weather & Logistics and Distributors pass through intact ─────────────────


class TestPassThroughSections:
    def test_weather_percentages_are_not_mangled_into_upgrade_prompts(self, explorer):
        """exteriorExposure reads "High (72% exterior scenes)". The financial
        redactor rewrites any percentage, so this section must not go through it."""
        weather = explorer["weatherLogistics"][0]
        assert weather["exteriorExposure"] == "High (72% exterior scenes)"
        assert weather["avgTempRange"] == "8-18C"
        assert weather["estimatedDelayDays"] == 4

    def test_distributors_are_carried_through_whole(self, explorer):
        distributor = explorer["distributorRecommendations"][0]
        assert distributor["name"] == "A Distributor"
        assert distributor["rightsType"] == "All rights"
        assert distributor["whyMatched"] == "Track record in UK drama."
        assert distributor["verified"] is True


# ── Next Steps ───────────────────────────────────────────────────────────────


class TestNextSteps:
    def test_actions_are_no_longer_blanked(self, explorer):
        assert len(explorer["nextSteps"]) == 2

    def test_priority_and_deadline_survive(self, explorer):
        step = explorer["nextSteps"][0]
        assert step["action"] == "Apply for BFI cultural test"
        assert step["deadline"] == "12-16 weeks out"
        assert step["priority"] == "URGENT"

    def test_figures_quoted_inside_an_action_are_redacted(self, explorer):
        assert "£2,400,000" not in explorer["nextSteps"][1]["action"]

    def test_the_urgent_counter_still_works(self, explorer):
        """_preview_urgent_action_count reads nextSteps, which used to be []."""
        assert explorer["previewUrgentActionCount"] == 1


# ── The PDF carries the same section set ─────────────────────────────────────


class TestExplorerPdf:
    """The free PDF is rendered from the same filtered payload, so the template's
    own tier gates have to agree with the API's. They were independent before:
    the template locked all thirteen rows off `is_preview` alone."""

    @staticmethod
    def _render() -> str:
        from app.modules.reports.pdf_service import PDFService

        return PDFService().render_report_html(
            _build_free_tier_report_data(_report_data()),
            script_title="A Production",
            report_type="preview",
            created_at="2026-08-19",
            is_preview=True,
        )

    def test_it_renders_at_all(self):
        """Opening these sections up routed Explorer data through template code
        that had only ever seen full reports."""
        assert len(self._render()) > 10_000

    @pytest.mark.parametrize(
        "title,locked",
        [
            ("Executive Summary", False),
            ("Script Intelligence", True),
            ("Production Location Strategy", False),
            ("Territory Analysis", True),
            ("Financial Analysis", True),
            ("Financial Readiness", False),
            ("Weather &amp; Logistics", False),
            ("Tax Incentive Analysis", True),
            ("Grant &amp; Funding Opportunities", False),
            ("Comparable Productions", False),
            ("Festival Recommendations", True),
            ("Next Steps", False),
        ],
    )
    def test_the_contents_page_locks_exactly_the_paid_sections(self, title, locked):
        import re

        html = self._render()
        toc = re.search(r'<table class="toc".*?</table>', html, re.S)
        assert toc, "contents table missing"
        row = next(
            (r for r in re.findall(r"<tr.*?</tr>", toc.group(0), re.S) if title in r),
            None,
        )
        assert row, f"{title} is not in the contents page"
        assert ('class="t locked"' in row) is locked, (
            f"{title} should be {'locked' if locked else 'open'} for Explorer"
        )

    def test_no_paid_figure_reaches_the_pdf(self):
        """Only the producer's own submitted budget may appear."""
        import re

        html = self._render()
        amounts = set(re.findall(r"[£€$]\s?[\d,]{4,}", html))
        assert amounts <= {"£18,700,000"}, f"paid figures leaked into the PDF: {amounts}"


class TestSparseRankingRowsDoNotBreakThePdf:
    """`dimbar`'s guard was `value is not none`, which does not catch a MISSING
    key: Jinja yields Undefined, which is not none, so the guard passed and
    `[value, 100] | min` raised — a 500 from PDF generation.

    Latent while only full reports reached the macro. Explorer rows reach it now,
    and the tier filter removes fields from them, so it is reachable.
    """

    @staticmethod
    def _render(rankings: list[dict]) -> str:
        from app.modules.reports.pdf_service import PDFService

        data = _report_data()
        data["locationRankings"] = rankings
        return PDFService().render_report_html(
            _build_free_tier_report_data(data),
            script_title="A Production",
            report_type="preview",
            created_at="2026-08-19",
            is_preview=True,
        )

    def test_a_row_with_no_dimension_scores_renders(self):
        html = self._render([
            {"name": "United Kingdom", "country": "UK", "score": 88,
             "reasoning": "Crew depth.", "keyAdvantages": ["Crew"], "keyRisks": ["Test"]},
        ])
        assert "United Kingdom" in html

    def test_dimension_scores_still_render_when_present(self):
        html = self._render([
            {"name": "United Kingdom", "country": "UK", "score": 88,
             "reasoning": "Crew depth.", "keyAdvantages": ["Crew"], "keyRisks": ["Test"],
             "incentiveStrength": 90, "costEfficiency": 70, "crewDepth": 80,
             "infrastructure": 85, "currencyAdvantage": 50},
        ])
        assert "width:90%" in html
        assert "width:70%" in html

    def test_a_score_above_one_hundred_is_clamped(self):
        html = self._render([
            {"name": "United Kingdom", "country": "UK", "score": 88,
             "reasoning": "Crew depth.", "keyAdvantages": ["Crew"], "keyRisks": ["Test"],
             "incentiveStrength": 140},
        ])
        assert "width:140%" not in html
        assert "width:100%" in html

    def test_an_empty_ranking_list_renders(self):
        assert len(self._render([])) > 10_000


def test_the_frontend_and_backend_section_lists_agree():
    """Three surfaces decide what Explorer sees: this module's _EXPLORER_SECTIONS,
    ReportViewer's LOCKED_FOR_EXPLORER, and report_base.html's lock flags. They
    drifted apart once already — the API stripped a section the PDF still tried to
    render. This pins the two list-shaped ones together across repos.

    Skips when the frontend checkout is not beside the backend.
    """
    import re
    from pathlib import Path

    frontend = (
        Path(__file__).resolve().parents[2]
        / "prodculator-frontend-dev"
        / "src" / "app" / "hooks" / "explorerSections.ts"
    )
    if not frontend.exists():
        pytest.skip("frontend checkout not present")

    text = frontend.read_text(encoding="utf-8")

    def names(const: str) -> set[str]:
        block = re.search(rf"{const} = \[(.*?)\] as const", text, re.S)
        assert block, f"{const} not found in {frontend}"
        return set(re.findall(r"'([^']+)'", block.group(1)))

    assert names("EXPLORER_SECTIONS") == set(_EXPLORER_SECTIONS), (
        "the frontend's open-section list disagrees with the API's"
    )

    # The withheld list is expressed differently on each side (the frontend names
    # UI sections, the API names payload keys), so only the count is comparable.
    assert len(names("LOCKED_FOR_EXPLORER")) == 5
