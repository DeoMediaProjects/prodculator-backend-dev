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


# ── _patch_financial_calculations tests ──────────────────────────────────────


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


def test_financial_calc_applies_qualifying_spend_cap():
    """Rebate should be calculated on 80% of budget, not 100%."""
    db = _make_incentive_db_full(
        "Test Programme",
        territory="TestLand",
        rate_gross=40.0,
        rate_net=40.0,
        qualifying_spend_cap_pct=80.0,
    )
    by_program = {"Test Programme": db, "test programme": db}

    report = {
        "executiveSummary": {"budget": "£10M"},
        "incentiveEstimates": [{
            "program": "Test Programme",
            "territory": "TestLand",
            "estimatedRebate": "£4,000,000",  # Wrong: 40% of full £10M
        }],
        "locationRankings": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_financial_calculations(report, by_program, warnings)

    est = report["incentiveEstimates"][0]
    # Should be corrected: 80% of £10M = £8M, then 40% of £8M = £3.2M
    assert "3,200,000" in est["estimatedRebate"]
    assert len(warnings) > 0


def test_financial_calc_no_cap_uses_full_budget():
    """If qualifying_spend_cap_pct is absent, use full budget."""
    db = _make_incentive_db_full(
        "NoCap Programme",
        territory="TestLand",
        rate_gross=30.0,
        rate_net=30.0,
        qualifying_spend_cap_pct=None,
    )
    by_program = {"NoCap Programme": db, "nocap programme": db}

    report = {
        "executiveSummary": {"budget": "£10M"},
        "incentiveEstimates": [{
            "program": "NoCap Programme",
            "territory": "TestLand",
            "estimatedRebate": "£3,000,000",  # Correct: 30% of full £10M
        }],
        "locationRankings": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_financial_calculations(report, by_program, warnings)

    # Should NOT be overridden — it's within tolerance
    assert "3,000,000" in report["incentiveEstimates"][0]["estimatedRebate"]


def test_financial_calc_budget_exceeds_cap_selects_alternative():
    """When budget exceeds programme cap, should fall back to alternative programme."""
    # IFTC-like: cap at £23.5M
    iftc = _make_incentive_db_full(
        "Enhanced Credit",
        territory="TestLand",
        rate_gross=53.0,
        rate_net=39.75,
        qualifying_spend_cap_pct=80.0,
        cap_amount=23_500_000.0,
    )
    # AVEC-like: no cap
    avec = _make_incentive_db_full(
        "Standard Credit",
        territory="TestLand",
        rate_gross=34.0,
        rate_net=25.5,
        qualifying_spend_cap_pct=80.0,
        cap_amount=None,
    )
    by_program = {
        "Enhanced Credit": iftc, "enhanced credit": iftc,
        "Standard Credit": avec, "standard credit": avec,
    }

    report = {
        "executiveSummary": {"budget": "£25M"},
        "incentiveEstimates": [{
            "program": "Enhanced Credit",
            "territory": "TestLand",
            "estimatedRebate": "£13,250,000",  # Wrong: 53% of £25M
        }],
        "locationRankings": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_financial_calculations(report, by_program, warnings)

    est = report["incentiveEstimates"][0]
    # Should use Standard Credit (34%/25.5% net) on 80% of £25M = £20M
    # Net rebate: £20M * 25.5% = £5,100,000
    assert "5,100,000" in est["estimatedRebate"]
    assert "programme" in (est.get("programmeNote") or "").lower() or len(warnings) > 0


def test_financial_calc_per_person_cap_reduces_qualifying_spend():
    """Per-person ATL cap should reduce qualifying spend and flag it."""
    db = _make_incentive_db_full(
        "Capped Programme",
        territory="TestLand",
        rate_gross=30.0,
        rate_net=30.0,
        qualifying_spend_cap_pct=80.0,  # 80% cap + per-person cap stack
        cap_per_person=3_000_000.0,
        cap_per_person_currency="HUF",
    )
    by_program = {"Capped Programme": db, "capped programme": db}

    report = {
        "executiveSummary": {"budget": "£10M"},
        "incentiveEstimates": [{
            "program": "Capped Programme",
            "territory": "TestLand",
            "estimatedRebate": "£3,000,000",  # Wrong: 30% of full £10M
        }],
        "locationRankings": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_financial_calculations(report, by_program, warnings)

    est = report["incentiveEstimates"][0]
    # Budget £10M, 80% qs cap → £8M, then 25% ATL deduction (v3) → £8M - £2.5M = £5.5M
    # 30% of £5.5M = £1,650,000
    # Deviation: |3M - 1.65M| / 1.65M = 82% → exceeds 15% tolerance
    assert "1,650,000" in est["estimatedRebate"]
    assert len(warnings) > 0


def test_budget_scenario_patches_intermediate_fields_and_programme():
    """budgetScenarios should have corrected atlDeduction, netQualifyingSpend, programme, and rates."""
    # IFTC-like: cap at £20M
    iftc = _make_incentive_db_full(
        "IFTC",
        territory="United Kingdom",
        rate_gross=53.0,
        rate_net=39.75,
        qualifying_spend_cap_pct=80.0,
        cap_amount=20_000_000.0,
        cap_per_person=500_000.0,
    )
    # AVEC-like: no cap, also has ATL cap
    avec = _make_incentive_db_full(
        "AVEC",
        territory="United Kingdom",
        rate_gross=34.0,
        rate_net=25.5,
        qualifying_spend_cap_pct=80.0,
        cap_amount=None,
        cap_per_person=500_000.0,
    )
    by_program = {
        "IFTC": iftc, "iftc": iftc,
        "AVEC": avec, "avec": avec,
    }

    report = {
        "executiveSummary": {"budget": "£30M"},
        "incentiveEstimates": [],
        "locationRankings": [],
        "financialAnalysis": {
            "budgetScenarios": [{
                "territory": "United Kingdom",
                "programme": "IFTC",  # Wrong — should switch to AVEC
                "totalBudget": "£30,000,000",
                "qualifyingSpendPct": "100%",
                "qualifyingSpend": "£30,000,000",
                "atlDeduction": "£6,000,000",
                "netQualifyingSpend": "£18,000,000",
                "rateGross": "53%",
                "rateNet": "39.75%",
                "grossRebate": "£15,900,000",
                "netRebate": "£11,925,000",
                "netBudget": "£18,075,000",
            }],
        },
    }
    warnings: list[str] = []
    ReportValidator._patch_financial_calculations(
        report, by_program, warnings, budget_gbp_override=30_000_000,
    )

    scenario = report["financialAnalysis"]["budgetScenarios"][0]

    # Programme should be switched to AVEC
    assert scenario["programme"] == "AVEC"

    # Rates should be AVEC (34% / 25.5%), not IFTC (53% / 39.75%)
    assert scenario["rateGross"] == "34%"
    assert scenario["rateNet"] == "25.5%"

    # Qualifying spend: 80% of £30M = £24M (before ATL)
    assert "24,000,000" in scenario["qualifyingSpend"]

    # ATL deduction: 25% of £30M = £7.5M
    assert "7,500,000" in scenario["atlDeduction"]

    # Net qualifying spend: £24M - £7.5M = £16.5M
    assert "16,500,000" in scenario["netQualifyingSpend"]

    # Net rebate: £16.5M * 25.5% = £4,207,500
    assert "4,207,500" in scenario["netRebate"]

    # Notes should mention the programme switch
    assert "AVEC" in (scenario.get("notes") or "")
    assert len(warnings) > 0


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


# ── _patch_location_rankings rebateAmount tests ─────────────────────────────


def test_location_rankings_rebate_amount_corrected():
    """rebateAmount in locationRankings should be corrected when it deviates >15%."""
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
            "rebateAmount": "£11,925,000",  # Wrong — AI hallucinated
            "score": 72,
        }],
        "incentiveEstimates": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_location_rankings(
        report, by_program, warnings, budget_gbp=30_000_000,
    )

    # The corrected amount should be 30M * 80% * 25.5% = £6,120,000
    assert "6,120,000" in report["locationRankings"][0]["rebateAmount"]
    assert any("rebateAmount corrected" in w for w in warnings)


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
            "rebateAmount": "~£6,200,000",  # Close enough to £6,120,000
            "score": 72,
        }],
        "incentiveEstimates": [],
    }
    warnings: list[str] = []
    ReportValidator._patch_location_rankings(
        report, by_program, warnings, budget_gbp=30_000_000,
    )

    # Should NOT be corrected — within tolerance
    assert "6,200,000" in report["locationRankings"][0]["rebateAmount"]
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


def test_deep_dive_rebate_corrected():
    """territoryDeepDives estimatedRebate should be corrected when deviating."""
    db = _make_incentive_db_full(
        "AVEC",
        territory="United Kingdom",
        rate_gross=34,
        rate_net=25.5,
        qualifying_spend_cap_pct=80,
    )
    by_program = {"AVEC": db, "avec": db}

    report = {
        "territoryDeepDives": [{
            "name": "United Kingdom",
            "score": 72,
            "estimatedRebate": "£11,925,000",  # Hallucinated
        }],
    }
    warnings: list[str] = []
    ReportValidator._patch_territory_deep_dives(
        report, by_program, warnings,
        budget_gbp=30_000_000,
    )

    assert "6,120,000" in report["territoryDeepDives"][0]["estimatedRebate"]
    assert any("estimatedRebate corrected" in w for w in warnings)


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
