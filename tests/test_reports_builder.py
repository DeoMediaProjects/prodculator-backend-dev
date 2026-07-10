"""Unit tests for ReportBuilder deterministic skeleton assembly.

Tests verify that the builder produces correct output for all
deterministic fields — scoring, financial data, eligibility, stacking,
weather risk, etc.  AI-narrative fields should be None.
"""
from __future__ import annotations

import json


from app.modules.reports.builder import (
    ReportBuilder,
    SCORE_WEIGHTS,
    _compute_bankability_label,
    _incentive_rate_score,
    _incentive_qualification_score,
    _incentive_stability_score,
)
from app.modules.reports.helpers import format_rate


# ── Test fixtures ──────────────────────────────────────────────────────────


def _make_incentive(
    program: str = "AVEC",
    territory: str = "United Kingdom",
    rate_gross: float = 34.0,
    rate_net: float | None = None,
    *,
    payment_reliability: float | None = 0.95,
    payment_timeline_days_max: float | None = 90,
    payment_timeline_days_min: float | None = None,
    payment_timeline_notes: str | None = "6-12 months post-completion",
    is_supplementary: bool = False,
    nationality_requirements: list[str] | None = None,
    spv_eligible: bool | None = None,
    co_production_eligible: bool = False,
    stackable_with: list[str] | None = None,
    scope: str = "national",
    parent_territory: str | None = None,
    warnings_json: list[str] | None = None,
    eligibility_rules_json: list | None = None,
    cap_amount: float | None = None,
    cap_currency: str = "GBP",
    status: str = "active",
    applicable_formats: list[str] | None = None,
    qualifying_spend_min: float | None = None,
    qualifying_spend_currency: str = "GBP",
    source_name: str | None = "BFI",
    data_freshness_days: int | None = 30,
    rate_tier_json: list | None = None,
    cap_per_person: float | None = None,
    cap_per_person_currency: str | None = None,
    eligibility_notes: str | None = None,
    qualifying_spend_cap_pct: float | None = None,
    rate_type: str = "tax_credit",
    atl_exempt: bool = False,
    currency: str = "GBP",
    qualifying_spend_type: str = "total",
    rebate_cap_amount: float | None = None,
    rebate_cap_currency: str | None = None,
    cultural_test_required: bool | None = None,
    admin_complexity: str | None = None,
) -> dict:
    row: dict = {
        "program": program,
        "territory": territory,
        "rate_gross": rate_gross,
        "rate_net": rate_net,
        "payment_reliability": payment_reliability,
        "payment_timeline_days_max": payment_timeline_days_max,
        "payment_timeline_days_min": payment_timeline_days_min,
        "payment_timeline_notes": payment_timeline_notes,
        "is_supplementary": is_supplementary,
        "scope": scope,
        "parent_territory": parent_territory,
        "co_production_eligible": co_production_eligible,
        "cap_amount": cap_amount,
        "cap_currency": cap_currency,
        "status": status,
        "source_name": source_name,
        "data_freshness_days": data_freshness_days,
        "qualifying_spend_min": qualifying_spend_min,
        "qualifying_spend_currency": qualifying_spend_currency,
        "cap_per_person": cap_per_person,
        "cap_per_person_currency": cap_per_person_currency,
        "eligibility_notes": eligibility_notes,
        "qualifying_spend_cap_pct": qualifying_spend_cap_pct,
        "rate_type": rate_type,
        "atl_exempt": atl_exempt,
        "currency": currency,
        "qualifying_spend_type": qualifying_spend_type,
        "rebate_cap_amount": rebate_cap_amount,
        "rebate_cap_currency": rebate_cap_currency,
        "cultural_test_required": cultural_test_required,
        "admin_complexity": admin_complexity,
    }
    if nationality_requirements is not None:
        row["nationality_requirements"] = json.dumps(nationality_requirements)
    if spv_eligible is not None:
        row["spv_eligible"] = spv_eligible
    if stackable_with is not None:
        row["stackable_with"] = json.dumps(stackable_with)
    if warnings_json is not None:
        row["warnings_json"] = json.dumps(warnings_json)
    if eligibility_rules_json is not None:
        row["eligibility_rules_json"] = json.dumps(eligibility_rules_json)
    if applicable_formats is not None:
        row["applicable_formats"] = json.dumps(applicable_formats)
    if rate_tier_json is not None:
        row["rate_tier_json"] = json.dumps(rate_tier_json)
    return row


