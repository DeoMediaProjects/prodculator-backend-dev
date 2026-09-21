"""Section 09 — Industry Development & Market Strategy.

The Markets/Labs/WIP engine has been frozen and developer-ready since its
handoff and had no section to render into, so 203 staged tracks reached no
producer. The regression report lists this as section 09 of 13; the live
report went straight from Grants to Comparables.

What these pin is mostly about honesty of absence. Most of a labs calendar is
unannounced at any moment, so "twelve known, none currently open" is the
commonest true thing this section can say, and it must be able to say it
without looking like a failure.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.modules.reports.builder import ReportBuilder


def _recommendation(name: str, *, eligibility="POTENTIALLY_ELIGIBLE",
                    state="DATED", deadline=None, status="OPEN"):
    from datetime import date

    opportunity = SimpleNamespace(
        name=name,
        cycle_state=state,
        cycle_deadline=deadline,
        source_url="https://market.example.org/call",
        verified_on=date(2026, 9, 18),
    )
    return SimpleNamespace(
        opportunity=opportunity,
        eligibility=eligibility,
        opportunity_class="COPRODUCTION_MARKET",
        sequence="APPLY_NOW",
        sequence_label="Apply now",
        sequence_reason="The call is open.",
        recommendation=SimpleNamespace(
            opportunity=opportunity,
            application_status=status,
            conditions_to_confirm=("Confirm the project stage.",),
        ),
    )


def _markets(recommendations=(), **counts):
    return SimpleNamespace(
        universe_count=counts.get("universe", len(recommendations)),
        actionable_count=counts.get("actionable", len(recommendations)),
        eligible_count=counts.get("eligible", 0),
        potential_count=counts.get("potential", len(recommendations)),
        recommendations=tuple(recommendations),
        is_committed_finance=False,
    )


def _builder(markets) -> ReportBuilder:
    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = []
    instance.request_metadata = {"_package": "producer"}
    instance._opportunity_cache = SimpleNamespace(markets=markets, festivals=None)
    return instance


class TestTheSection:
    def test_it_renders_the_engine_ranked_opportunities(self):
        from datetime import date

        section = _builder(_markets([
            _recommendation("HAF — Work in Progress", deadline=date(2026, 12, 16)),
        ]))._build_markets_labs_wip()
        assert [o["name"] for o in section["opportunities"]] == [
            "HAF — Work in Progress"
        ]
        assert section["opportunities"][0]["deadline"] == "2026-12-16"

    def test_a_rolling_call_is_not_described_as_open(self):
        # A producer reading "open" reasonably asks until when. A rolling call
        # has no until, and flattening the two loses the only thing that
        # separates them.
        section = _builder(_markets([
            _recommendation("Rolling Lab", state="ROLLING", status="ROLLING"),
        ]))._build_markets_labs_wip()
        entry = section["opportunities"][0]
        assert entry["cycleState"] == "ROLLING"
        assert entry["applicationStatus"] == "ROLLING"
        assert entry["deadline"] is None

    def test_it_carries_what_is_still_unresolved(self):
        section = _builder(_markets([_recommendation("Some Market")]))._build_markets_labs_wip()
        assert section["opportunities"][0]["conditionsToConfirm"] == [
            "Confirm the project stage."
        ]


class TestHonestAbsence:
    def test_an_empty_universe_is_still_a_section(self):
        # "Nothing is open" is a true and useful answer, and it is what most
        # of a labs calendar says at any given moment. Rendering no section at
        # all would leave the reader to guess whether anyone looked.
        section = _builder(_markets([], universe=132, actionable=0, potential=0))
        result = section._build_markets_labs_wip()
        assert result["universeCount"] == 132
        assert result["actionableCount"] == 0
        assert result["opportunities"] == []

    def test_an_engine_that_could_not_run_renders_nothing(self):
        # Different from running and finding nothing. A section header over a
        # failed load tells a reader less than no section.
        instance = ReportBuilder.__new__(ReportBuilder)
        instance.warnings = []
        instance.request_metadata = {}
        instance._opportunity_cache = False
        assert instance._build_markets_labs_wip() is None

    def test_no_projectfacts_snapshot_means_no_section(self):
        # Locked decision I.1: every engine consumes the same snapshot.
        instance = ReportBuilder.__new__(ReportBuilder)
        instance.warnings = []
        instance.request_metadata = {}
        assert instance._opportunity_strategies() is None


class TestItIsNotFinance:
    def test_the_section_says_so_itself(self):
        # Locked decision A.7, beside a Financial Readiness section that
        # totals what the report presents as money. Market access is an
        # opportunity to meet financiers, not financing.
        section = _builder(_markets([_recommendation("Some Market")]))._build_markets_labs_wip()
        assert section["isCommittedFinance"] is False

    def test_no_opportunity_carries_an_amount(self):
        section = _builder(_markets([_recommendation("Some Market")]))._build_markets_labs_wip()
        assert not any(
            key.lower().endswith(("amount", "value", "funding"))
            for key in section["opportunities"][0]
        )
