"""Unit tests for ReportValidator gap-fix patch methods.

Covers:
- _patch_stacking_logic
- _patch_weather_risk
- _patch_eligibility
"""
from __future__ import annotations

import json

from app.modules.reports.validator import ReportValidator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_report(incentive_estimates=None, location_rankings=None):
    return {
        "incentiveEstimates": incentive_estimates or [],
        "locationRankings": location_rankings or [],
    }


def _make_incentive_db(
    program_name: str,
    territory: str = "United Kingdom",
    scope: str = "national",
    parent_territory: str | None = None,
    stackable_with: list[str] | None = None,
    nationality_requirements: list[str] | None = None,
    co_production_eligible: bool = False,
    co_production_treaties: list[str] | None = None,
    spv_eligible: bool = False,
):
    return {
        "program_name": program_name,
        "territory": territory,
        "scope": scope,
        "parent_territory": parent_territory,
        "stackable_with": json.dumps(stackable_with) if stackable_with else None,
        "nationality_requirements": json.dumps(nationality_requirements) if nationality_requirements else None,
        "co_production_eligible": co_production_eligible,
        "co_production_treaties": json.dumps(co_production_treaties) if co_production_treaties else None,
        "spv_eligible": spv_eligible,
        "rate_gross": 34,
        "rate_net": None,
        "cap_amount": None,
        "cap_currency": "GBP",
    }


# ── _patch_stacking_logic ─────────────────────────────────────────────────────

def test_patch_stacking_logic_patches_scope_from_db():
    """scope is copied from DB when the AI omits it."""
    db = _make_incentive_db("AVEC", scope="national")
    by_program = {"AVEC": db, "avec": db}
    report = _make_report(
        incentive_estimates=[{"program": "AVEC", "territory": "United Kingdom"}]
    )
    warnings: list[str] = []
    ReportValidator._patch_stacking_logic(report, by_program, warnings)
    assert report["incentiveEstimates"][0]["scope"] == "national"


def test_patch_stacking_logic_patches_parent_territory():
    """parentTerritory is copied from DB for regional incentives."""
    db = _make_incentive_db(
        "Creative Scotland Production Growth Fund",
        territory="Scotland",
        scope="regional",
        parent_territory="United Kingdom",
    )
    by_program = {
        "Creative Scotland Production Growth Fund": db,
        "creative scotland production growth fund": db,
    }
    report = _make_report(
        incentive_estimates=[{"program": "Creative Scotland Production Growth Fund", "territory": "Scotland"}]
    )
    warnings: list[str] = []
    ReportValidator._patch_stacking_logic(report, by_program, warnings)
    est = report["incentiveEstimates"][0]
    assert est["scope"] == "regional"
    assert est["parentTerritory"] == "United Kingdom"


def test_patch_stacking_logic_strips_hallucinated_stacking():
    """AI-invented stacking entries not in DB stackable_with are removed."""
    db = _make_incentive_db("AVEC", stackable_with=["Creative Scotland Production Growth Fund"])
    by_program = {"AVEC": db, "avec": db}
    report = _make_report(
        incentive_estimates=[{
            "program": "AVEC",
            "territory": "United Kingdom",
            "stackableWith": ["Creative Scotland Production Growth Fund", "Hallucinated Fund"],
        }]
    )
    warnings: list[str] = []
    ReportValidator._patch_stacking_logic(report, by_program, warnings)
    est = report["incentiveEstimates"][0]
    assert "Hallucinated Fund" not in (est.get("stackableWith") or [])
    assert "Creative Scotland Production Growth Fund" in (est.get("stackableWith") or [])
    assert any("hallucinated" in w.lower() for w in warnings)


def test_patch_stacking_logic_fills_stackable_with_from_db():
    """If AI omitted stackableWith, fill it from DB."""
    db = _make_incentive_db("AVEC", stackable_with=["Creative Scotland Production Growth Fund"])
    by_program = {"AVEC": db, "avec": db}
    report = _make_report(
        incentive_estimates=[{"program": "AVEC", "territory": "United Kingdom"}]
    )
    warnings: list[str] = []
    ReportValidator._patch_stacking_logic(report, by_program, warnings)
    assert report["incentiveEstimates"][0].get("stackableWith") == ["Creative Scotland Production Growth Fund"]


