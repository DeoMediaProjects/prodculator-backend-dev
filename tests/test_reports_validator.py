"""Unit tests for ReportValidator kept methods.

Covers:
- _compute_corrected_rebate (rebate caps, labour type, ATL deduction, PDV, local_spend)
- _best_incentive (from helpers, re-exported via validator)
- _parse_money_string
- _patch_production_format
- assert_integrity
"""
from __future__ import annotations

import json

from app.modules.reports.validator import ReportValidator, _best_incentive


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_rebate_row(
    rate_gross: float = 30.0,
    rate_type: str = "tax_credit",
    cap_per_person: float | None = None,
    cap_per_person_currency: str | None = None,
    qualifying_spend_cap_pct: float | None = None,
    cap_amount: float | None = None,
    currency: str = "GBP",
    qualifying_spend_type: str | None = None,
    qualifying_spend_labour_pct: float | None = None,
) -> dict:
    """Build a minimal DB row for _compute_corrected_rebate() tests."""
    return {
        "rate_gross": rate_gross,
        "rate_net": rate_gross,
        "rate_type": rate_type,
        "cap_per_person": cap_per_person,
        "cap_per_person_currency": cap_per_person_currency,
        "qualifying_spend_cap_pct": qualifying_spend_cap_pct,
        "cap_amount": cap_amount,
        "currency": currency,
        "rate_tier_json": None,
        "payment_timeline_notes": None,
        "last_verified_at": None,
        "qualifying_spend_type": qualifying_spend_type,
        "qualifying_spend_labour_pct": qualifying_spend_labour_pct,
    }


def _make_best_incentive_row(
    rate_gross: float,
    nationality_requirements: str | None = None,
    spv_eligible: bool | None = None,
    applicable_formats: str | None = None,
) -> dict:
    """Build a minimal incentive row for _best_incentive() tests."""
    return {
        "rate_gross": rate_gross,
        "rate_net": None,
        "nationality_requirements": nationality_requirements,
        "spv_eligible": spv_eligible,
        "applicable_formats": applicable_formats,
    }


def _make_best_incentive_row_bc(
    program: str,
    rate_gross: float,
    nationality_requirements: str | None,
    spv_eligible: bool = False,
) -> dict:
    return {
        "program": program,
        "program_name": program,
        "rate_gross": rate_gross,
        "rate_net": rate_gross,
        "rate_type": "tax_credit",
        "nationality_requirements": nationality_requirements,
        "spv_eligible": spv_eligible,
        "co_production_eligible": True,
        "applicable_formats": None,
        "cap_amount": None,
        "territory": "British Columbia",
    }


def _make_rebate_row_with_cap(
    rate_gross: float = 25.0,
    rate_type: str = "cash_rebate",
    rebate_cap_amount: float | None = None,
    rebate_cap_currency: str | None = None,
) -> dict:
    """Build a DB row with optional rebate_cap_amount for South-Africa-style tests."""
    row = _make_rebate_row(rate_gross=rate_gross, rate_type=rate_type)
    row["rebate_cap_amount"] = rebate_cap_amount
    row["rebate_cap_currency"] = rebate_cap_currency
    return row


def _make_atl_exempt_row(
    rate_gross: float = 34.0,
    rate_type: str = "tax_credit",
    atl_exempt: bool = True,
    qualifying_spend_cap_pct: float | None = 80.0,
) -> dict:
    row = _make_rebate_row(rate_gross=rate_gross, rate_type=rate_type,
                           qualifying_spend_cap_pct=qualifying_spend_cap_pct)
    row["atl_exempt"] = atl_exempt
    return row


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


# ── _best_incentive() nationality filtering ───────────────────────────────────


def test_best_incentive_prefers_pstc_over_cptc():
    """PSTC (foreign-accessible) is selected over higher-rate CPTC (domestic-corp-only)."""
    cptc = _make_best_incentive_row(25.0, nationality_requirements='["CA"]', spv_eligible=False)
    pstc = _make_best_incentive_row(16.0, nationality_requirements=None, spv_eligible=True)
    result = _best_incentive([cptc, pstc])
    assert result["rate_gross"] == 16.0, "PSTC should be selected despite lower rate"


def test_best_incentive_fallback_when_only_domestic_corp():
    """When no foreign-accessible row exists, fall back to the only available row."""
    cptc = _make_best_incentive_row(25.0, nationality_requirements='["CA"]', spv_eligible=False)
    result = _best_incentive([cptc])
    assert result["rate_gross"] == 25.0, "Should fall back gracefully when no alternative"