def _make_datasets(
    incentives: list[dict] | None = None,
    territory_financials: dict | None = None,
    weather: list[dict] | None = None,
    crew_costs: list[dict] | None = None,
    cast_costs: list[dict] | None = None,
    comparables: list[dict] | None = None,
    grants: list[dict] | None = None,
    festivals: list[dict] | None = None,
    budget_gbp: float | None = 10_000_000,
    budget_currency: str = "GBP",
    budget_amount: float | None = 10_000_000,
    production_format: str | None = "Feature Film",
    production_priority: str = "full",
    shoot_months: list[int] | None = None,
    shoot_weeks: int | None = 8,
    ext_int_ratio: float | None = None,
    producer_country: str | None = None,
    currency_advantage_scores: dict | None = None,
    visa_requirements: dict | None = None,
    territory_profiles: dict | None = None,
) -> dict:
    ds: dict = {
        "incentives": incentives or [],
        "_territory_financials": territory_financials or {},
        "weather": weather or [],
        "crew_costs": crew_costs or [],
        "cast_costs": cast_costs or [],
        "comparables": comparables or [],
        "grants": grants or [],
        "festivals": festivals or [],
        "_budget_gbp": {"converted": budget_gbp} if budget_gbp else None,
        "_budget_currency": budget_currency,
        "_budget_amount": budget_amount,
        "_production_format": production_format,
        "_production_priority": production_priority,
        "_shoot_months": shoot_months,
        "_shoot_weeks": shoot_weeks,
        "_ext_int_ratio": ext_int_ratio,
        "_producer_country": producer_country,
        "_currency_advantage_scores": currency_advantage_scores,
        "_visa_requirements": visa_requirements,
        "_territory_profiles": territory_profiles or {},
        "_fx_rates_from_budget": {},
    }
    return ds


def _build(datasets: dict, request_metadata: dict | None = None) -> dict:
    """Shortcut: build a skeleton and return it."""
    return ReportBuilder(
        datasets, request_metadata or {}, script_analysis=None,
    ).build()


class TestFormatRate:
    def test_net_rate_is_primary_when_gross_and_net_differ(self):
        assert format_rate(53, 39.75) == "39.75% net (53% gross)"
        assert format_rate(34, 25.5) == "25.5% net (34% gross)"

    def test_matching_gross_and_net_are_shown_once(self):
        assert format_rate(30, 30) == "30%"

    def test_labels_single_available_rate(self):
        assert format_rate(34, None) == "34% gross"
        assert format_rate(None, 25.5) == "25.5% net"


# ── Scoring tests ──────────────────────────────────────────────────────────


class TestIncentiveStrength:
    def test_high_rate_high_reliability(self):
        inc = _make_incentive(rate_gross=40.0, payment_reliability=0.95)
        strength = ReportBuilder._compute_incentive_strength(inc)
        assert 70 <= strength <= 90

    def test_low_rate_low_reliability(self):
        inc = _make_incentive(rate_gross=10.0, payment_reliability=0.30)
        strength = ReportBuilder._compute_incentive_strength(inc)
        assert strength < 45  # low rate + low reliability → weak overall

    def test_zero_rate(self):
        inc = _make_incentive(rate_gross=0.0, payment_reliability=0.95)
        strength = ReportBuilder._compute_incentive_strength(inc)
        # Zero rate still gets >50 because other dimensions (reliability 90,
        # stability 90, qualification ~80) contribute 65% of the weight.
        # But it should be meaningfully lower than high-rate equivalents.
        high_rate = _make_incentive(rate_gross=40.0, payment_reliability=0.95)
        high_strength = ReportBuilder._compute_incentive_strength(high_rate)
        assert strength < high_strength - 20


class TestBankabilityLabel:
    def test_bankable(self):
        assert _compute_bankability_label(0.90, 90) == "BANKABLE"

    def test_not_bankable_low_reliability(self):
        assert _compute_bankability_label(0.30, 90) == "NOT BANKABLE"

    def test_not_bankable_long_timeline_no_reliability(self):
        assert _compute_bankability_label(None, 400) == "NOT BANKABLE"

    def test_verify_first_default(self):
        assert _compute_bankability_label(0.70, 200) == "VERIFY FIRST"

    def test_bankable_high_reliability_no_timeline(self):
        assert _compute_bankability_label(0.85, None) == "BANKABLE"


class TestRateScore:
    def test_zero_rate(self):
        assert _incentive_rate_score(0) == 0.0

    def test_20_percent(self):
        assert _incentive_rate_score(20) == 40.0

    def test_above_max(self):
        assert _incentive_rate_score(120) == 100.0

    def test_interpolation(self):
        # 25% is halfway between (20, 40) and (30, 65)
        score = _incentive_rate_score(25)
        assert 50 < score < 55


class TestQualificationScore:
    def test_no_restrictions(self):
        inc = _make_incentive(nationality_requirements=None)
        assert _incentive_qualification_score(inc) >= 70

    def test_nationality_restriction(self):
        inc = _make_incentive(nationality_requirements=["CA"])
        assert _incentive_qualification_score(inc) < 50

    def test_spv_ineligible_penalty(self):
        inc = _make_incentive(spv_eligible=False, nationality_requirements=["CA"])
        score = _incentive_qualification_score(inc)
        assert score <= 30


