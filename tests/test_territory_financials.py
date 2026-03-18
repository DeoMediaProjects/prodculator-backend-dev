"""Unit tests for ReportService._pre_compute_territory_financials.

These tests exercise the service-layer pre-computation of all monetary figures
(qualifying spend, ATL deductions, rebate amounts, FX conversion, crew rates)
that are injected into the AI prompt so the AI can copy them verbatim.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.reports.service import ReportService


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_service() -> ReportService:
    """Build a ReportService with a stubbed Supabase client."""
    svc = ReportService.__new__(ReportService)
    svc.supabase = MagicMock()
    return svc


def _make_incentive(
    territory: str = "United Kingdom",
    program_name: str = "AVEC",
    rate_gross: float = 34.0,
    rate_net: float | None = 25.5,
    rate_type: str = "tax_credit",
    currency: str = "GBP",
    qualifying_spend_cap_pct: float | None = 80.0,
    cap_amount: float | None = None,
) -> dict:
    return {
        "territory": territory,
        "program_name": program_name,
        "rate_gross": rate_gross,
        "rate_net": rate_net,
        "rate_type": rate_type,
        "currency": currency,
        "qualifying_spend_cap_pct": qualifying_spend_cap_pct,
        "cap_amount": cap_amount,
        "cap_per_person": None,
        "cap_per_person_currency": None,
        "rate_tier_json": None,
        "payment_timeline_notes": None,
        "last_verified_at": None,
        "scope": "national",
        "parent_territory": None,
        "stackable_with": None,
    }


def _make_datasets(
    budget_gbp: float,
    incentives: list[dict],
    *,
    budget_currency: str = "GBP",
    budget_original_amount: float | None = None,
    fx_rates_from_budget: dict | None = None,
    crew_costs: list[dict] | None = None,
) -> dict:
    return {
        "_budget_gbp": {"converted": budget_gbp},
        "_budget_currency": budget_currency,
        "_budget_amount": budget_original_amount or budget_gbp,
        "_fx_rates_from_budget": fx_rates_from_budget or {},
        "incentives": incentives,
        "crew_costs": crew_costs or [],
    }


# ── Basic output shape ────────────────────────────────────────────────────────


def test_output_shape_gbp_territory():
    """Should produce a well-formed entry for a GBP territory."""
    svc = _make_service()
    incentive = _make_incentive(
        territory="United Kingdom",
        program_name="AVEC",
        rate_gross=34.0,
        rate_net=25.5,
        qualifying_spend_cap_pct=80.0,
        currency="GBP",
    )
    datasets = _make_datasets(budget_gbp=10_000_000, incentives=[incentive])

    svc._pre_compute_territory_financials(datasets)

    tf = datasets["_territory_financials"]
    assert "United Kingdom" in tf

    entry = tf["United Kingdom"]
    # All required keys present
    for key in (
        "currency", "currency_symbol", "total_budget", "qualifying_spend_pct",
        "qualifying_spend", "atl_deduction", "net_qualifying_spend",
        "rate", "rate_gross", "rate_net", "gross_rebate", "net_rebate",
        "net_budget", "headline_net_budget", "programme", "fx_note",
        "crew_rates", "budget_currency", "budget_symbol",
    ):
        assert key in entry, f"Missing key: {key}"


def test_gbp_territory_rebate_amounts():
    """AVEC (34%/25.5% net, 80% QS cap, tax_credit) on £10M budget.

    ATL deduction on tax_credit: 15% of £10M = £1.5M
    qualifying_spend (before ATL) = 80% × £10M = £8M
    net_qualifying_spend = £8M - £1.5M = £6.5M
    gross_rebate = £6.5M × 34% = £2,210,000
    net_rebate = £6.5M × 25.5% = £1,657,500
    """
    svc = _make_service()
    incentive = _make_incentive(
        qualifying_spend_cap_pct=80.0,
        rate_gross=34.0,
        rate_net=25.5,
        rate_type="tax_credit",
        currency="GBP",
    )
    datasets = _make_datasets(budget_gbp=10_000_000, incentives=[incentive])

    svc._pre_compute_territory_financials(datasets)
    entry = datasets["_territory_financials"]["United Kingdom"]

    assert "1,657,500" in entry["net_rebate"]
    assert "2,210,000" in entry["gross_rebate"]
    assert "8,000,000" in entry["qualifying_spend"]
    assert "6,500,000" in entry["net_qualifying_spend"]
    assert "80%" in entry["qualifying_spend_pct"]
    assert entry["atl_deduction"] is not None
    assert "1,500,000" in entry["atl_deduction"]


def test_cash_rebate_no_atl_deduction():
    """Cash rebate programmes should not have an ATL deduction."""
    svc = _make_service()
    incentive = _make_incentive(
        territory="Hungary",
        program_name="NFI Cash Rebate",
        rate_gross=30.0,
        rate_net=30.0,
        rate_type="cash_rebate",
        currency="GBP",  # use GBP to avoid FX complexity
        qualifying_spend_cap_pct=None,
    )
    datasets = _make_datasets(budget_gbp=10_000_000, incentives=[incentive])

    svc._pre_compute_territory_financials(datasets)
    entry = datasets["_territory_financials"]["Hungary"]

    assert entry["atl_deduction"] is None
    assert "3,000,000" in entry["net_rebate"]  # 30% of £10M


# ── Missing budget guard ──────────────────────────────────────────────────────


def test_absent_when_no_budget_gbp():
    """_territory_financials should be absent if _budget_gbp is missing."""
    svc = _make_service()
    datasets = {
        "_budget_currency": "GBP",
        "incentives": [_make_incentive()],
        "crew_costs": [],
        # No _budget_gbp key
    }
    svc._pre_compute_territory_financials(datasets)

    assert "_territory_financials" not in datasets


def test_absent_when_budget_gbp_zero():
    """_territory_financials should be absent if budget_gbp is 0."""
    svc = _make_service()
    datasets = {
        "_budget_gbp": {"converted": 0},
        "_budget_currency": "GBP",
        "incentives": [_make_incentive()],
        "crew_costs": [],
    }
    svc._pre_compute_territory_financials(datasets)

    assert "_territory_financials" not in datasets


# ── FX conversion ─────────────────────────────────────────────────────────────


def test_fx_conversion_non_gbp_territory():
    """Amounts for a EUR territory should display in EUR using the FX rate.

    Hungary: 30% cash rebate in EUR. Budget = £10M GBP, EUR rate = 1.17.
    total_budget = £10M × 1.17 = €11,700,000
    net_rebate = £10M × 30% × 1.17 = £3M × 1.17 = €3,510,000
    """
    svc = _make_service()
    incentive = _make_incentive(
        territory="Hungary",
        program_name="NFI Cash Rebate",
        rate_gross=30.0,
        rate_net=30.0,
        rate_type="cash_rebate",
        currency="EUR",
        qualifying_spend_cap_pct=None,
    )
    datasets = _make_datasets(
        budget_gbp=10_000_000,
        incentives=[incentive],
        budget_currency="GBP",
        budget_original_amount=10_000_000,
        fx_rates_from_budget={"EUR": {"rate": 1.17, "rate_date": "2026-03-17"}},
    )

    svc._pre_compute_territory_financials(datasets)
    entry = datasets["_territory_financials"]["Hungary"]

    assert entry["currency"] == "EUR"
    assert "11,700,000" in entry["total_budget"]
    assert "3,510,000" in entry["net_rebate"]
    assert entry["fx_note"] is not None
    assert "1.17" in (entry["fx_note"] or "")


# ── Budget-cap switching ──────────────────────────────────────────────────────


def test_budget_cap_switches_to_alternative_programme():
    """When budget exceeds IFTC cap (£20M), should use AVEC rate (34%/25.5%)."""
    iftc = _make_incentive(
        territory="United Kingdom",
        program_name="IFTC",
        rate_gross=53.0,
        rate_net=39.75,
        qualifying_spend_cap_pct=80.0,
        cap_amount=20_000_000.0,
    )
    avec = _make_incentive(
        territory="United Kingdom",
        program_name="AVEC",
        rate_gross=34.0,
        rate_net=25.5,
        qualifying_spend_cap_pct=80.0,
        cap_amount=None,
    )
    svc = _make_service()
    datasets = _make_datasets(budget_gbp=30_000_000, incentives=[iftc, avec])

    svc._pre_compute_territory_financials(datasets)
    entry = datasets["_territory_financials"]["United Kingdom"]

    # Should use AVEC rates (34% / 25.5%), not IFTC (53% / 39.75%)
    assert "34%" in entry["rate"]
    assert "53%" not in entry["rate"]
    # Net rebate: £30M × 80% QS - 15% ATL = £19.5M × 25.5% = £4,972,500
    assert "4,972,500" in entry["net_rebate"]


# ── Crew rates ────────────────────────────────────────────────────────────────


def test_crew_rates_included_in_budget_currency():
    """Crew rates should be pre-converted to budget currency."""
    svc = _make_service()
    incentive = _make_incentive(
        territory="United Kingdom",
        qualifying_spend_cap_pct=None,
        rate_type="cash_rebate",
    )
    crew = {
        "territory": "United Kingdom",
        "role_category": "Director of Photography",
        "union_rate_gbp": 1200.0,
        "non_union_rate_gbp": 600.0,
        "fx_rate": 1.0,
        "fx_date": "2026-03-17",
    }
    datasets = _make_datasets(budget_gbp=10_000_000, incentives=[incentive], crew_costs=[crew])

    svc._pre_compute_territory_financials(datasets)
    entry = datasets["_territory_financials"]["United Kingdom"]

    assert "Director of Photography" in entry["crew_rates"]
    rate_str = entry["crew_rates"]["Director of Photography"]
    assert "/day" in rate_str
    assert "600" in rate_str
    assert "1,200" in rate_str


def test_crew_rates_empty_when_no_crew_data():
    """crew_rates dict should be empty when no crew costs are available."""
    svc = _make_service()
    incentive = _make_incentive(qualifying_spend_cap_pct=None, rate_type="cash_rebate")
    datasets = _make_datasets(budget_gbp=10_000_000, incentives=[incentive], crew_costs=[])

    svc._pre_compute_territory_financials(datasets)
    entry = datasets["_territory_financials"]["United Kingdom"]

    assert entry["crew_rates"] == {}


# ── Programme name ────────────────────────────────────────────────────────────


def test_programme_name_included():
    """The programme name should appear in the territory entry."""
    svc = _make_service()
    incentive = _make_incentive(
        program_name="UK Audio Visual Expenditure Credit",
        qualifying_spend_cap_pct=None,
        rate_type="cash_rebate",
    )
    datasets = _make_datasets(budget_gbp=10_000_000, incentives=[incentive])

    svc._pre_compute_territory_financials(datasets)
    entry = datasets["_territory_financials"]["United Kingdom"]

    assert entry["programme"] == "UK Audio Visual Expenditure Credit"


# ── Headline net budget string ────────────────────────────────────────────────


def test_headline_net_budget_format():
    """headline_net_budget should start with 'approximately' and contain a currency figure."""
    svc = _make_service()
    incentive = _make_incentive(
        rate_gross=30.0,
        rate_net=30.0,
        rate_type="cash_rebate",
        qualifying_spend_cap_pct=None,
    )
    datasets = _make_datasets(budget_gbp=10_000_000, incentives=[incentive])

    svc._pre_compute_territory_financials(datasets)
    entry = datasets["_territory_financials"]["United Kingdom"]

    assert entry["headline_net_budget"].startswith("approximately")
    assert "7,000,000" in entry["headline_net_budget"]  # £10M - £3M = £7M