def test_best_incentive_spv_true_not_excluded():
    """Rows with nationality_requirements set but spv_eligible=True are NOT excluded (e.g. UK AVEC)."""
    avec = _make_best_incentive_row(34.0, nationality_requirements='["GB"]', spv_eligible=True)
    pstc = _make_best_incentive_row(16.0, nationality_requirements=None)
    result = _best_incentive([avec, pstc])
    assert result["rate_gross"] == 34.0, "AVEC (SPV-accessible) should still win"


def test_best_incentive_nationality_none_spv_false_not_excluded():
    """Rows with nationality_requirements=None are always accessible regardless of spv_eligible."""
    row_a = _make_best_incentive_row(30.0, nationality_requirements=None, spv_eligible=False)
    row_b = _make_best_incentive_row(25.0, nationality_requirements=None, spv_eligible=True)
    result = _best_incentive([row_a, row_b])
    assert result["rate_gross"] == 30.0


def test_best_incentive_multiple_domestic_corp_only_fallback():
    """When all rows are domestic-corp-only, picks the highest rate (graceful degradation)."""
    row_a = _make_best_incentive_row(25.0, nationality_requirements='["CA"]', spv_eligible=False)
    row_b = _make_best_incentive_row(35.0, nationality_requirements='["CA"]', spv_eligible=False)
    result = _best_incentive([row_a, row_b])
    assert result["rate_gross"] == 35.0


# ── apply_atl / cap_per_person decoupling ─────────────────────────────────────


def test_georgia_cap_per_person_no_atl_deduction():
    """transferable_tax_credit + cap_per_person must NOT trigger 15% ATL deduction."""
    row = _make_rebate_row(
        rate_gross=30.0,
        rate_type="transferable_tax_credit",
        cap_per_person=500_000.0,
        cap_per_person_currency="USD",
    )
    budget_gbp = 37_600_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    # No ATL deduction — full budget qualifies
    assert result["atl_deduction_amount"] == 0.0
    assert abs(result["qualifying_spend"] - budget_gbp) < 1.0
    # W-2 cap note IS surfaced
    note = result.get("atl_deduction_note", "")
    assert "per-person wage cap" in note.lower() or "500,000" in note


def test_georgia_rebate_amount_correct_with_cap_per_person():
    """Rebate = 30% x full budget when cap_per_person is set on transferable_tax_credit."""
    row = _make_rebate_row(
        rate_gross=30.0,
        rate_type="transferable_tax_credit",
        cap_per_person=500_000.0,
        cap_per_person_currency="USD",
    )
    budget_gbp = 37_600_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    expected_rebate = budget_gbp * 0.30
    assert abs(result["gross_rebate"] - expected_rebate) < 1.0


def test_tax_credit_cap_per_person_still_triggers_atl():
    """tax_credit type + cap_per_person -> ATL deduction still applies (France CIC pattern)."""
    row = _make_rebate_row(
        rate_gross=25.0,
        rate_type="tax_credit",
        cap_per_person=990_000.0,
        qualifying_spend_cap_pct=80.0,
    )
    budget_gbp = 37_600_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    assert result["atl_deduction_amount"] > 0, "ATL deduction must still be applied"


def test_transferable_tax_credit_without_cap_per_person_no_atl():
    """transferable_tax_credit without cap_per_person -- no ATL deduction, no note."""
    row = _make_rebate_row(rate_gross=30.0, rate_type="transferable_tax_credit")
    budget_gbp = 37_600_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    assert result["atl_deduction_amount"] == 0.0
    assert result.get("atl_deduction_note") is None


# ── qualifying_spend_type logic ────────────────────────────────────────────────


def test_labour_type_reduces_qualifying_spend_to_labour_pct():
    """qualifying_spend_type='labour' with explicit labour_pct reduces qualifying spend."""
    row = _make_rebate_row(
        rate_gross=16.0,
        rate_type="tax_credit",
        qualifying_spend_type="labour",
        qualifying_spend_labour_pct=35.0,
    )
    budget_gbp = 15_040_000.0  # $20M at 0.7520
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    expected_qs = budget_gbp * 0.35
    assert abs(result["qualifying_spend_before_atl"] - expected_qs) < 1.0, (
        "qualifying_spend_before_atl should reflect 35% labour fraction"
    )
    assert result["qualifying_spend_pct"] == 35.0
    assert "qualifying_spend_note" in result
    assert "labour" in result["qualifying_spend_note"].lower()