def test_patch_stacking_logic_no_op_when_no_incentive_data():
    """Gracefully handles unknown program (not in incentives_by_program)."""
    report = _make_report(
        incentive_estimates=[{"program": "Unknown Fund", "territory": "Atlantis"}]
    )
    warnings: list[str] = []
    ReportValidator._patch_stacking_logic(report, {}, warnings)
    assert warnings == []


# ── _patch_weather_risk ───────────────────────────────────────────────────────

def _make_weather_row(territory: str, month: int, storm_risk: str = "low", rainfall: float = 30.0):
    return {
        "territory": territory,
        "month": month,
        "storm_risk": storm_risk,
        "avg_rainfall_mm": rainfall,
        "exterior_shoot_score": 80 if storm_risk == "low" else 30,
    }


def test_patch_weather_risk_injects_key_risk_for_high_risk_month():
    """High storm risk in shoot month → risk injected at top of keyRisks."""
    weather = [_make_weather_row("South Africa", 2, storm_risk="high", rainfall=150.0)]
    report = _make_report(
        location_rankings=[{"name": "South Africa", "score": 70, "keyRisks": []}]
    )
    warnings: list[str] = []
    ReportValidator._patch_weather_risk(report, weather, shoot_months=[2], ext_int_ratio=0.6, warnings=warnings)

    loc = report["locationRankings"][0]
    assert any("weather risk" in r.lower() for r in loc["keyRisks"])
    assert loc["keyRisks"][0].lower().startswith("weather risk")  # inserted at top
    assert loc["score"] < 70  # penalised
    assert loc.get("weatherRiskImpact") is not None
    assert loc["weatherRiskImpact"] < 0
    assert any("penali" in w.lower() for w in warnings)


def test_patch_weather_risk_high_ext_ratio_adds_exposure_risk():
    """70%+ exterior ratio AND high risk month → exposure message added."""
    weather = [_make_weather_row("South Africa", 2, storm_risk="high", rainfall=200.0)]
    report = _make_report(
        location_rankings=[{"name": "South Africa", "score": 70, "keyRisks": []}]
    )
    warnings: list[str] = []
    ReportValidator._patch_weather_risk(report, weather, shoot_months=[2], ext_int_ratio=0.72, warnings=warnings)

    key_risks = report["locationRankings"][0]["keyRisks"]
    assert any("exterior" in r.lower() for r in key_risks)


def test_patch_weather_risk_low_ext_ratio_no_penalty():
    """Low ext ratio (< 0.5) → no score penalty even with high risk month."""
    weather = [_make_weather_row("South Africa", 2, storm_risk="high", rainfall=150.0)]
    report = _make_report(
        location_rankings=[{"name": "South Africa", "score": 70, "keyRisks": []}]
    )
    warnings: list[str] = []
    ReportValidator._patch_weather_risk(report, weather, shoot_months=[2], ext_int_ratio=0.2, warnings=warnings)

    loc = report["locationRankings"][0]
    # Risk note may still be added, but no score penalty
    assert loc["score"] == 70
    assert loc.get("weatherRiskImpact") is None


def test_patch_weather_risk_no_data_is_noop():
    """No weather data → no changes at all."""
    report = _make_report(
        location_rankings=[{"name": "South Africa", "score": 70, "keyRisks": []}]
    )
    warnings: list[str] = []
    ReportValidator._patch_weather_risk(report, [], shoot_months=[2], ext_int_ratio=0.8, warnings=warnings)
    assert report["locationRankings"][0]["score"] == 70
    assert warnings == []