class TestStabilityScore:
    def test_active_high_reliability(self):
        inc = _make_incentive(status="active", payment_reliability=0.90)
        assert _incentive_stability_score(inc) == 90

    def test_inactive(self):
        inc = _make_incentive(status="suspended")
        assert _incentive_stability_score(inc) == 20

    def test_frozen_warning(self):
        inc = _make_incentive(warnings_json=["Fund is currently frozen"])
        assert _incentive_stability_score(inc) == 20


# ── Location Rankings tests ────────────────────────────────────────────────


class TestBuildLocationRankings:
    def test_basic_ranking_structure(self):
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)

        rankings = report["locationRankings"]
        assert len(rankings) == 1
        loc = rankings[0]
        assert loc["name"] == "United Kingdom"
        assert loc["rebatePercent"] == "34% gross"
        assert isinstance(loc["incentiveStrength"], int)
        assert isinstance(loc["incentiveReliability"], int)
        assert loc["bankabilityLabel"] in ("BANKABLE", "VERIFY FIRST", "NOT BANKABLE")
        # Profile/AI-filled fields are None when no source profile or crew data exists.
        assert loc["costEfficiency"] is None
        assert loc["crewDepth"] is None
        assert loc["infrastructure"] is None
        assert loc["reasoning"] is None
        assert loc["keyAdvantages"] is None

    def test_crew_depth_and_infrastructure_from_territory_profile(self):
        inc = _make_incentive()
        ds = _make_datasets(
            incentives=[inc],
            territory_profiles={
                "United Kingdom": {
                    "territory": "United Kingdom",
                    "iso_code": "GB",
                    "crew_depth_tier": "established",
                    "crew_depth_score": 82,
                    "infrastructure_tier": "growing",
                    "infrastructure_score": 64,
                },
            },
        )

        report = _build(ds)
        loc = report["locationRankings"][0]

        assert loc["crewDepth"] == 82
        assert loc["crewDepthTier"] == "Established"
        assert loc["infrastructure"] == 64
        assert loc["infrastructureTier"] == "Growing"

    def test_payment_speed_from_db(self):
        inc = _make_incentive(payment_timeline_notes="6-12 months post-completion")
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        assert report["locationRankings"][0]["paymentSpeed"] == "6-12 months post-completion"

    def test_staleness_injected_into_key_risks(self):
        inc = _make_incentive(data_freshness_days=400)
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        risks = report["locationRankings"][0]["keyRisks"]
        assert any("outdated" in r for r in risks)

    def test_zero_rate_sets_strength_zero(self):
        inc = _make_incentive(rate_gross=0.0, rate_net=0.0)
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        assert report["locationRankings"][0]["incentiveStrength"] == 0

    def test_currency_advantage_from_scores(self):
        inc = _make_incentive(territory="Hungary")
        ds = _make_datasets(
            incentives=[inc],
            currency_advantage_scores={"Hungary": {"score": 78}},
        )
        report = _build(ds)
        assert report["locationRankings"][0]["currencyAdvantage"] == 78


class TestScoreWeights:
    def test_weights_match_public_methodology(self):
        assert SCORE_WEIGHTS["full"] == {
            "incentiveStrength": 0.30,
            "incentiveReliability": 0.15,
            "costEfficiency": 0.20,
            "currencyAdvantage": 0.15,
            "crewDepth": 0.10,
            "infrastructure": 0.10,
        }
        assert SCORE_WEIGHTS["incentive"] == {
            "incentiveStrength": 0.45,
            "incentiveReliability": 0.15,
            "costEfficiency": 0.15,
            "currencyAdvantage": 0.15,
            "crewDepth": 0.05,
            "infrastructure": 0.05,
        }
        assert SCORE_WEIGHTS["location"] == {
            "crewDepth": 0.25,
            "infrastructure": 0.20,
            "costEfficiency": 0.20,
            "incentiveStrength": 0.15,
            "incentiveReliability": 0.10,
            "currencyAdvantage": 0.10,
        }