def test_labour_type_default_35pct_when_no_labour_pct_set():
    """qualifying_spend_type='labour' with no qualifying_spend_labour_pct defaults to 35%."""
    row = _make_rebate_row(
        rate_gross=16.0,
        rate_type="tax_credit",
        qualifying_spend_type="labour",
        qualifying_spend_labour_pct=None,
    )
    budget_gbp = 15_040_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    expected_qs = budget_gbp * 0.35
    assert abs(result["qualifying_spend_before_atl"] - expected_qs) < 1.0


def test_pstc_rebate_significantly_lower_than_full_budget():
    """Canada PSTC pattern: 16% on 35% labour = much lower than 16% on full budget.

    ATL deduction is NOT applied to labour-type credits: the 35% labour pct
    already represents a BTL-weighted estimate of qualifying spend, so a second
    15% ATL haircut would double-discount the base.
    Correct: 16% x (35% x 15.04M) = 842K (no ATL deduction)
    """
    row = _make_rebate_row(
        rate_gross=16.0,
        rate_type="tax_credit",
        qualifying_spend_type="labour",
        qualifying_spend_labour_pct=35.0,
    )
    budget_gbp = 15_040_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    # No ATL deduction for labour credits — qualifying_spend = 35% x budget
    expected_qs = budget_gbp * 0.35
    expected_rebate = expected_qs * 0.16
    assert abs(result["gross_rebate"] - expected_rebate) < 1.0
    # Must be well below the full-budget calculation (16% x 100% budget)
    full_budget_rebate = budget_gbp * 0.16
    assert result["gross_rebate"] < full_budget_rebate * 0.5, (
        "Labour-only rebate should be substantially less than full-budget calculation"
    )


def test_pdv_type_reduces_qualifying_spend_to_pdv_pct():
    """qualifying_spend_type='pdv' with explicit pdv_pct reduces qualifying spend."""
    row = _make_rebate_row(
        rate_gross=30.0,
        rate_type="tax_offset",
        qualifying_spend_type="pdv",
        qualifying_spend_labour_pct=15.0,
    )
    budget_gbp = 15_040_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    expected_qs = budget_gbp * 0.15
    assert abs(result["qualifying_spend_before_atl"] - expected_qs) < 1.0
    assert result["qualifying_spend_pct"] == 15.0
    assert "qualifying_spend_note" in result
    assert "post-production" in result["qualifying_spend_note"].lower()


def test_pdv_rebate_significantly_lower_than_full_budget():
    """Australia PDV Offset pattern: 30% on 15% PDV = much lower than 30% on full budget."""
    row = _make_rebate_row(
        rate_gross=30.0,
        rate_type="tax_offset",
        qualifying_spend_type="pdv",
        qualifying_spend_labour_pct=15.0,
    )
    budget_gbp = 15_040_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    expected_rebate = budget_gbp * 0.15 * 0.30
    assert abs(result["gross_rebate"] - expected_rebate) < 1.0
    full_budget_rebate = budget_gbp * 0.30
    assert result["gross_rebate"] < full_budget_rebate * 0.25, (
        "PDV-only rebate should be much less than full-budget calculation"
    )


def test_local_spend_type_preserves_full_budget_but_adds_note():
    """qualifying_spend_type='local_spend' with no cap_pct: calculation unchanged, note added."""
    row = _make_rebate_row(
        rate_gross=30.0,
        rate_type="cash_rebate",
        qualifying_spend_type="local_spend",
    )
    budget_gbp = 15_040_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    # Qualifying spend should be the full budget (no cap_pct set)
    assert abs(result["qualifying_spend_before_atl"] - budget_gbp) < 1.0
    assert "qualifying_spend_note" in result
    assert "in-territory" in result["qualifying_spend_note"].lower()


def test_local_spend_with_cap_pct_applies_cap():
    """local_spend + qualifying_spend_cap_pct=75 applies the cap (Italy pattern)."""
    row = _make_rebate_row(
        rate_gross=40.0,
        rate_type="tax_credit",
        qualifying_spend_type="local_spend",
        qualifying_spend_cap_pct=75.0,
    )
    budget_gbp = 15_040_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    expected_qs_before_atl = budget_gbp * 0.75
    assert abs(result["qualifying_spend_before_atl"] - expected_qs_before_atl) < 1.0