def test_patch_weather_risk_no_shoot_months_is_noop():
    """No shoot months → no changes."""
    weather = [_make_weather_row("South Africa", 2, storm_risk="high", rainfall=150.0)]
    report = _make_report(
        location_rankings=[{"name": "South Africa", "score": 70, "keyRisks": []}]
    )
    warnings: list[str] = []
    ReportValidator._patch_weather_risk(report, weather, shoot_months=None, ext_int_ratio=0.8, warnings=warnings)
    assert report["locationRankings"][0]["score"] == 70
    assert warnings == []


def test_patch_weather_risk_low_rainfall_no_risk():
    """Low rainfall + low storm_risk → no injection."""
    weather = [_make_weather_row("Malta", 6, storm_risk="low", rainfall=5.0)]
    report = _make_report(
        location_rankings=[{"name": "Malta", "score": 80, "keyRisks": []}]
    )
    warnings: list[str] = []
    ReportValidator._patch_weather_risk(report, weather, shoot_months=[6], ext_int_ratio=0.6, warnings=warnings)
    assert report["locationRankings"][0]["score"] == 80
    assert warnings == []


# ── _patch_eligibility ────────────────────────────────────────────────────────

def test_patch_eligibility_uk_producer_qualifies():
    """GB producer + AVEC (requires GB) → eligibilityStatus = 'qualified'."""
    db = _make_incentive_db("AVEC", nationality_requirements=["GB"], spv_eligible=True)
    by_program = {"AVEC": db, "avec": db}
    report = _make_report(
        incentive_estimates=[{"program": "AVEC", "territory": "United Kingdom"}]
    )
    warnings: list[str] = []
    ReportValidator._patch_eligibility(report, by_program, producer_country="GB", co_production_status=None, warnings=warnings)

    est = report["incentiveEstimates"][0]
    assert est["eligibilityStatus"] == "qualified"
    assert warnings == []


def test_patch_eligibility_foreign_producer_requires_spv():
    """ZA producer + AVEC (requires GB, spv_eligible=True) → requires_spv."""
    db = _make_incentive_db(
        "AVEC",
        nationality_requirements=["GB"],
        co_production_eligible=True,
        spv_eligible=True,
    )
    by_program = {"AVEC": db, "avec": db}
    report = _make_report(
        incentive_estimates=[{"program": "AVEC", "territory": "United Kingdom"}]
    )
    warnings: list[str] = []
    ReportValidator._patch_eligibility(report, by_program, producer_country="ZA", co_production_status=None, warnings=warnings)

    est = report["incentiveEstimates"][0]
    assert est["eligibilityStatus"] == "requires_spv"
    assert "ZA" in est.get("eligibilityNote", "")
    assert any("nationality_requirements" in w for w in warnings)


def test_patch_eligibility_foreign_producer_requires_co_production():
    """ZA producer + AVEC (requires GB, co_production_eligible=True, spv_eligible=False)."""
    db = _make_incentive_db(
        "AVEC",
        nationality_requirements=["GB"],
        co_production_eligible=True,
        spv_eligible=False,
    )
    by_program = {"AVEC": db, "avec": db}
    report = _make_report(
        incentive_estimates=[{"program": "AVEC", "territory": "United Kingdom"}]
    )
    warnings: list[str] = []
    ReportValidator._patch_eligibility(report, by_program, producer_country="ZA", co_production_status=None, warnings=warnings)

    assert report["incentiveEstimates"][0]["eligibilityStatus"] == "requires_co_production"


def test_patch_eligibility_no_producer_adds_assumption_note():
    """No producer_country → assumption note appended to requirements."""
    db = _make_incentive_db("AVEC", nationality_requirements=["GB"])
    by_program = {"AVEC": db, "avec": db}
    report = _make_report(
        incentive_estimates=[{"program": "AVEC", "territory": "United Kingdom", "requirements": []}]
    )
    warnings: list[str] = []
    ReportValidator._patch_eligibility(report, by_program, producer_country=None, co_production_status=None, warnings=warnings)

    reqs = report["incentiveEstimates"][0]["requirements"]
    assert any("eligibility assumes" in r.lower() for r in reqs)
    assert warnings == []  # assumption note is not a warning