class TestWeatherRiskInjection:
    def test_weather_risk_injected(self):
        inc = _make_incentive()
        weather = [
            {"territory": "United Kingdom", "month": 1, "storm_risk": "high", "avg_rainfall_mm": 120},
        ]
        ds = _make_datasets(
            incentives=[inc], weather=weather,
            shoot_months=[1, 2],
        )
        report = _build(ds)
        risks = report["locationRankings"][0]["keyRisks"]
        assert any("weather risk" in r.lower() for r in risks)

    def test_no_weather_risk_when_low(self):
        inc = _make_incentive()
        weather = [
            {"territory": "United Kingdom", "month": 6, "storm_risk": "low", "avg_rainfall_mm": 40},
        ]
        ds = _make_datasets(
            incentives=[inc], weather=weather,
            shoot_months=[6],
        )
        report = _build(ds)
        risks = report["locationRankings"][0]["keyRisks"]
        assert not any("weather risk" in r.lower() for r in risks)

    def test_exterior_exposure_amplification(self):
        inc = _make_incentive()
        weather = [
            {"territory": "United Kingdom", "month": 1, "storm_risk": "high", "avg_rainfall_mm": 120},
        ]
        ds = _make_datasets(
            incentives=[inc], weather=weather,
            shoot_months=[1], ext_int_ratio=0.8,
        )
        report = _build(ds)
        risks = report["locationRankings"][0]["keyRisks"]
        assert any("exterior" in r.lower() for r in risks)


class TestReliabilityWarnings:
    def test_db_warnings_injected(self):
        inc = _make_incentive(warnings_json=["Fund frequently oversubscribed"])
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        risks = report["locationRankings"][0]["keyRisks"]
        assert any("oversubscribed" in r for r in risks)

    def test_long_payment_timeline_warning(self):
        inc = _make_incentive(
            payment_timeline_days_max=400,
            payment_timeline_days_min=300,
        )
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        risks = report["locationRankings"][0]["keyRisks"]
        assert any("investor-bankable" in r.lower() for r in risks)


class TestOperationalRequirements:
    def test_operational_requirement_injected(self):
        inc = _make_incentive(
            eligibility_rules_json=[
                {"rule": "Must engage a local production service company", "required": True},
            ],
        )
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        risks = report["locationRankings"][0]["keyRisks"]
        assert any("production service company" in r.lower() for r in risks)


# ── Incentive Estimates tests ──────────────────────────────────────────────


class TestBuildIncentiveEstimates:
    def test_basic_estimate_structure(self):
        inc = _make_incentive()
        tf = {"United Kingdom": {"gross_rebate": "£2,720,000"}}
        ds = _make_datasets(incentives=[inc], territory_financials=tf)
        report = _build(ds)

        estimates = report["incentiveEstimates"]
        assert len(estimates) >= 1
        est = estimates[0]
        assert est["territory"] == "United Kingdom"
        assert est["program"] == "AVEC"
        assert est["rate"] == "34% gross"
        assert est["estimatedRebate"] == "£2,720,000"
        assert est["paymentSpeed"] == "6-12 months post-completion"
        assert est["dataSource"] == "BFI"

    def test_estimate_uses_net_rebate_as_primary_amount(self):
        inc = _make_incentive(rate_gross=53.0, rate_net=39.75)
        tf = {
            "United Kingdom": {
                "gross_rebate": "£424,000",
                "net_rebate": "£318,000",
            }
        }
        ds = _make_datasets(incentives=[inc], territory_financials=tf)
        report = _build(ds)

        est = report["incentiveEstimates"][0]
        assert est["rate"] == "39.75% net (53% gross)"
        assert est["estimatedRebate"] == "£318,000"

    def test_supplementary_stub(self):
        primary = _make_incentive(program="AVEC", territory="United Kingdom")
        supplementary = _make_incentive(
            program="VFX Expenditure Credit",
            territory="United Kingdom",
            is_supplementary=True,
            rate_gross=5.0,
        )
        tf = {"United Kingdom": {"gross_rebate": "£2,720,000"}}
        ds = _make_datasets(
            incentives=[primary, supplementary],
            territory_financials=tf,
        )
        report = _build(ds)

        estimates = report["incentiveEstimates"]
        supp = [e for e in estimates if e.get("program") == "VFX Expenditure Credit"]
        assert len(supp) == 1
        assert supp[0]["bankabilityLabel"] == "INFORMATIONAL"
        # When primary is AVEC, VFX credit CAN stack — stackingNote should say so
        assert "stacks ON TOP of" in supp[0]["stackingNote"]
        assert "MUTUAL EXCLUSIVITY" not in supp[0]["stackingNote"]

    def test_supplementary_stub_mutually_exclusive_with_primary(self):
        """When eligibility_notes says 'CANNOT be combined with IFTC' and the
        primary programme is IFTC, the stub must warn of mutual exclusivity
        instead of claiming the credit stacks on top."""
        primary = _make_incentive(
            program="UK Independent Film Tax Credit (IFTC)",
            territory="United Kingdom",
        )
        supplementary = _make_incentive(
            program="VFX Expenditure Credit (Uplift)",
            territory="United Kingdom",
            is_supplementary=True,
            rate_gross=39.0,
            eligibility_notes=(
                "39% on qualifying UK VFX expenditure. "
                "CANNOT be combined with IFTC — mutually exclusive. "
                "CAN be combined with standard AVEC."
            ),
        )
        tf = {"United Kingdom": {"gross_rebate": "£6,360,000"}}
        ds = _make_datasets(
            incentives=[primary, supplementary],
            territory_financials=tf,
        )
        report = _build(ds)

        estimates = report["incentiveEstimates"]
        supp = [e for e in estimates if e.get("program") == "VFX Expenditure Credit (Uplift)"]
        assert len(supp) == 1
        assert "MUTUAL EXCLUSIVITY" in supp[0]["stackingNote"]
        assert "CANNOT be combined with" in supp[0]["stackingNote"]
        assert "stacks ON TOP of" not in supp[0]["stackingNote"]
        # eligibility_notes should be surfaced so the AI has the full constraint
        assert supp[0].get("eligibilityNote") is not None
        assert "CANNOT be combined with IFTC" in supp[0]["eligibilityNote"]

    def test_format_not_applicable(self):
        inc = _make_incentive(
            program="IFTC", territory="Ireland",
            applicable_formats=["Feature Film"],
        )
        ds = _make_datasets(
            incentives=[inc], production_format="TV Series",
        )
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert est["bankabilityLabel"] == "NOT APPLICABLE"

    def test_zero_rate_estimate(self):
        inc = _make_incentive(rate_gross=0.0, rate_net=0.0)
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert est["incentiveStrength"] == 0
        assert est["estimatedRebate"] == "N/A"

    def test_staleness_warning(self):
        inc = _make_incentive(data_freshness_days=500)
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert "stalenessWarning" in est