def test_total_type_with_null_preserves_existing_behaviour():
    """qualifying_spend_type=None (NULL/default) behaves identically to 'total'."""
    row_default = _make_rebate_row(rate_gross=30.0, rate_type="cash_rebate")
    row_explicit = _make_rebate_row(
        rate_gross=30.0, rate_type="cash_rebate", qualifying_spend_type="total"
    )
    budget_gbp = 15_040_000.0
    r1 = ReportValidator._compute_corrected_rebate(row_default, budget_gbp, {})
    r2 = ReportValidator._compute_corrected_rebate(row_explicit, budget_gbp, {})
    assert r1 is not None and r2 is not None
    assert r1["gross_rebate"] == r2["gross_rebate"]
    assert r1.get("qualifying_spend_note") is None
    assert r2.get("qualifying_spend_note") is None


# ── Group A: rebate_cap_amount enforcement ────────────────────────────────────


def test_rebate_cap_enforced_south_africa_static_fx():
    """25% x 20M = 5M but R25M cap ~ 1.05M at static rate 23.8 -- cap wins."""
    row = _make_rebate_row_with_cap(
        rate_gross=25.0,
        rate_type="cash_rebate",
        rebate_cap_amount=25_000_000.0,
        rebate_cap_currency="ZAR",
    )
    budget_gbp = 20_000_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    # Uncapped rebate would be 25% x 20M = 5M; cap = 25M / 23.8 ~ 1_050_420
    expected_cap_gbp = 25_000_000.0 / 23.8
    assert abs(result["gross_rebate"] - expected_cap_gbp) < 1.0
    assert abs(result["net_rebate"] - expected_cap_gbp) < 1.0
    assert result["gross_rebate"] < 2_000_000  # well below uncapped 5M
    assert result.get("rebate_cap_note") is not None
    assert "R25M" in result["rebate_cap_note"] or "25M" in result["rebate_cap_note"]


def test_rebate_cap_not_applied_when_below_cap():
    """Small budget produces a rebate below the R25M cap -- cap does not trigger."""
    row = _make_rebate_row_with_cap(
        rate_gross=25.0,
        rate_type="cash_rebate",
        rebate_cap_amount=25_000_000.0,
        rebate_cap_currency="ZAR",
    )
    budget_gbp = 100_000.0  # 25% x 100K = 25K -- far below R25M ~ 1.05M
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    assert abs(result["gross_rebate"] - 25_000.0) < 1.0
    assert result.get("rebate_cap_note") is None


def test_rebate_cap_with_explicit_fx_rate():
    """Explicit fx_rate_to_gbp=24.0 takes precedence over the static fallback."""
    row = _make_rebate_row_with_cap(
        rate_gross=25.0,
        rate_type="cash_rebate",
        rebate_cap_amount=25_000_000.0,
        rebate_cap_currency="ZAR",
    )
    budget_gbp = 20_000_000.0
    result = ReportValidator._compute_corrected_rebate(
        row, budget_gbp, {}, fx_rate_to_gbp=24.0
    )
    assert result is not None
    expected_cap_gbp = 25_000_000.0 / 24.0  # ~ 1_041_666
    assert abs(result["gross_rebate"] - expected_cap_gbp) < 1.0
    assert result.get("rebate_cap_note") is not None


def test_rebate_cap_no_cap_amount_leaves_rebate_unchanged():
    """When rebate_cap_amount is None, existing calculation is unchanged."""
    row = _make_rebate_row_with_cap(
        rate_gross=25.0,
        rate_type="cash_rebate",
        rebate_cap_amount=None,
        rebate_cap_currency=None,
    )
    budget_gbp = 20_000_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    assert abs(result["gross_rebate"] - 5_000_000.0) < 1.0
    assert result.get("rebate_cap_note") is None


# ── Group C: BC PSTC wins over FIBC for foreign producer ─────────────────────


def test_bc_pstc_wins_over_fibc_for_foreign_producer():
    """BC PSTC (NULL nationality) should be selected over FIBC (CA-only) for foreign producers."""
    fibc = _make_best_incentive_row_bc(
        "BC Film Incentive BC Tax Credit (FIBC)",
        rate_gross=40.0,
        nationality_requirements='["CA"]',
        spv_eligible=False,
    )
    pstc = _make_best_incentive_row_bc(
        "BC Production Services Tax Credit (PSTC)",
        rate_gross=36.0,
        nationality_requirements=None,
        spv_eligible=False,
    )
    result = _best_incentive([fibc, pstc])
    assert result["program"] == "BC Production Services Tax Credit (PSTC)"


