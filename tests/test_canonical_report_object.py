"""One report object. One selection path. No surface recomputes.

FIX-01. The PDF and the platform were quoting different UK rates for the same
report: the ranking card said 39.75% net / 53% gross (Independent Film Tax
Credit) and the financial waterfall and tax-incentive section said 25.5% / 34%
(standard AVEC). Both are real UK programmes; the report was costed under one
and ranked under the other.

The cause was not a rounding or display difference. ``best_incentive`` ranks a
programme partly on whether this production clears the programme's own stated
gates, and it can only do that if it is handed the production. Seven call sites
in the builder passed ``_project_facts``. Three did not:

    service._pre_compute_territory_financials
    validator (recommended-territory payment speed)
    validator (_finish_corrected_rebate programme switch)

Without the facts, IFTC's budget-eligibility ceiling could not be tested, so it
came back UNVERIFIABLE and was demoted below standard AVEC, which states no
gates at all and came back AVAILABLE. The financials therefore selected a
different programme row than the ranking, for the same territory, in the same
report.

The facts are now assembled once by the service and read by everyone.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.modules.reports.helpers import best_incentive
from app.modules.reports.service import ReportService


AVEC = {
    "territory": "United Kingdom",
    "program_name": "UK Audio-Visual Expenditure Credit (AVEC)",
    "rate_gross": 34, "rate_net": 25.5,
    "status": "active", "is_supplementary": False,
}
IFTC = {
    "territory": "United Kingdom",
    "program_name": "Independent Film Tax Credit (Enhanced AVEC)",
    "rate_gross": 53, "rate_net": 39.75,
    "status": "active", "is_supplementary": False,
    "budget_eligibility_ceiling": (
        "GBP 23,500,000 total core expenditure - ABOVE THIS, IFTC IS NOT AVAILABLE"
    ),
}
UK_ROWS = [AVEC, IFTC]
FACTS = {"budget_gbp": 45_730, "format": "Short"}


class TestOneProgrammePerTerritory:
    def test_the_same_row_wins_wherever_it_is_asked(self):
        """The regression itself: ranking and financials must agree."""
        ranking = best_incentive(UK_ROWS, "Short", FACTS)
        financials = best_incentive(UK_ROWS, "Short", FACTS)
        assert ranking["program_name"] == financials["program_name"]
        assert (ranking["rate_net"], ranking["rate_gross"]) == (39.75, 53)

    def test_omitting_the_facts_is_what_used_to_change_the_answer(self):
        """Pinned deliberately. If a future change makes the facts irrelevant to
        selection this fails, and the reason for threading them everywhere needs
        rewriting rather than quietly disappearing."""
        with_facts = best_incentive(UK_ROWS, "Short", FACTS)
        without = best_incentive(UK_ROWS, "Short")
        assert with_facts["program_name"] != without["program_name"]

    def test_a_production_above_the_ceiling_is_costed_on_the_standard_rate(self):
        """The gate is real, not a formality: a GBP 30M feature cannot use IFTC."""
        big = best_incentive(UK_ROWS, "Feature Film", {"budget_gbp": 30_000_000})
        assert big["program_name"] == AVEC["program_name"]


class TestEverySelectionSitePassesTheFacts:
    """A grep-level guard. Selection is spread over three modules, and the defect
    was one call site out of ten quietly omitting an argument."""

    MODULES = (
        "app/modules/reports/builder.py",
        "app/modules/reports/service.py",
        "app/modules/reports/validator.py",
    )
    CALL_START = re.compile(r"\b_?best_incentive\(")

    @staticmethod
    def _call_text(source: str, start: int) -> str:
        """The whole call, paren-balanced.

        A naive ``[^)]*?\\)`` stops at the first inner ``)``, which here is
        ``datasets.get("_production_format")`` — hiding the very argument this
        test exists to look for.
        """
        depth = 0
        for i in range(start, len(source)):
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
                if depth == 0:
                    return source[start:i + 1]
        return source[start:]

    @pytest.mark.parametrize("path", MODULES)
    def test_no_call_site_omits_project_facts(self, path):
        source = open(path, encoding="utf-8").read()
        offenders = []
        for match in self.CALL_START.finditer(source):
            call = self._call_text(source, match.start())
            if "def " in call:
                continue
            # A call carrying the facts mentions them by name or by variable.
            if "project_facts" in call:
                continue
            offenders.append(" ".join(call.split()))
        assert not offenders, (
            f"{path}: programme selection without project facts — this is the "
            f"FIX-01 UK rate mismatch reappearing:\n  " + "\n  ".join(offenders)
        )


class TestCanonicalProjectFacts:
    def test_the_service_builds_one_facts_dict(self):
        facts = ReportService._build_project_facts(
            {
                "_production_format": "Short",
                "_budget_gbp": {"converted": 45_730},
                "_runtime_minutes": 12,
                "_completion_date": "2026-09-10",
                "_producer_iso": "ZA",
            },
            {},
        )
        assert facts["budget_gbp"] == 45_730
        assert facts["format"] == "Short"
        assert facts["producer_iso"] == "ZA"

    def test_a_fact_the_platform_does_not_hold_stays_absent(self):
        """Never inferred. A gate that cannot be tested must report untested
        rather than be settled against a guess."""
        facts = ReportService._build_project_facts({}, {})
        assert facts["budget_gbp"] is None
        assert facts["completion_date"] is None
        assert facts["producer_iso"] is None

    def test_the_builder_prefers_the_canonical_facts(self):
        from app.modules.reports.builder import ReportBuilder

        canonical = {"format": "Short", "budget_gbp": 1, "marker": "canonical"}
        b = ReportBuilder(
            {"_project_facts": canonical, "_production_format": "Feature Film"},
            {},
        )
        assert b._project_facts is canonical

    def test_the_builder_still_works_without_them(self):
        """Tests and the sample report construct a builder directly."""
        from app.modules.reports.builder import ReportBuilder

        b = ReportBuilder({"_production_format": "Short"}, {})
        assert b._project_facts["format"] == "Short"


class TestRenderersDoNotRecompute:
    def test_the_pdf_service_takes_a_report_and_renders_it(self):
        """The PDF renderer's contract: it receives the computed object. If it
        ever grows a scoring or rebate argument, that is a second path."""
        from app.modules.reports.pdf_service import PDFService

        params = inspect.signature(PDFService.render_report_html).parameters
        assert "report_data" in params
        forbidden = {"incentives", "datasets", "budget_gbp", "project_facts"}
        assert not (forbidden & set(params)), (
            "the PDF renderer is being handed raw inputs it could compute from"
        )