class TestStacking:
    def test_stackable_with_from_db(self):
        inc = _make_incentive(stackable_with=["VFX Expenditure Credit"])
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert est["stackableWith"] == ["VFX Expenditure Credit"]

    def test_scope_from_db(self):
        inc = _make_incentive(scope="national")
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert est["scope"] == "national"

    def test_parent_territory(self):
        inc = _make_incentive(
            territory="Scotland", scope="regional",
            parent_territory="United Kingdom",
        )
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert est["parentTerritory"] == "United Kingdom"


class TestEligibility:
    def test_no_nationality_restriction_qualifies(self):
        inc = _make_incentive(nationality_requirements=None)
        ds = _make_datasets(incentives=[inc], producer_country="US")
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert est["eligibilityStatus"] == "qualified"

    def test_matching_country_qualifies(self):
        inc = _make_incentive(nationality_requirements=["GB"])
        ds = _make_datasets(incentives=[inc], producer_country="GB")
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert est["eligibilityStatus"] == "qualified"

    def test_non_matching_with_spv(self):
        inc = _make_incentive(
            nationality_requirements=["GB"], spv_eligible=True,
        )
        ds = _make_datasets(incentives=[inc], producer_country="US")
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert est["eligibilityStatus"] == "requires_spv"

    def test_non_matching_ineligible(self):
        inc = _make_incentive(
            nationality_requirements=["CA"], spv_eligible=False,
            co_production_eligible=False,
        )
        ds = _make_datasets(incentives=[inc], producer_country="US")
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        assert est["eligibilityStatus"] == "ineligible"

    def test_no_producer_country_adds_assumption(self):
        inc = _make_incentive(nationality_requirements=["GB"])
        ds = _make_datasets(incentives=[inc], producer_country=None)
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        reqs = est.get("requirements", [])
        assert any("eligibility assumes" in r.lower() for r in reqs)


class TestHETVCheck:
    def test_hetv_pass(self):
        inc = _make_incentive(program="Audio-Visual Expenditure Credit (AVEC)")
        ds = _make_datasets(
            incentives=[inc],
            production_format="TV Series",
            budget_gbp=20_000_000,
        )
        ds["_total_episodes"] = 8
        ds["_episode_runtime_minutes"] = 60
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        reqs = est.get("requirements", [])
        assert any("HETV threshold: PASS" in str(r) for r in reqs)

    def test_hetv_fail(self):
        inc = _make_incentive(program="Audio-Visual Expenditure Credit (AVEC)")
        ds = _make_datasets(
            incentives=[inc],
            production_format="TV Series",
            budget_gbp=2_000_000,
        )
        ds["_total_episodes"] = 10
        ds["_episode_runtime_minutes"] = 60
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        reqs = est.get("requirements", [])
        assert any("HETV threshold: FAIL" in str(r) for r in reqs)
        assert est["bankabilityLabel"] == "NOT APPLICABLE"

    def test_hetv_not_triggered_for_feature_film(self):
        inc = _make_incentive(program="Audio-Visual Expenditure Credit (AVEC)")
        ds = _make_datasets(
            incentives=[inc],
            production_format="Feature Film",
        )
        report = _build(ds)
        est = report["incentiveEstimates"][0]
        reqs = est.get("requirements", [])
        assert not any("hetv" in str(r).lower() for r in reqs)