def test_bc_fibc_wins_when_only_option():
    """When PSTC is absent, FIBC is returned (graceful degradation)."""
    fibc = _make_best_incentive_row_bc(
        "BC Film Incentive BC Tax Credit (FIBC)",
        rate_gross=40.0,
        nationality_requirements='["CA"]',
        spv_eligible=False,
    )
    result = _best_incentive([fibc])
    assert result["program"] == "BC Film Incentive BC Tax Credit (FIBC)"


# ── Group J: atl_exempt flag skips ATL deduction ─────────────────────────────


def test_atl_exempt_true_skips_atl_deduction():
    """atl_exempt=True (AVEC) -> no ATL deduction applied regardless of rate_type."""
    row = _make_atl_exempt_row(rate_gross=34.0, rate_type="tax_credit", atl_exempt=True)
    budget_gbp = 25_000_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    assert result["atl_deduction_amount"] == 0.0
    assert result.get("atl_deduction_note") is None


def test_atl_exempt_true_avec_rebate_uses_full_80pct_qualifying_spend():
    """AVEC: 34% x 80% x 25M = 6,800,000 (no 15% ATL deduction)."""
    row = _make_atl_exempt_row(
        rate_gross=34.0, rate_type="tax_credit",
        atl_exempt=True, qualifying_spend_cap_pct=80.0,
    )
    budget_gbp = 25_000_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    expected = 25_000_000.0 * 0.80 * 0.34
    assert abs(result["gross_rebate"] - expected) < 1.0


def test_atl_exempt_false_applies_atl_deduction_as_normal():
    """atl_exempt=False (or absent) -> ATL deduction applied to tax_credit type.

    ATL deduction = 15% of TOTAL budget (not 15% of qualifying spend).
    qualifying_spend_before_atl = 80% x 25M = 20M
    atl_deduction = 15% x 25M = 3.75M
    net_qualifying_spend = 20M - 3.75M = 16.25M
    gross_rebate = 16.25M x 34% = 5,525,000
    """
    row = _make_atl_exempt_row(
        rate_gross=34.0, rate_type="tax_credit",
        atl_exempt=False, qualifying_spend_cap_pct=80.0,
    )
    budget_gbp = 25_000_000.0
    result = ReportValidator._compute_corrected_rebate(row, budget_gbp, {})
    assert result is not None
    # ATL deduction = 15% of total budget
    atl_deduction = budget_gbp * 0.15
    qs_before_atl = budget_gbp * 0.80
    expected_rebate = (qs_before_atl - atl_deduction) * 0.34  # = 5,525,000
    assert abs(result["gross_rebate"] - expected_rebate) < 1.0
    assert result["atl_deduction_amount"] > 0


# ── assert_integrity tests ───────────────────────────────────────────────────