def test_patch_eligibility_no_nationality_requirements_is_qualified():
    """Programme with no nationality_requirements → open to all → qualified."""
    db = _make_incentive_db("SA DTIC", nationality_requirements=None)
    by_program = {"SA DTIC": db, "sa dtic": db}
    report = _make_report(
        incentive_estimates=[{"program": "SA DTIC", "territory": "South Africa"}]
    )
    warnings: list[str] = []
    ReportValidator._patch_eligibility(report, by_program, producer_country="ZA", co_production_status=None, warnings=warnings)
    assert report["incentiveEstimates"][0]["eligibilityStatus"] == "qualified"


def test_patch_eligibility_does_not_overwrite_existing_status():
    """If AI already set eligibilityStatus, the validator doesn't overwrite it."""
    db = _make_incentive_db("AVEC", nationality_requirements=["GB"])
    by_program = {"AVEC": db, "avec": db}
    report = _make_report(
        incentive_estimates=[{
            "program": "AVEC",
            "territory": "United Kingdom",
            "eligibilityStatus": "ineligible",  # AI set this
        }]
    )
    warnings: list[str] = []
    ReportValidator._patch_eligibility(report, by_program, producer_country="ZA", co_production_status=None, warnings=warnings)
    # Should still be ineligible — validator only sets if not present
    assert report["incentiveEstimates"][0]["eligibilityStatus"] == "ineligible"


def _make_incentive_db_full(
    program_name: str,
    territory: str = "United Kingdom",
    rate_gross: float = 34.0,
    rate_net: float | None = 25.5,
    qualifying_spend_cap_pct: float | None = 80.0,
    cap_amount: float | None = None,
    cap_currency: str = "GBP",
    cap_per_person: float | None = None,
    cap_per_person_currency: str | None = None,
    rate_tier_json: str | None = None,
    warnings_json: str | None = None,
    eligibility_rules_json: str | None = None,
    payment_timeline_days_min: int | None = None,
    payment_timeline_days_max: int | None = None,
    currency: str = "GBP",
):
    return {
        "program_name": program_name,
        "territory": territory,
        "rate_gross": rate_gross,
        "rate_net": rate_net,
        "qualifying_spend_cap_pct": qualifying_spend_cap_pct,
        "cap_amount": cap_amount,
        "cap_currency": cap_currency,
        "cap_per_person": cap_per_person,
        "cap_per_person_currency": cap_per_person_currency,
        "rate_tier_json": rate_tier_json,
        "rate_type": "tax_credit",
        "warnings_json": warnings_json,
        "eligibility_rules_json": eligibility_rules_json,
        "payment_timeline_days_min": payment_timeline_days_min,
        "payment_timeline_days_max": payment_timeline_days_max,
        "payment_timeline_notes": None,
        "currency": currency,
        "scope": "national",
        "parent_territory": None,
        "stackable_with": None,
        "nationality_requirements": None,
        "co_production_eligible": False,
        "spv_eligible": False,
    }


# ── _patch_reliability_warnings tests ────────────────────────────────────────