# ── Financial Analysis tests ───────────────────────────────────────────────


class TestBuildFinancialAnalysis:
    def test_budget_scenarios_from_territory_financials(self):
        inc = _make_incentive()
        tf = {
            "United Kingdom": {
                "total_budget": "£10,000,000",
                "qualifying_spend_pct": "80%",
                "qualifying_spend": "£8,000,000",
                "net_qualifying_spend": "£6,800,000",
                "rate_gross": "34%",
                "rate_net": None,
                "gross_rebate": "£2,312,000",
                "net_rebate": "£2,312,000",
                "net_budget": "£7,688,000",
                "programme": "AVEC",
                "atl_deduction": "£1,500,000",
                "atl_pct": "15%",
                "atl_deduction_note": "ATL deduction estimated at 15%",
                "rebate_cap_note": None,
                "qualifying_spend_note": None,
                "crew_rates": {"Director of Photography": "£800–£1,200/day"},
            },
        }
        ds = _make_datasets(incentives=[inc], territory_financials=tf)
        report = _build(ds)

        scenarios = report["financialAnalysis"]["budgetScenarios"]
        assert len(scenarios) == 1
        s = scenarios[0]
        assert s["territory"] == "United Kingdom"
        assert s["totalBudget"] == "£10,000,000"
        assert s["grossRebate"] == "£2,312,000"
        assert s["atlDeduction"] == "-£1,500,000"
        assert s["atlDeductionPct"] == "15%"

    def test_no_crew_day_rate_sections(self):
        """Crew COST (day rates) is out of report scope — handoff §1.
        Crew DEPTH remains a ranking dimension on locationRankings."""
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)

        assert "crewCostComparison" not in report["financialAnalysis"]
        assert "crewInsights" not in report
        assert "castInsights" not in report


# ── Executive Summary tests ────────────────────────────────────────────────


class TestBuildExecutiveSummary:
    def test_basic_structure(self):
        inc = _make_incentive()
        tf = {"United Kingdom": {"gross_rebate": "£2,720,000", "headline_net_budget": "approximately £7,280,000"}}
        ds = _make_datasets(incentives=[inc], territory_financials=tf, shoot_weeks=8)
        report = _build(ds)

        summary = report["executiveSummary"]
        assert summary["recommendedTerritory"] == "United Kingdom"
        assert summary["keyInsights"] is None  # AI fills
        assert summary["shootDays"] == 8
        assert summary["recommendedTerritoryRebate"] == "£2,720,000"

    def test_recommended_rebate_uses_net_amount(self):
        inc = _make_incentive(rate_gross=53.0, rate_net=39.75)
        tf = {
            "United Kingdom": {
                "gross_rebate": "£424,000",
                "net_rebate": "£318,000",
                "headline_net_budget": "approximately £682,000",
            }
        }
        ds = _make_datasets(incentives=[inc], territory_financials=tf)
        report = _build(ds)

        summary = report["executiveSummary"]
        assert summary["recommendedTerritoryRebate"] == "£318,000"

    def test_shoot_duration_flag_injected(self):
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc], shoot_weeks=30, production_format="Feature Film")
        report = _build(ds)

        summary = report["executiveSummary"]
        assert "keyFlags" in summary
        assert any("extended shoot" in f.lower() for f in summary["keyFlags"])

    def test_no_shoot_flag_for_short_duration(self):
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc], shoot_weeks=20, production_format="Feature Film")
        report = _build(ds)

        summary = report["executiveSummary"]
        key_flags = summary.get("keyFlags", [])
        assert not any("extended shoot" in f.lower() for f in key_flags)


# ── Weather Logistics tests ────────────────────────────────────────────────


class TestBuildWeatherLogistics:
    def test_visa_from_db(self):
        inc = _make_incentive()
        ds = _make_datasets(
            incentives=[inc],
            visa_requirements={
                "United Kingdom": {"notes": "No visa required for UK nationals"},
            },
        )
        report = _build(ds)
        weather = report["weatherLogistics"]
        assert len(weather) == 1
        assert weather[0]["travelVisa"] == "No visa required for UK nationals"

    def test_visa_disclaimer_when_no_db(self):
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc], visa_requirements={})
        report = _build(ds)
        weather = report["weatherLogistics"]
        assert "vary by nationality" in weather[0]["travelVisa"]


# ── Comparables tests ──────────────────────────────────────────────────────