class TestAssertIntegrity:
    """Tests for ReportValidator.assert_integrity (builder-path assertions)."""

    @staticmethod
    def _make_builder_report(territories=None):
        """Minimal report that looks like builder output."""
        territories = territories or ["United Kingdom"]
        return {
            "genre": "Drama",
            "tone": "Tense",
            "scale": "Medium",
            "complexity": "Medium",
            "locationRankings": [
                {
                    "name": t,
                    "score": 70 - i * 5,
                    "costEfficiency": 60,
                    "crewDepth": 65,
                    "infrastructure": 70,
                    "incentiveStrength": 75,
                    "incentiveReliability": 80,
                    "currencyAdvantage": 55,
                    "reasoning": ["Good incentives"],
                    "keyAdvantages": ["Strong crew base"],
                    "keyRisks": ["Weather risk"],
                }
                for i, t in enumerate(territories)
            ],
            "incentiveEstimates": [
                {"programName": "AVEC", "territory": "United Kingdom"}
            ],
            "executiveSummary": {
                "keyInsights": "UK is recommended.",
                "recommendedTerritory": territories[0],
                "recommendedTerritoryScore": 70,
            },
        }

    @staticmethod
    def _make_datasets(territories=None):
        territories = territories or ["United Kingdom"]
        return {
            "incentives": [
                {"program_name": "AVEC", "territory": t, "rate_gross": 25}
                for t in territories
            ],
        }

    def test_clean_report_no_warnings(self):
        report = self._make_builder_report()
        datasets = self._make_datasets()
        result, warnings = ReportValidator.assert_integrity(report, datasets)
        assert result is report
        structure_warnings = [
            w for w in warnings
            if any(tag in w for tag in ("[structure]", "[scores]", "[financial]", "[coverage]", "[narrative]"))
        ]
        assert len(structure_warnings) == 0

    def test_missing_genre_warned(self):
        report = self._make_builder_report()
        report["genre"] = None
        _, warnings = ReportValidator.assert_integrity(report, self._make_datasets())
        assert any("genre" in w for w in warnings)

    def test_score_out_of_bounds_clamped(self):
        report = self._make_builder_report()
        report["locationRankings"][0]["costEfficiency"] = 150
        result, warnings = ReportValidator.assert_integrity(report, self._make_datasets())
        assert result["locationRankings"][0]["costEfficiency"] == 100
        assert any("clamped" in w for w in warnings)

    def test_unknown_programme_warned(self):
        report = self._make_builder_report()
        report["incentiveEstimates"].append(
            {"programName": "Nonexistent Programme", "territory": "UK"}
        )
        _, warnings = ReportValidator.assert_integrity(report, self._make_datasets())
        assert any("Nonexistent Programme" in w for w in warnings)

    def test_territory_without_db_data_warned(self):
        report = self._make_builder_report(territories=["Atlantis"])
        datasets = self._make_datasets()  # only has UK
        _, warnings = ReportValidator.assert_integrity(report, datasets)
        assert any("Atlantis" in w for w in warnings)

    def test_missing_reasoning_warned(self):
        report = self._make_builder_report()
        report["locationRankings"][0]["reasoning"] = None
        _, warnings = ReportValidator.assert_integrity(report, self._make_datasets())
        assert any("reasoning" in w for w in warnings)

    def test_rankings_sorted_by_score(self):
        report = self._make_builder_report(territories=["UK", "Canada", "Ireland"])
        report["locationRankings"][0]["score"] = 50
        report["locationRankings"][1]["score"] = 80
        report["locationRankings"][2]["score"] = 65
        datasets = self._make_datasets(territories=["UK", "Canada", "Ireland"])
        result, _ = ReportValidator.assert_integrity(report, datasets)
        scores = [loc["score"] for loc in result["locationRankings"]]
        assert scores == sorted(scores, reverse=True)

    def test_executive_summary_updated_after_sort(self):
        report = self._make_builder_report(territories=["UK", "Canada"])
        report["locationRankings"][0]["score"] = 50  # UK lower
        report["locationRankings"][1]["score"] = 80  # Canada higher
        # Set initial headline data pointing at UK (the original #1)
        report["executiveSummary"]["recommendedTerritoryRebate"] = "£1,000,000"
        report["executiveSummary"]["headlineNetBudget"] = "approximately £19,000,000"
        datasets = self._make_datasets(territories=["UK", "Canada"])
        # Add territory financials so headline can be refreshed
        datasets["_territory_financials"] = {
            "UK": {"gross_rebate": "£1,000,000", "headline_net_budget": "approximately £19,000,000"},
            "Canada": {
                "gross_rebate": "C$2,000,000",
                "net_rebate": "C$1,500,000",
                "headline_net_budget": "approximately C$34,000,000",
            },
        }
        datasets["incentives"] = [
            {"program_name": "AVEC", "territory": "UK", "rate_gross": 25,
             "payment_timeline_notes": "6-12 months via HMRC"},
            {"program_name": "PSTC", "territory": "Canada", "rate_gross": 16,
             "payment_timeline_notes": "4-12 months via CRA"},
        ]
        result, _ = ReportValidator.assert_integrity(report, datasets)
        assert result["executiveSummary"]["recommendedTerritory"] == "Canada"
        assert result["executiveSummary"]["recommendedTerritoryScore"] == 80
        # Financial headline must match Canada, not UK
        assert result["executiveSummary"]["recommendedTerritoryRebate"] == "C$1,500,000"
        assert "C$34,000,000" in result["executiveSummary"]["headlineNetBudget"]
        assert "CRA" in result["executiveSummary"]["recommendedTerritoryPaymentSpeed"]

    def test_null_narrative_dimensions_warned(self):
        report = self._make_builder_report()
        report["locationRankings"][0]["costEfficiency"] = None
        _, warnings = ReportValidator.assert_integrity(report, self._make_datasets())
        assert any("costEfficiency" in w and "None" in w for w in warnings)