def test_reliability_warnings_injected_from_dataset():
    """Warnings from warnings_json should appear in keyRisks."""
    db = _make_incentive_db_full(
        "Slow Programme",
        territory="SlowLand",
        warnings_json='["Payment takes forever","ZAR volatility risk"]',
    )
    by_program = {"Slow Programme": db, "slow programme": db}

    report = {
        "locationRankings": [{
            "name": "SlowLand",
            "keyRisks": [],
        }],
        "incentiveEstimates": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_reliability_warnings(report, by_program, warnings)

    risks = report["locationRankings"][0]["keyRisks"]
    assert any("payment takes forever" in r.lower() for r in risks)
    assert any("zar volatility" in r.lower() for r in risks)


def test_reliability_warnings_long_payment_timeline():
    """Territories with >180 day payment get investor-bankable warning."""
    db = _make_incentive_db_full(
        "Delayed Programme",
        territory="DelayLand",
        payment_timeline_days_min=270,
        payment_timeline_days_max=450,
    )
    by_program = {"Delayed Programme": db, "delayed programme": db}

    report = {
        "locationRankings": [{
            "name": "DelayLand",
            "keyRisks": [],
        }],
        "incentiveEstimates": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_reliability_warnings(report, by_program, warnings)

    risks = report["locationRankings"][0]["keyRisks"]
    assert any("investor-bankable" in r.lower() for r in risks)
    assert len(warnings) > 0


def test_reliability_warnings_short_timeline_no_warning():
    """Territories with <=180 day payment should NOT get reliability warning."""
    db = _make_incentive_db_full(
        "Fast Programme",
        territory="FastLand",
        payment_timeline_days_min=42,
        payment_timeline_days_max=56,
    )
    by_program = {"Fast Programme": db, "fast programme": db}

    report = {
        "locationRankings": [{
            "name": "FastLand",
            "keyRisks": [],
        }],
        "incentiveEstimates": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_reliability_warnings(report, by_program, warnings)

    risks = report["locationRankings"][0]["keyRisks"]
    assert not any("investor-bankable" in r.lower() for r in risks)


# ── _patch_operational_requirements tests ────────────────────────────────────


def test_operational_requirements_service_company_injected():
    """Eligibility rules mentioning 'service company required' should appear in keyRisks."""
    db = _make_incentive_db_full(
        "Local Programme",
        territory="LocalLand",
        eligibility_rules_json='[{"rule":"Local production service company required","required":true}]',
    )
    by_program = {"Local Programme": db, "local programme": db}

    report = {
        "locationRankings": [{
            "name": "LocalLand",
            "keyRisks": [],
        }],
        "incentiveEstimates": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_operational_requirements(report, by_program, warnings)

    risks = report["locationRankings"][0]["keyRisks"]
    assert any("service company" in r.lower() for r in risks)


def test_operational_requirements_minimum_spend_injected():
    """Eligibility rules mentioning 'minimum spend' should appear in keyRisks."""
    db = _make_incentive_db_full(
        "MinSpend Programme",
        territory="MinLand",
        eligibility_rules_json='[{"rule":"Minimum qualifying spend of ZAR 12M","required":true}]',
    )
    by_program = {"MinSpend Programme": db, "minspend programme": db}

    report = {
        "locationRankings": [{
            "name": "MinLand",
            "keyRisks": [],
        }],
        "incentiveEstimates": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_operational_requirements(report, by_program, warnings)

    risks = report["locationRankings"][0]["keyRisks"]
    assert any("minimum" in r.lower() for r in risks)


def test_operational_requirements_skips_non_required_rules():
    """Rules with required=false should NOT be injected."""
    db = _make_incentive_db_full(
        "Optional Programme",
        territory="OptLand",
        eligibility_rules_json='[{"rule":"Local production service company required","required":false}]',
    )
    by_program = {"Optional Programme": db, "optional programme": db}

    report = {
        "locationRankings": [{
            "name": "OptLand",
            "keyRisks": [],
        }],
        "incentiveEstimates": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_operational_requirements(report, by_program, warnings)

    risks = report["locationRankings"][0]["keyRisks"]
    assert len(risks) == 0


# ── _patch_comparable_relevance tests ────────────────────────────────────────


def test_comparable_relevance_flags_budget_gap():
    """Comparables with wildly different budgets get a caveat."""
    report = {
        "executiveSummary": {"budget": "£10M"},
        "comparables": [
            {"title": "Big Budget Film", "budgetRange": "£200M", "relevanceDescription": "Similar genre"},
            {"title": "Similar Film", "budgetRange": "£12M", "relevanceDescription": "Similar genre and budget"},
        ],
    }
    warnings: list[str] = []
    ReportValidator._patch_comparable_relevance(report, [], warnings)

    assert "budget gap" in report["comparables"][0]["relevanceDescription"].lower()
    assert "budget gap" not in report["comparables"][1]["relevanceDescription"].lower()


def test_comparable_relevance_no_budget_no_caveat():
    """If budget can't be parsed, don't add caveats."""
    report = {
        "executiveSummary": {},
        "comparables": [
            {"title": "Some Film", "budgetRange": "£200M", "relevanceDescription": "Similar genre"},
        ],
    }
    warnings: list[str] = []
    ReportValidator._patch_comparable_relevance(report, [], warnings)

    assert "budget gap" not in report["comparables"][0]["relevanceDescription"].lower()


# ── _patch_grant_labelling tests ─────────────────────────────────────────────


def test_grant_labelling_adds_up_to_prefix():
    """Grant fund amounts should be prefixed with 'Up to'."""
    report = {
        "fundingOpportunities": [
            {"type": "Fund", "name": "Test Grant", "notes": "£500,000"},
            {"type": "Fund", "name": "Another Grant", "notes": "Up to £250,000"},
            {"type": "Festival", "name": "Test Festival", "notes": "£50 entry fee"},
        ],
    }
    warnings: list[str] = []
    ReportValidator._patch_grant_labelling(report, warnings)

    assert report["fundingOpportunities"][0]["notes"] == "Up to £500,000"
    assert report["fundingOpportunities"][1]["notes"] == "Up to £250,000"  # unchanged
    assert report["fundingOpportunities"][2]["notes"] == "£50 entry fee"  # festival, unchanged


# ── _parse_money_string tests ────────────────────────────────────────────────


def test_parse_money_string():
    from app.modules.reports.validator import _parse_money_string

    assert _parse_money_string("£22.5M") == 22_500_000
    assert _parse_money_string("$6,500,000") == 6_500_000
    assert _parse_money_string("£18M net") == 18_000_000
    assert _parse_money_string("€12.5M") == 12_500_000
    assert _parse_money_string("£250K") == 250_000
    assert _parse_money_string(None) is None
    assert _parse_money_string("") is None
    assert _parse_money_string("No data") is None


def test_location_rankings_rebate_amount_within_tolerance():
    """rebateAmount should NOT be overridden if within 15% tolerance."""
    db = _make_incentive_db_full(
        "AVEC",
        territory="United Kingdom",
        rate_gross=34,
        rate_net=25.5,
        qualifying_spend_cap_pct=80,
    )
    by_program = {"AVEC": db, "avec": db}

    report = {
        "locationRankings": [{
            "name": "United Kingdom",
            "rebatePercent": "34% / 25.5%",
            "rebateAmount": "~£5,100,000",  # Close to £4,972,500 (within 15%)
            "score": 72,
        }],
        "incentiveEstimates": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_location_rankings(
        report, by_program, warnings, budget_gbp=30_000_000,
    )

    # Should NOT be corrected — within tolerance (5.1M vs 4.97M = 2.6%)
    assert "5,100,000" in report["locationRankings"][0]["rebateAmount"]
    assert not any("rebateAmount corrected" in w for w in warnings)


# ── _patch_territory_deep_dives score + rebate tests ────────────────────────


def test_deep_dive_score_propagated_from_rankings():
    """territoryDeepDives scores should match locationRankings after patching."""
    db = _make_incentive_db_full(
        "AVEC", territory="United Kingdom", rate_gross=34, rate_net=25.5,
    )
    by_program = {"AVEC": db, "avec": db}

    report = {
        "territoryDeepDives": [{
            "name": "United Kingdom",
            "score": 78,  # AI-generated, wrong
            "estimatedRebate": "£4,590,000",
        }],
    }
    ranking_scores = {"United Kingdom": 72}
    warnings: list[str] = []
    ReportValidator._patch_territory_deep_dives(
        report, by_program, warnings,
        ranking_scores=ranking_scores,
    )

    assert report["territoryDeepDives"][0]["score"] == 72
    assert any("score aligned" in w for w in warnings)


# ── crewCostComparison territory filtering tests ─────────────────────────────


def test_crew_cost_comparison_removes_extra_territories():
    """Territories not in locationRankings should be stripped from crew table."""
    report = {
        "locationRankings": [
            {"name": "United Kingdom", "score": 72},
            {"name": "South Africa", "score": 66},
        ],
        "financialAnalysis": {
            "crewCostComparison": [
                {
                    "role": "Director of Photography",
                    "territories": {
                        "United Kingdom": "£800-£2,500/day",
                        "South Africa": "£537-£1,565/day",
                        "Ireland": "£700-£2,200/day",  # Extra — not in rankings
                    },
                },
            ],
        },
    }
    warnings: list[str] = []
    ReportValidator._patch_crew_cost_territories(report, warnings)

    territories = report["financialAnalysis"]["crewCostComparison"][0]["territories"]
    assert "Ireland" not in territories
    assert "United Kingdom" in territories
    assert "South Africa" in territories
    assert any("Ireland" in w for w in warnings)


def test_crew_cost_comparison_noop_when_all_match():
    """No changes when all crew territories are in locationRankings."""
    report = {
        "locationRankings": [
            {"name": "United Kingdom", "score": 72},
        ],
        "financialAnalysis": {
            "crewCostComparison": [
                {
                    "role": "DP",
                    "territories": {"United Kingdom": "£800/day"},
                },
            ],
        },
    }
    warnings: list[str] = []
    ReportValidator._patch_crew_cost_territories(report, warnings)

    assert "United Kingdom" in report["financialAnalysis"]["crewCostComparison"][0]["territories"]
    assert len(warnings) == 0


# ── _patch_production_format tests ───────────────────────────────────────────


def test_production_format_harmonises_scale():
    """scale field should be corrected when it contains wrong format."""
    report = {
        "scale": "Mid-to-High Budget Feature Film",
        "executiveSummary": {"format": "Feature Film"},
    }
    warnings: list[str] = []
    ReportValidator._patch_production_format(report, "TV Series", warnings)

    assert "TV Series" in report["scale"]
    assert "Feature Film" not in report["scale"]
    assert report["executiveSummary"]["format"] == "TV Series"
    assert len(warnings) >= 1


def test_production_format_noop_when_correct():
    """No changes when format already matches."""
    report = {
        "scale": "Mid-Budget TV Series",
        "executiveSummary": {"format": "TV Series"},
    }
    warnings: list[str] = []
    ReportValidator._patch_production_format(report, "TV Series", warnings)

    assert report["scale"] == "Mid-Budget TV Series"
    assert len(warnings) == 0


def test_production_format_noop_when_none():
    """No changes when production_format is None."""
    report = {
        "scale": "Mid-Budget Feature Film",
        "executiveSummary": {"format": "Feature Film"},
    }
    warnings: list[str] = []
    ReportValidator._patch_production_format(report, None, warnings)

    assert report["scale"] == "Mid-Budget Feature Film"
    assert len(warnings) == 0


# ── _patch_shoot_duration_context tests ──────────────────────────────────────


def test_shoot_duration_context_injects_flag_for_long_pilot():
    """Long shoot duration for a TV Pilot should inject a keyFlag."""
    report = {
        "executiveSummary": {
            "shootDays": 320,  # 320 days = ~64 weeks
            "keyFlags": ["Some existing flag"],
        },
    }
    warnings: list[str] = []
    ReportValidator._patch_shoot_duration_context(report, "TV Pilot", warnings)

    flags = report["executiveSummary"]["keyFlags"]
    assert len(flags) == 2
    assert any("shoot timeline" in f.lower() for f in flags)
    assert "64 weeks" in flags[-1]
    assert "tv pilot" in flags[-1].lower()
    assert len(warnings) == 1


def test_shoot_duration_context_noop_for_short_shoot():
    """Normal shoot duration should not trigger a keyFlag."""
    report = {
        "executiveSummary": {
            "shootDays": 40,  # 40 days = ~8 weeks, normal for a pilot
            "keyFlags": [],
        },
    }
    warnings: list[str] = []
    ReportValidator._patch_shoot_duration_context(report, "TV Pilot", warnings)

    assert len(report["executiveSummary"]["keyFlags"]) == 0
    assert len(warnings) == 0


def test_shoot_duration_context_creates_key_flags_list():
    """keyFlags should be created if it doesn't exist."""
    report = {
        "executiveSummary": {
            "shootDays": 150,
        },
    }
    warnings: list[str] = []
    ReportValidator._patch_shoot_duration_context(report, "Feature Film", warnings)

    assert "keyFlags" in report["executiveSummary"]
    assert len(report["executiveSummary"]["keyFlags"]) == 1
    assert "30 weeks" in report["executiveSummary"]["keyFlags"][0]


def test_shoot_duration_context_no_duplicate():
    """Should not add duplicate flag if one already mentions shoot timeline."""
    report = {
        "executiveSummary": {
            "shootDays": 200,
            "keyFlags": ["Extended shoot timeline: already noted by AI"],
        },
    }
    warnings: list[str] = []
    ReportValidator._patch_shoot_duration_context(report, "Feature Film", warnings)

    assert len(report["executiveSummary"]["keyFlags"]) == 1
    assert len(warnings) == 0


# ── Deadline proximity flagging ──────────────────────────────────────────────


def test_deadline_proximity_flags_imminent_deadlines():
    """Funding deadlines within 8 weeks should be inserted into actionTimeline."""
    from datetime import date, timedelta
    # Create a deadline 14 days from now
    soon = (date.today() + timedelta(days=14)).isoformat()
    far = (date.today() + timedelta(days=120)).isoformat()

    report = {
        "executiveSummary": {
            "actionTimeline": [
                {"action": "Register with Georgia Film Office", "deadline": None},
            ],
        },
        "fundingOpportunities": [
            {
                "type": "Festival",
                "name": "DIFF",
                "deadline": f"Feature Submission: {soon}",
            },
            {
                "type": "Festival",
                "name": "Venice",
                "deadline": f"Feature Submission: {far}",
            },
        ],
    }
    warnings: list[str] = []
    ReportValidator._patch_deadline_proximity(report, warnings)

    timeline = report["executiveSummary"]["actionTimeline"]
    # DIFF should be flagged (14 days away), Venice should not (120 days away)
    urgent_actions = [a for a in timeline if "URGENT" in a.get("action", "")]
    assert len(urgent_actions) == 1
    assert "DIFF" in urgent_actions[0]["action"]
    assert soon in urgent_actions[0]["deadline"]
    assert len(warnings) == 1


def test_deadline_proximity_noop_when_no_imminent_deadlines():
    """No action items added when all deadlines are far in the future."""
    from datetime import date, timedelta
    far = (date.today() + timedelta(days=120)).isoformat()

    report = {
        "executiveSummary": {"actionTimeline": []},
        "fundingOpportunities": [
            {"type": "Fund", "name": "Telefilm", "deadline": far},
        ],
    }
    warnings: list[str] = []
    ReportValidator._patch_deadline_proximity(report, warnings)

    assert len(report["executiveSummary"]["actionTimeline"]) == 0
    assert len(warnings) == 0


def test_deadline_proximity_skips_past_deadlines():
    """Deadlines that have already passed should not be flagged."""
    from datetime import date, timedelta
    past = (date.today() - timedelta(days=5)).isoformat()

    report = {
        "executiveSummary": {"actionTimeline": []},
        "fundingOpportunities": [
            {"type": "Festival", "name": "DIFF", "deadline": past},
        ],
    }
    warnings: list[str] = []
    ReportValidator._patch_deadline_proximity(report, warnings)

    assert len(report["executiveSummary"]["actionTimeline"]) == 0


def test_deadline_proximity_no_duplicate():
    """Should not add duplicate if the deadline name is already in the timeline."""
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=10)).isoformat()

    report = {
        "executiveSummary": {
            "actionTimeline": [
                {"action": "Submit to DIFF before deadline", "note": ""},
            ],
        },
        "fundingOpportunities": [
            {"type": "Festival", "name": "DIFF", "deadline": soon},
        ],
    }
    warnings: list[str] = []
    ReportValidator._patch_deadline_proximity(report, warnings)

    timeline = report["executiveSummary"]["actionTimeline"]
    assert len(timeline) == 1  # No new item added
    assert len(warnings) == 0