class TestBuildComparables:
    def test_comparables_from_dataset(self):
        comps = [
            {"title": "The Crown", "primary_territory": "United Kingdom", "budget_range": "£10M", "genre": "Drama"},
        ]
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc], comparables=comps)
        report = _build(ds)

        assert len(report["comparables"]) == 1
        comp = report["comparables"][0]
        assert comp["title"] == "The Crown"
        assert comp["location"] == "United Kingdom"
        assert comp["relevanceDescription"] is None  # AI fills

    def test_budget_gap_flag(self):
        comps = [
            {"title": "Avengers", "primary_territory": "United Kingdom", "budget_range": "£200M", "genre": "Action"},
        ]
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc], comparables=comps, budget_gbp=5_000_000)
        report = _build(ds)

        comp = report["comparables"][0]
        assert comp.get("_budgetGapFlag") == "significantly larger"


# ── Funding Opportunities tests ────────────────────────────────────────────


class TestBuildFundingOpportunities:
    def test_grant_label_prefix(self):
        from datetime import date as _date
        grants = [
            {"title": "BFI Development Fund", "territory": "United Kingdom",
             "amount_description": "£50,000 per project",
             "deadline": "rolling", "recurrence": "rolling",
             "verified_at": _date.today().isoformat()},
        ]
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc], grants=grants)
        report = _build(ds)

        opps = report["fundingOpportunities"]
        assert len(opps) == 1
        assert opps[0]["notes"].startswith("Up to £50,000")

    def test_feature_only_grants_filtered_for_tv(self):
        """Format is a HARD GATE (grants matcher G1): a feature-only fund is
        excluded for a TV production — the old-report defect the handoff
        called out (BFI Film Fund/Film4 recommended for a TV pilot)."""
        from datetime import date as _date
        grants = [
            {"title": "BFI Distribution Fund", "territory": "United Kingdom",
             "amount_description": "£100K",
             "eligible_formats": ["feature"],
             "deadline": "rolling", "recurrence": "rolling",
             "verified_at": _date.today().isoformat()},
        ]
        inc = _make_incentive()
        ds = _make_datasets(
            incentives=[inc], grants=grants, production_format="TV Series",
        )
        report = _build(ds)
        assert len(report["fundingOpportunities"]) == 0


# ── Section Explainers tests ──────────────────────────────────────────────


class TestSectionExplainers:
    def test_explainers_present(self):
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)

        assert "sectionExplainers" in report
        explainers = report["sectionExplainers"]
        assert "executive_summary" in explainers
        assert "location_strategy" in explainers
        assert "financial_analysis" in explainers


# ── Overall score computation tests ────────────────────────────────────────


