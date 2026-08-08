"""Unit tests for the deterministic financial-readiness assessment (§4.1).

The contract these tests defend: every figure traces to a named input, every
status follows from stated checks, and the verdict follows a fixed rule order
that no amount of favourable narrative can move. Nothing in the section is AI
generated, so the same inputs must always produce the same verdict.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.modules.reports.builder import ReportBuilder
from app.modules.reports.readiness import (
    CASH_GAP_WARN_WEEKS,
    CONDITIONAL_MIN_SCORE,
    MIN_POST_WEEKS,
    READY_MIN_SCORE,
    SOFT_MONEY_MATERIAL_PCT,
    STATUS_POINTS,
    VERDICT_CONDITIONAL,
    VERDICT_INSUFFICIENT,
    VERDICT_NOT_READY,
    VERDICT_READY,
    WEIGHTS,
    compute_financial_readiness,
)

TODAY = date(2026, 8, 6)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _scenario(
    territory: str = "United Kingdom",
    total: float = 30_000_000,
    net_rebate: float = 5_737_500,
    programme: str = "AVEC",
) -> dict:
    return {
        "territory": territory,
        "programme": programme,
        "currencySymbol": "£",
        "totalBudgetValue": total,
        "netRebateValue": net_rebate,
        "qualifyingSpendValue": total * 0.8,
    }


def _estimate(
    territory: str = "United Kingdom",
    program: str = "AVEC",
    *,
    bankability: str | None = "BANKABLE",
    eligibility: str | None = "qualified",
    last_updated: str | None = "2026-07-01",
    staleness: str | None = None,
    expiry: str | None = None,
) -> dict:
    est: dict = {"territory": territory, "program": program}
    if bankability is not None:
        est["bankabilityLabel"] = bankability
    if eligibility is not None:
        est["eligibilityStatus"] = eligibility
    if last_updated is not None:
        est["lastUpdated"] = last_updated
    if staleness is not None:
        est["stalenessWarning"] = staleness
    if expiry is not None:
        est["expiryDate"] = expiry
    return est


def _timing(
    territory: str = "United Kingdom",
    total_max: float | None = 20.0,
    source_quality: str | None = "official",
) -> dict:
    return {
        "territory": territory,
        "totalWeeksMin": 8.0,
        "totalWeeksMax": total_max,
        "sourceQuality": source_quality,
        "suspended": False,
    }


def _comparables(count: int = 4, budget_usd: int = 38_000_000) -> list[dict]:
    return [
        {
            "title": f"Comparable {i}",
            "location": "London, United Kingdom",
            "budgetUSD": budget_usd,
            "year": 2024,
        }
        for i in range(count)
    ]


def _funds(max_amount: float | None = 3_000_000, deadline: str = "2026-11-30") -> list[dict]:
    return [{
        "type": "Fund",
        "name": "BFI Filmmaking Fund",
        "deadline": deadline,
        "notes": "Up to £3M",
    }]


def _grant_rows(max_amount: float | None = 3_000_000, currency: str = "GBP") -> list[dict]:
    return [{
        "title": "BFI Filmmaking Fund",
        "max_amount": max_amount,
        "currency": currency,
    }]


def _report(
    *,
    scenarios: list[dict] | None = None,
    estimates: list[dict] | None = None,
    timing: list[dict] | None = None,
    comparables: list[dict] | None = None,
    funding: list[dict] | None = None,
    territory: str = "United Kingdom",
) -> dict:
    return {
        "executiveSummary": {"recommendedTerritory": territory},
        "locationRankings": [{"name": territory, "score": 88}],
        "financialAnalysis": {
            "budgetScenarios": scenarios if scenarios is not None else [_scenario(territory)],
            "paymentTiming": timing if timing is not None else [_timing(territory)],
        },
        "incentiveEstimates": estimates if estimates is not None else [_estimate(territory)],
        "comparables": comparables if comparables is not None else _comparables(),
        "fundingOpportunities": funding if funding is not None else _funds(),
    }


def _datasets(
    *,
    budget_gbp: float | None = 30_000_000,
    budget_currency: str = "GBP",
    qualifying_spend_min: float | None = None,
    qualifying_spend_currency: str = "GBP",
    cost_efficiency_score: int | None = 62,
    grants: list[dict] | None = None,
    suspended: bool = False,
    last_reviewed: str | None = "2026-06-01",
    territory: str = "United Kingdom",
    programme: str = "AVEC",
) -> dict:
    return {
        "incentives": [{
            "territory": territory,
            "program": programme,
            "qualifying_spend_min": qualifying_spend_min,
            "qualifying_spend_currency": qualifying_spend_currency,
            "currency": "GBP",
        }],
        "grants": grants if grants is not None else _grant_rows(),
        "_budget_gbp": {"converted": budget_gbp, "rate_date": "2026-08-05"} if budget_gbp else None,
        "_budget_amount": budget_gbp,
        "_budget_currency": budget_currency,
        "_territory_profiles": {
            territory: {
                "territory": territory,
                "cost_efficiency_score": cost_efficiency_score,
                "cost_efficiency_source": "Curated 2026 review" if cost_efficiency_score else None,
                "bankability_suspended": suspended,
                "bankability_source_quality": "official",
                "last_reviewed_at": last_reviewed,
            }
        },
    }


def _metadata(
    *,
    completion_date: str | None = "2027-06-30",
    filming_start_date: str | None = "2026-10-01",
    filming_duration: int | None = 8,
    fmt: str = "Feature Film",
) -> dict:
    return {
        "completion_date": completion_date,
        "filming_start_date": filming_start_date,
        "filming_duration": filming_duration,
        "format": fmt,
    }


def _assess(report: dict, datasets: dict, metadata: dict) -> dict:
    result = compute_financial_readiness(
        report=report, datasets=datasets, request_metadata=metadata, today=TODAY,
    )
    assert result is not None
    return result


def _component(section: dict, key: str) -> dict:
    return next(c for c in section["components"] if c["key"] == key)


# ── Rubric integrity ────────────────────────────────────────────────────────


class TestRubric:
    def test_weights_sum_to_one_hundred(self):
        assert sum(WEIGHTS.values()) == 100

    def test_status_points_are_ordered(self):
        assert (
            STATUS_POINTS["ready"]
            > STATUS_POINTS["conditional"]
            > STATUS_POINTS["insufficient_data"]
            > STATUS_POINTS["not_ready"]
        )

    def test_score_is_the_weighted_sum_of_component_statuses(self):
        section = _assess(_report(), _datasets(), _metadata())
        expected = round(
            sum(STATUS_POINTS[c["status"]] * c["weight"] for c in section["components"])
            / sum(c["weight"] for c in section["components"])
        )
        assert section["score"] == expected

    def test_every_component_is_present_exactly_once(self):
        section = _assess(_report(), _datasets(), _metadata())
        keys = [c["key"] for c in section["components"]]
        assert sorted(keys) == sorted(WEIGHTS)

    def test_verdict_names_the_rule_that_produced_it(self):
        section = _assess(_report(), _datasets(), _metadata())
        assert section["rule"].startswith("R")
        assert section["verdictReason"]

    def test_assessment_is_deterministic(self):
        args = (_report(), _datasets(), _metadata())
        first = _assess(*args)
        second = _assess(*args)
        assert first == second


class TestTraceability:
    def test_every_figure_cites_its_basis(self):
        section = _assess(_report(), _datasets(), _metadata())
        figures = [f for c in section["components"] for f in c["figures"]]
        assert figures
        for figure in figures:
            assert figure["basis"].strip(), figure

    def test_every_check_states_what_it_compared(self):
        section = _assess(_report(), _datasets(), _metadata())
        checks = [chk for c in section["components"] for chk in c["checks"]]
        assert checks
        for check in checks:
            assert check["result"] in {"pass", "fail", "warn", "skipped"}
            assert check["detail"].strip(), check

    def test_every_flag_names_an_input_and_an_action(self):
        # A production with several missing inputs, so flags are definitely raised.
        section = _assess(
            _report(estimates=[_estimate(last_updated=None, eligibility=None)]),
            _datasets(cost_efficiency_score=None),
            _metadata(completion_date=None),
        )
        assert section["flags"]
        for flag in section["flags"]:
            assert flag["severity"] in {"critical", "warning", "info"}
            assert flag["input"].strip()
            assert flag["action"].strip()

    def test_flag_counts_match_the_flag_list(self):
        section = _assess(
            _report(estimates=[_estimate(last_updated=None)]),
            _datasets(cost_efficiency_score=None),
            _metadata(),
        )
        for severity, count in section["flagCounts"].items():
            assert count == sum(1 for f in section["flags"] if f["severity"] == severity)

    def test_flags_are_ordered_most_severe_first(self):
        section = _assess(
            _report(estimates=[_estimate(last_updated=None)]),
            _datasets(budget_currency="OTHER", cost_efficiency_score=None),
            _metadata(),
        )
        rank = {"critical": 0, "warning": 1, "info": 2}
        severities = [rank[f["severity"]] for f in section["flags"]]
        assert severities == sorted(severities)


# ── Component 1 — budget vs. cost base ──────────────────────────────────────


class TestBudgetVsCostBase:
    def test_budget_below_minimum_qualifying_spend_fails_hard(self):
        section = _assess(
            _report(),
            _datasets(budget_gbp=1_000_000, qualifying_spend_min=5_000_000),
            _metadata(),
        )
        component = _component(section, "budget_vs_cost_base")
        assert component["status"] == "not_ready"
        assert section["verdict"] == VERDICT_NOT_READY
        assert section["rule"].startswith("R1")

    def test_budget_clearing_the_minimum_passes(self):
        section = _assess(
            _report(),
            _datasets(budget_gbp=30_000_000, qualifying_spend_min=5_000_000),
            _metadata(),
        )
        component = _component(section, "budget_vs_cost_base")
        min_check = next(
            c for c in component["checks"] if c["name"] == "minimum qualifying spend"
        )
        assert min_check["result"] == "pass"

    def test_minimum_spend_is_converted_before_comparison(self):
        # A 25,000,000 ZAR floor is ~£1.05M, which a £30M budget clears. Comparing
        # the raw numbers instead would wrongly fail the production.
        section = _assess(
            _report(),
            _datasets(
                budget_gbp=30_000_000,
                qualifying_spend_min=25_000_000,
                qualifying_spend_currency="ZAR",
            ),
            _metadata(),
        )
        component = _component(section, "budget_vs_cost_base")
        min_check = next(
            c for c in component["checks"] if c["name"] == "minimum qualifying spend"
        )
        assert min_check["result"] == "pass"
        assert "ZAR" in min_check["detail"]

    def test_unconvertible_minimum_spend_is_flagged_not_guessed(self):
        section = _assess(
            _report(),
            _datasets(qualifying_spend_min=1_000_000, qualifying_spend_currency="XYZ"),
            _metadata(),
        )
        component = _component(section, "budget_vs_cost_base")
        min_check = next(
            c for c in component["checks"] if c["name"] == "minimum qualifying spend"
        )
        assert min_check["result"] == "skipped"
        assert any("XYZ" in f["input"] for f in section["flags"])

    def test_budget_far_below_comparable_median_is_conditional(self):
        section = _assess(
            _report(
                scenarios=[_scenario(total=3_000_000, net_rebate=500_000)],
                comparables=_comparables(budget_usd=40_000_000),
            ),
            _datasets(budget_gbp=3_000_000),
            _metadata(),
        )
        component = _component(section, "budget_vs_cost_base")
        anchor = next(
            c for c in component["checks"] if c["name"] == "comparable cost anchor"
        )
        assert anchor["result"] == "fail"
        assert component["status"] == "conditional"

    def test_budget_above_the_band_is_not_penalised(self):
        section = _assess(
            _report(comparables=_comparables(budget_usd=2_000_000)),
            _datasets(),
            _metadata(),
        )
        component = _component(section, "budget_vs_cost_base")
        anchor = next(
            c for c in component["checks"] if c["name"] == "comparable cost anchor"
        )
        assert anchor["result"] == "pass"
        assert component["status"] == "ready"

    def test_too_few_comparables_yields_insufficient_data_not_a_guess(self):
        section = _assess(
            _report(comparables=_comparables(count=1)),
            _datasets(),
            _metadata(),
        )
        component = _component(section, "budget_vs_cost_base")
        anchor = next(
            c for c in component["checks"] if c["name"] == "comparable cost anchor"
        )
        assert anchor["result"] == "skipped"
        assert component["status"] == "insufficient_data"

    def test_missing_cost_efficiency_score_is_flagged(self):
        section = _assess(_report(), _datasets(cost_efficiency_score=None), _metadata())
        assert any("cost_efficiency_score" in f["input"] for f in section["flags"])

    def test_unnormalisable_budget_cannot_be_assessed(self):
        section = _assess(_report(), _datasets(budget_gbp=None), _metadata())
        component = _component(section, "budget_vs_cost_base")
        assert component["status"] == "insufficient_data"


# ── Component 2 — confirmed vs. estimated incentive ─────────────────────────


class TestIncentiveConfidence:
    def test_qualified_bankable_fresh_incentive_is_confirmed(self):
        section = _assess(_report(), _datasets(), _metadata())
        component = _component(section, "incentive_confidence")
        assert component["grade"] == "confirmed"
        assert component["status"] == "ready"

    def test_confirmed_value_is_zero_when_the_incentive_is_not_confirmed(self):
        section = _assess(
            _report(estimates=[_estimate(bankability="VERIFY FIRST")]),
            _datasets(),
            _metadata(),
        )
        component = _component(section, "incentive_confidence")
        confirmed = next(
            f for f in component["figures"] if f["label"] == "Confirmed incentive value"
        )
        assert confirmed["value"] == "£0"
        assert component["grade"] == "contingent"

    def test_unknown_eligibility_makes_the_value_contingent(self):
        section = _assess(
            _report(estimates=[_estimate(eligibility="unknown")]),
            _datasets(),
            _metadata(),
        )
        assert _component(section, "incentive_confidence")["grade"] == "contingent"

    @pytest.mark.parametrize("status", ["requires_co_production", "requires_spv"])
    def test_structure_dependent_eligibility_is_contingent(self, status):
        section = _assess(
            _report(estimates=[_estimate(eligibility=status)]),
            _datasets(),
            _metadata(),
        )
        assert _component(section, "incentive_confidence")["grade"] == "contingent"

    def test_ineligible_producer_fails_the_component(self):
        section = _assess(
            _report(estimates=[_estimate(eligibility="ineligible")]),
            _datasets(),
            _metadata(),
        )
        component = _component(section, "incentive_confidence")
        assert component["grade"] == "failed"
        assert component["status"] == "not_ready"
        assert section["verdict"] == VERDICT_NOT_READY

    def test_unbankable_incentive_fails_the_component(self):
        section = _assess(
            _report(estimates=[_estimate(bankability="NOT BANKABLE")]),
            _datasets(),
            _metadata(),
        )
        assert _component(section, "incentive_confidence")["status"] == "not_ready"

    def test_stale_record_downgrades_and_flags(self):
        section = _assess(
            _report(estimates=[_estimate(staleness="Incentive data may be outdated")]),
            _datasets(),
            _metadata(),
        )
        assert _component(section, "incentive_confidence")["grade"] == "contingent"
        assert any("incentive_programs" in f["input"] for f in section["flags"])

    def test_missing_verification_date_downgrades_and_flags(self):
        section = _assess(
            _report(estimates=[_estimate(last_updated=None)]),
            _datasets(),
            _metadata(),
        )
        assert _component(section, "incentive_confidence")["grade"] == "contingent"
        assert any("last_verified_at" in f["input"] for f in section["flags"])

    def test_programme_expiring_before_delivery_fails(self):
        section = _assess(
            _report(estimates=[_estimate(expiry="2027-01-01")]),
            _datasets(),
            _metadata(completion_date="2027-06-30"),
        )
        component = _component(section, "incentive_confidence")
        assert component["grade"] == "failed"
        assert any("expires" in c["detail"] for c in component["checks"])

    def test_programme_expiring_after_delivery_is_fine(self):
        section = _assess(
            _report(estimates=[_estimate(expiry="2028-01-01")]),
            _datasets(),
            _metadata(completion_date="2027-06-30"),
        )
        assert _component(section, "incentive_confidence")["grade"] == "confirmed"

    def test_suspended_territory_fails_and_raises_a_critical_flag(self):
        section = _assess(_report(), _datasets(suspended=True), _metadata())
        assert _component(section, "incentive_confidence")["status"] == "not_ready"
        assert any(
            f["severity"] == "critical" and "suspended" in f["input"]
            for f in section["flags"]
        )

    def test_informational_programme_carries_no_assessable_value(self):
        section = _assess(
            _report(estimates=[_estimate(bankability="INFORMATIONAL")]),
            _datasets(),
            _metadata(),
        )
        assert _component(section, "incentive_confidence")["grade"] == "failed"

    def test_coverage_percentage_divides_two_figures_in_one_currency(self):
        section = _assess(
            _report(scenarios=[_scenario(total=30_000_000, net_rebate=6_000_000)]),
            _datasets(),
            _metadata(),
        )
        component = _component(section, "incentive_confidence")
        share = next(
            f for f in component["figures"] if f["label"] == "As a share of budget"
        )
        assert share["value"] == "20.0%"

    def test_missing_estimate_for_the_recommended_territory_is_flagged_critical(self):
        section = _assess(_report(estimates=[]), _datasets(), _metadata())
        component = _component(section, "incentive_confidence")
        assert component["status"] == "insufficient_data"
        assert any(f["severity"] == "critical" for f in section["flags"])

    def test_confirmed_alternatives_are_surfaced(self):
        section = _assess(
            _report(estimates=[
                _estimate(bankability="VERIFY FIRST"),
                _estimate(territory="Hungary", program="NFI"),
            ]),
            _datasets(),
            _metadata(),
        )
        component = _component(section, "incentive_confidence")
        assert any(
            c["name"] == "confirmed alternatives" and "Hungary" in c["detail"]
            for c in component["checks"]
        )


# ── Component 3 — soft money ────────────────────────────────────────────────


class TestSoftMoney:
    def test_material_soft_money_with_an_open_window_is_ready(self):
        section = _assess(
            _report(funding=_funds(deadline="2026-11-30")),
            _datasets(grants=_grant_rows(max_amount=3_000_000)),
            _metadata(),
        )
        component = _component(section, "soft_money_coverage")
        assert component["status"] == "ready"
        share = next(
            f for f in component["figures"]
            if f["label"] == "Soft money as a share of budget"
        )
        assert share["value"] == "10.0%"

    def test_immaterial_soft_money_is_conditional_not_failed(self):
        section = _assess(
            _report(),
            _datasets(grants=_grant_rows(max_amount=100_000)),
            _metadata(),
        )
        component = _component(section, "soft_money_coverage")
        assert component["status"] == "conditional"
        assert f"{SOFT_MONEY_MATERIAL_PCT:g}%" in component["headline"]

    def test_closed_windows_downgrade_material_soft_money(self):
        section = _assess(
            _report(funding=_funds(deadline="2026-01-01")),
            _datasets(grants=_grant_rows(max_amount=3_000_000)),
            _metadata(),
        )
        assert _component(section, "soft_money_coverage")["status"] == "conditional"

    def test_no_matched_funds_is_insufficient_data_never_not_ready(self):
        section = _assess(_report(funding=[]), _datasets(grants=[]), _metadata())
        component = _component(section, "soft_money_coverage")
        assert component["status"] == "insufficient_data"

    def test_soft_money_never_returns_not_ready(self):
        for grants, funding in (
            ([], []),
            (_grant_rows(max_amount=None), _funds()),
            (_grant_rows(max_amount=1), _funds(deadline="2020-01-01")),
        ):
            section = _assess(
                _report(funding=funding), _datasets(grants=grants), _metadata(),
            )
            assert _component(section, "soft_money_coverage")["status"] != "not_ready"

    def test_unquantified_funds_are_excluded_and_flagged(self):
        section = _assess(
            _report(),
            _datasets(grants=[{"title": "BFI Filmmaking Fund", "max_amount": None}]),
            _metadata(),
        )
        component = _component(section, "soft_money_coverage")
        assert any(
            c["name"] == "unquantified soft money" for c in component["checks"]
        )
        assert any("grants.max_amount" in f["input"] for f in section["flags"])

    def test_foreign_currency_awards_are_converted(self):
        # 3M EUR is ~£2.56M on the static table, so ~8.5% of a £30M budget —
        # counting the raw 3,000,000 as GBP would overstate coverage.
        section = _assess(
            _report(),
            _datasets(grants=_grant_rows(max_amount=3_000_000, currency="EUR")),
            _metadata(),
        )
        component = _component(section, "soft_money_coverage")
        share = next(
            f for f in component["figures"]
            if f["label"] == "Soft money as a share of budget"
        )
        assert share["value"] == "8.5%"

    def test_residual_is_the_complement_of_identified_coverage(self):
        section = _assess(
            _report(scenarios=[_scenario(total=30_000_000, net_rebate=6_000_000)]),
            _datasets(grants=_grant_rows(max_amount=3_000_000)),
            _metadata(),
        )
        component = _component(section, "soft_money_coverage")
        residual = next(
            f for f in component["figures"]
            if f["label"] == "Residual for equity or debt"
        )
        # 100% - 10% soft - 20% incentive
        assert residual["value"] == "70.0%"

    def test_note_states_that_soft_money_is_not_committed(self):
        section = _assess(_report(), _datasets(), _metadata())
        component = _component(section, "soft_money_coverage")
        assert "not committed funds" in component["note"]


# ── Component 4 — timeline ──────────────────────────────────────────────────


class TestTimeline:
    def test_consistent_schedule_is_ready(self):
        section = _assess(_report(), _datasets(), _metadata())
        assert _component(section, "timeline_feasibility")["status"] == "ready"

    def test_completion_before_photography_ends_fails(self):
        section = _assess(
            _report(),
            _datasets(),
            _metadata(
                filming_start_date="2026-10-01",
                filming_duration=8,
                completion_date="2026-10-15",
            ),
        )
        component = _component(section, "timeline_feasibility")
        assert component["status"] == "not_ready"
        assert section["verdict"] == VERDICT_NOT_READY

    def test_post_window_shorter_than_the_floor_is_conditional(self):
        # 8-week shoot from 1 Oct 2026 ends 26 Nov; a Feature Film floor of
        # MIN_POST_WEEKS weeks pushes the earliest feasible completion past a
        # completion date only 4 weeks later.
        section = _assess(
            _report(),
            _datasets(),
            _metadata(
                filming_start_date="2026-10-01",
                filming_duration=8,
                completion_date="2026-12-24",
            ),
        )
        component = _component(section, "timeline_feasibility")
        post = next(
            c for c in component["checks"] if c["name"] == "post-production window"
        )
        assert post["result"] == "fail"
        assert str(MIN_POST_WEEKS["Feature Film"]) in post["detail"]

    def test_post_floor_is_format_specific(self):
        metadata = _metadata(
            filming_start_date="2026-10-01",
            filming_duration=8,
            completion_date="2027-04-01",
            fmt="Animated Feature",
        )
        section = _assess(_report(), _datasets(), metadata)
        component = _component(section, "timeline_feasibility")
        post = next(
            c for c in component["checks"] if c["name"] == "post-production window"
        )
        # 40-week animation floor is not met by an April completion.
        assert post["result"] == "fail"

    def test_post_floor_is_labelled_as_an_assumption(self):
        section = _assess(_report(), _datasets(), _metadata())
        component = _component(section, "timeline_feasibility")
        earliest = next(
            f for f in component["figures"]
            if f["label"] == "Earliest feasible completion"
        )
        assert "modelling assumption" in earliest["basis"]

    def test_long_cash_gap_requires_interim_financing(self):
        section = _assess(
            _report(timing=[_timing(total_max=CASH_GAP_WARN_WEEKS + 10)]),
            _datasets(),
            _metadata(),
        )
        component = _component(section, "timeline_feasibility")
        gap = next(
            c for c in component["checks"] if c["name"] == "incentive cash-flow gap"
        )
        assert gap["result"] == "fail"
        assert component["status"] == "conditional"

    def test_expected_receipt_date_is_derived_from_the_window(self):
        section = _assess(
            _report(timing=[_timing(total_max=20)]),
            _datasets(),
            _metadata(completion_date="2027-06-30"),
        )
        component = _component(section, "timeline_feasibility")
        receipt = next(
            f for f in component["figures"]
            if f["label"] == "Latest expected incentive receipt"
        )
        assert receipt["value"] == (date(2027, 6, 30) + timedelta(weeks=20)).isoformat()

    def test_missing_payment_window_is_flagged(self):
        section = _assess(_report(timing=[]), _datasets(), _metadata())
        component = _component(section, "timeline_feasibility")
        gap = next(
            c for c in component["checks"] if c["name"] == "incentive cash-flow gap"
        )
        assert gap["result"] == "skipped"
        assert any("payment_weeks_max" in f["input"] for f in section["flags"])

    def test_missing_completion_date_is_flagged_and_unassessable(self):
        section = _assess(_report(), _datasets(), _metadata(completion_date=None))
        component = _component(section, "timeline_feasibility")
        assert component["status"] == "insufficient_data"
        assert any(f["input"] == "completion_date" for f in section["flags"])

    def test_reachable_festival_deadline_passes(self):
        section = _assess(
            _report(funding=_funds() + [{
                "type": "Festival", "name": "BFI London", "deadline": "2027-08-01",
            }]),
            _datasets(),
            _metadata(completion_date="2027-06-30"),
        )
        component = _component(section, "timeline_feasibility")
        window = next(c for c in component["checks"] if c["name"] == "festival window")
        assert window["result"] == "pass"

    def test_all_festival_deadlines_before_delivery_flags_a_missed_cycle(self):
        section = _assess(
            _report(funding=_funds() + [{
                "type": "Festival", "name": "BFI London", "deadline": "2027-01-15",
            }]),
            _datasets(),
            _metadata(completion_date="2027-06-30"),
        )
        component = _component(section, "timeline_feasibility")
        window = next(c for c in component["checks"] if c["name"] == "festival window")
        assert window["result"] == "fail"
        assert "first festival cycle" in component["headline"]

    def test_undated_festivals_are_skipped_not_assumed_reachable(self):
        report = _report(funding=_funds() + [{
            "type": "Festival", "name": "Series Mania", "deadline": "Autumn",
        }])
        report["festivalRecommendations"] = [{"name": "Series Mania"}]
        section = _assess(report, _datasets(), _metadata())
        component = _component(section, "timeline_feasibility")
        window = next(c for c in component["checks"] if c["name"] == "festival window")
        assert window["result"] == "skipped"
        assert any("submission_deadline" in f["input"] for f in section["flags"])


# ── Verdict rules ───────────────────────────────────────────────────────────


class TestVerdictRules:
    def test_all_components_ready_gives_ready(self):
        section = _assess(_report(), _datasets(), _metadata())
        assert section["verdict"] == VERDICT_READY
        assert section["score"] >= READY_MIN_SCORE

    def test_a_failed_component_beats_a_high_score(self):
        # Everything else is ready, so the weighted score stays high; the failed
        # component must still be decisive.
        section = _assess(
            _report(estimates=[_estimate(eligibility="ineligible")]),
            _datasets(),
            _metadata(),
        )
        assert section["verdict"] == VERDICT_NOT_READY
        assert section["rule"].startswith("R1")

    def test_two_unassessable_components_give_insufficient_data(self):
        section = _assess(
            _report(estimates=[], comparables=_comparables(count=0)),
            _datasets(),
            _metadata(),
        )
        assert section["verdict"] == VERDICT_INSUFFICIENT
        assert section["rule"].startswith("R2")

    def test_a_critical_flag_caps_the_verdict_at_conditional(self):
        section = _assess(_report(), _datasets(budget_currency="OTHER"), _metadata())
        assert any(f["severity"] == "critical" for f in section["flags"])
        assert section["verdict"] == VERDICT_CONDITIONAL
        assert section["rule"].startswith("R3")

    def test_an_estimated_incentive_can_never_be_ready(self):
        # The weighted score still clears READY_MIN_SCORE here. An unconfirmed
        # incentive must block the verdict anyway: over-claiming on estimated
        # incentive value is the specific failure this section exists to prevent.
        section = _assess(
            _report(estimates=[_estimate(bankability="VERIFY FIRST", eligibility="unknown")]),
            _datasets(grants=_grant_rows(max_amount=3_000_000)),
            _metadata(),
        )
        assert section["score"] >= READY_MIN_SCORE
        assert section["verdict"] == VERDICT_CONDITIONAL
        assert section["rule"].startswith("R3")
        assert "estimated rather than confirmed" in section["verdictReason"]

    def test_a_single_unassessable_component_blocks_ready(self):
        section = _assess(
            _report(comparables=_comparables(count=1)),
            _datasets(),
            _metadata(),
        )
        assert section["verdict"] == VERDICT_CONDITIONAL
        assert "could not be assessed" in section["verdictReason"]

    def test_middling_score_gives_conditional(self):
        section = _assess(
            _report(estimates=[_estimate(bankability="NOT BANKABLE")]),
            _datasets(grants=_grant_rows(max_amount=50_000)),
            _metadata(completion_date=None),
        )
        # A failed component is decisive, so force the middling path instead by
        # checking the score band directly on a report with no hard failure.
        soft = _assess(
            _report(estimates=[_estimate(bankability="VERIFY FIRST", eligibility="unknown")]),
            _datasets(grants=_grant_rows(max_amount=50_000)),
            _metadata(completion_date=None),
        )
        assert section["verdict"] == VERDICT_NOT_READY
        assert soft["verdict"] == VERDICT_CONDITIONAL
        assert CONDITIONAL_MIN_SCORE <= soft["score"] < READY_MIN_SCORE

    def test_verdict_is_one_of_the_four_declared_values(self):
        section = _assess(_report(), _datasets(), _metadata())
        assert section["verdict"] in {
            VERDICT_READY, VERDICT_CONDITIONAL, VERDICT_NOT_READY, VERDICT_INSUFFICIENT,
        }


# ── Input-provenance flags ──────────────────────────────────────────────────


class TestInputFlags:
    def test_other_currency_is_critical(self):
        section = _assess(_report(), _datasets(budget_currency="OTHER"), _metadata())
        assert any(
            f["severity"] == "critical" and f["input"] == "budget_currency"
            for f in section["flags"]
        )

    def test_currency_without_a_fallback_rate_is_flagged(self):
        section = _assess(_report(), _datasets(budget_currency="MAD"), _metadata())
        assert any("MAD" in f["input"] for f in section["flags"])

    def test_stale_territory_profile_is_flagged(self):
        section = _assess(
            _report(), _datasets(last_reviewed="2024-01-01"), _metadata(),
        )
        assert any("last_reviewed_at" in f["input"] for f in section["flags"])

    def test_fresh_territory_profile_is_not_flagged_as_stale(self):
        section = _assess(_report(), _datasets(last_reviewed="2026-06-01"), _metadata())
        assert not any(
            "last_reviewed_at" in f["input"] and f["severity"] == "warning"
            for f in section["flags"]
        )

    def test_stale_alternatives_are_reported_separately(self):
        section = _assess(
            _report(estimates=[
                _estimate(),
                _estimate(territory="Hungary", program="NFI", staleness="outdated"),
            ]),
            _datasets(),
            _metadata(),
        )
        assert any(
            "other ranked territories" in f["input"] and "Hungary" in f["detail"]
            for f in section["flags"]
        )


# ── Guard rails ─────────────────────────────────────────────────────────────


class TestGuards:
    def test_no_financial_basis_returns_none(self):
        report = _report(scenarios=[])
        assert compute_financial_readiness(
            report=report, datasets=_datasets(), request_metadata=_metadata(),
            today=TODAY,
        ) is None

    def test_no_recommended_territory_returns_none(self):
        report = _report()
        report["executiveSummary"] = {}
        report["locationRankings"] = []
        report["financialAnalysis"]["budgetScenarios"] = []
        assert compute_financial_readiness(
            report=report, datasets=_datasets(), request_metadata=_metadata(),
            today=TODAY,
        ) is None

    def test_missing_metadata_does_not_raise(self):
        section = compute_financial_readiness(
            report=_report(), datasets=_datasets(), request_metadata=None, today=TODAY,
        )
        assert section is not None
        assert section["verdict"] in {VERDICT_CONDITIONAL, VERDICT_INSUFFICIENT}

    def test_empty_report_returns_none(self):
        assert compute_financial_readiness(
            report={}, datasets=_datasets(), request_metadata={},
        ) is None

    def test_anchors_on_the_recommended_territory_not_the_first_scenario(self):
        report = _report(
            territory="Hungary",
            scenarios=[
                _scenario("United Kingdom"),
                _scenario("Hungary", total=15_000_000, net_rebate=3_825_000, programme="NFI"),
            ],
            estimates=[_estimate("United Kingdom"), _estimate("Hungary", "NFI")],
            timing=[_timing("United Kingdom"), _timing("Hungary")],
        )
        report["locationRankings"] = [{"name": "Hungary", "score": 90}]
        section = _assess(
            report, _datasets(territory="Hungary", programme="NFI"), _metadata(),
        )
        assert section["territory"] == "Hungary"
        assert section["programme"] == "NFI"


# ── Builder and preview wiring ──────────────────────────────────────────────


class TestBuilderWiring:
    def _builder_datasets(self) -> dict:
        return {
            "incentives": [{
                "territory": "United Kingdom",
                "program": "AVEC",
                "rate_gross": 34.0,
                "rate_net": 25.5,
                "currency": "GBP",
                "payment_reliability": 0.95,
                "payment_timeline_days_max": 90,
                "payment_timeline_notes": "6-12 months",
                "source_name": "BFI",
                "data_freshness_days": 30,
                "status": "active",
                "rate_type": "tax_credit",
            }],
            "_territory_financials": {
                "United Kingdom": {
                    "programme": "AVEC",
                    "currency_symbol": "£",
                    "total_budget": "£30,000,000",
                    "net_rebate": "£5,737,500",
                    "total_budget_value": 30_000_000,
                    "net_rebate_value": 5_737_500,
                    "qualifying_spend_value": 24_000_000,
                }
            },
            "weather": [],
            "comparables": [],
            "grants": [],
            "festivals": [],
            "_budget_gbp": {"converted": 30_000_000},
            "_budget_amount": 30_000_000,
            "_budget_currency": "GBP",
            "_production_format": "Feature Film",
            "_production_priority": "full",
            "_shoot_weeks": 8,
            "_territory_profiles": {},
            "_fx_rates_from_budget": {},
            "_user_territories": ["United Kingdom"],
        }

    def test_paid_build_carries_the_section(self):
        report = ReportBuilder(
            self._builder_datasets(),
            {"country": "United Kingdom", "completion_date": "2027-06-30"},
            script_analysis=None,
            is_preview=False,
        ).build()
        assert "financialReadiness" in report
        assert report["financialReadiness"]["verdict"]

    def test_preview_build_never_carries_the_section(self):
        report = ReportBuilder(
            self._builder_datasets(),
            {"country": "United Kingdom"},
            script_analysis=None,
            is_preview=True,
        ).build()
        assert "financialReadiness" not in report

    def test_section_explainer_is_injected(self):
        report = ReportBuilder(
            self._builder_datasets(),
            {"country": "United Kingdom"},
            script_analysis=None,
            is_preview=False,
        ).build()
        assert report["sectionExplainers"]["financial_readiness"]

    def test_free_tier_filter_strips_the_section(self):
        from app.modules.reports.router import _build_free_tier_report_data

        filtered = _build_free_tier_report_data({
            "financialReadiness": {"verdict": "READY", "score": 90},
            "locationRankings": [],
        })
        assert "financialReadiness" not in filtered