class TestComputeOverallScores:
    def test_scores_computed_and_sorted(self):
        inc1 = _make_incentive(
            program="AVEC", territory="United Kingdom",
            rate_gross=34, payment_reliability=0.95,
        )
        inc2 = _make_incentive(
            program="IFTC", territory="Ireland",
            rate_gross=32, payment_reliability=0.85,
        )
        ds = _make_datasets(incentives=[inc1, inc2])
        report = _build(ds)

        # Simulate AI filling the 3 qualitative dimensions
        for loc in report["locationRankings"]:
            loc["costEfficiency"] = 70
            loc["crewDepth"] = 65
            loc["infrastructure"] = 60

        ReportBuilder.compute_overall_scores(report, "full")

        rankings = report["locationRankings"]
        assert all(isinstance(loc["score"], int) for loc in rankings)
        assert all(0 <= loc["score"] <= 100 for loc in rankings)
        # Should be sorted descending
        scores = [loc["score"] for loc in rankings]
        assert scores == sorted(scores, reverse=True)

    def test_summary_updated_with_top_territory(self):
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)

        for loc in report["locationRankings"]:
            loc["costEfficiency"] = 70
            loc["crewDepth"] = 65
            loc["infrastructure"] = 60

        ReportBuilder.compute_overall_scores(report, "full")

        summary = report["executiveSummary"]
        assert summary["recommendedTerritory"] == "United Kingdom"
        assert isinstance(summary["recommendedTerritoryScore"], int)

    def test_deep_dives_get_scores(self):
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)

        for loc in report["locationRankings"]:
            loc["costEfficiency"] = 70
            loc["crewDepth"] = 65
            loc["infrastructure"] = 60

        ReportBuilder.compute_overall_scores(report, "full")

        dives = report["territoryDeepDives"]
        assert len(dives) >= 1
        assert isinstance(dives[0]["score"], int)

    def test_deep_dives_have_required_fields(self):
        inc = _make_incentive(rate_gross=25.0, rate_net=20.0)
        ds = _make_datasets(incentives=[inc])
        ds["_territory_financials"] = {
            "United Kingdom": {
                "gross_rebate": "£500,000",
                "headline_net_budget": "approximately £3,500,000",
            },
        }
        report = _build(ds)

        dives = report["territoryDeepDives"]
        assert len(dives) >= 1
        dive = dives[0]
        # DB-populated fields
        assert dive["name"] == "United Kingdom"
        assert dive["country"] == "United Kingdom"
        assert dive["rebate"] == "20% net (25% gross)"
        assert dive["estimatedRebate"] == "£500,000"
        assert dive["paymentSpeed"] != ""
        # AI-filled fields should be None in skeleton
        assert dive["infrastructure"] is None
        assert dive["keyAdvantages"] is None
        # DB-computed qualitative fields are always set (never None)
        assert dive["culturalTestLikelihood"] is not None
        assert dive["adminComplexity"] is not None

    def test_cultural_test_from_db_column(self):
        """cultural_test_required DB column is the sole source — no heuristic."""
        inc_true = _make_incentive(cultural_test_required=True)
        inc_false = _make_incentive(cultural_test_required=False)
        inc_null = _make_incentive(cultural_test_required=None)

        for inc, expected in [
            (inc_true, "High (85%)"),
            (inc_false, "N/A"),
            (inc_null, "N/A"),
        ]:
            ds = _make_datasets(incentives=[inc])
            report = _build(ds)
            dive = report["territoryDeepDives"][0]
            loc = report["locationRankings"][0]
            assert dive["culturalTestLikelihood"] == expected, f"dive: expected {expected}"
            assert loc["culturalTestLikelihood"] == expected, f"loc: expected {expected}"

    def test_admin_complexity_from_db_column(self):
        """admin_complexity DB column is the sole source — no heuristic."""
        for val, expected in [("Low", "Low"), ("Medium", "Medium"), ("High", "High"), (None, "Medium")]:
            inc = _make_incentive(admin_complexity=val)
            ds = _make_datasets(incentives=[inc])
            report = _build(ds)
            dive = report["territoryDeepDives"][0]
            assert dive["adminComplexity"] == expected, f"expected {expected} for column={val!r}"

    def test_deep_dives_estimated_rebate_fallback(self):
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc])
        # No _territory_financials → fallback
        report = _build(ds)
        dives = report["territoryDeepDives"]
        assert len(dives) >= 1
        assert dives[0]["estimatedRebate"] == "See programme terms"

    def test_weather_penalty_applied(self):
        inc = _make_incentive()
        weather = [
            {"territory": "United Kingdom", "month": 1, "storm_risk": "high", "avg_rainfall_mm": 120},
        ]
        ds = _make_datasets(
            incentives=[inc], weather=weather,
            shoot_months=[1], ext_int_ratio=0.6,
        )
        report = _build(ds)

        # Build identical report without weather
        ds2 = _make_datasets(incentives=[inc])
        report2 = _build(ds2)

        # Fill same AI scores
        for r in (report, report2):
            for loc in r["locationRankings"]:
                loc["costEfficiency"] = 70
                loc["crewDepth"] = 65
                loc["infrastructure"] = 60

        ReportBuilder.compute_overall_scores(report, "full")
        ReportBuilder.compute_overall_scores(report2, "full")

        score_with_weather = report["locationRankings"][0]["score"]
        score_without_weather = report2["locationRankings"][0]["score"]
        assert score_with_weather < score_without_weather


# ── Integration: full build test ───────────────────────────────────────────


class TestFullBuild:
    def test_build_produces_complete_skeleton(self):
        """Verify all top-level keys are present in a full build."""
        inc = _make_incentive()
        tf = {"United Kingdom": {
            "gross_rebate": "£2,720,000",
            "headline_net_budget": "approximately £7,280,000",
            "crew_rates": {},
        }}
        ds = _make_datasets(incentives=[inc], territory_financials=tf)
        report = _build(ds)

        expected_keys = {
            "genre", "tone", "scale", "complexity",
            "locationRankings", "incentiveEstimates", "financialAnalysis",
            "executiveSummary",
            "comparables", "weatherLogistics", "fundingOpportunities",
            "territoryDeepDives", "attributions", "alternativeStrategy",
            "sectionExplainers", "scriptAnalysis",
        }
        assert expected_keys.issubset(set(report.keys()))

    def test_ai_narrative_fields_are_none(self):
        """All AI-narrative fields should be None in the skeleton."""
        inc = _make_incentive()
        ds = _make_datasets(incentives=[inc])
        report = _build(ds)

        assert report["genre"] is None
        assert report["tone"] is None
        assert report["scale"] is None
        assert report["complexity"] is None
        assert report["alternativeStrategy"] is None
        assert report["executiveSummary"]["keyInsights"] is None

    def test_preview_mode_limits_territories(self):
        """Preview mode should limit to 3 territories."""
        incs = [
            _make_incentive(program=f"Prog{i}", territory=f"Territory{i}")
            for i in range(6)
        ]
        ds = _make_datasets(incentives=incs)
        builder = ReportBuilder(ds, {}, is_preview=True)
        report = builder.build()

        # Should have at most 3 territories
        assert len(report["locationRankings"]) <= 3
