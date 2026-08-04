"""PROD-FIX-007 — Lion King: UK VFX rate mismatch and wrong programme.

The Lion King PRO report ($50M budget, ~£37.5M) recommended the UK and then
contradicted itself:

  Executive summary:  "The UK Independent Film Tax Credit delivers a net rebate
                       of 39.75% on qualifying spend, capped at 6,360,000 GBP"
  Waterfall, same recommendation, two paragraphs later:
                      "On £37,535,000 qualifying spend at 29.25% net (39%
                       gross)"

39.75%/53% is the Independent Film Tax Credit. 29.25%/39% is the separate VFX
Expenditure Credit. £6.36M is the IFTC's project cap, which does not apply to
the VFX credit at all — and 39% of £37.5M is ~£14.6M, so the figure reconciled
with neither programme.

Three independent defects combined to produce that output:

  1. is_supplementary was wiped from the UK VFX credit by the v4 refresh
     (restored by migration h8c9d0e1f2a3), letting a VFX-only credit be
     selected as the territory's primary programme.
  2. _compute_corrected_rebate read caps, currency and ATL treatment from the
     ruled-out programme's row after switching.
  3. ReportBuilder labelled the estimate with the ruled-out programme's name
     and rate while showing the switched programme's figure.

Each is covered separately below, then together on the reported scenario.
"""
from __future__ import annotations

import pytest

from app.modules.reports.validator import ReportValidator

# £37,535,000 — the Lion King qualifying-spend figure quoted in the report.
LION_KING_BUDGET_GBP = 37_535_000.0


def _uk_avec() -> dict:
    """UK Audio-Visual Expenditure Credit — 34% gross / 25.5% net, no cap."""
    return {
        "territory": "United Kingdom",
        "program": "UK Audio-Visual Expenditure Credit (AVEC)",
        "rate_gross": 34.0,
        "rate_net": 25.5,
        "rate_type": "tax_credit",
        "qualifying_spend_type": "total",
        "qualifying_spend_cap_pct": 80.0,
        "atl_exempt": True,
        "cap_amount": None,
        "rebate_cap_amount": None,
        "currency": "GBP",
        "is_supplementary": False,
    }


def _uk_iftc() -> dict:
    """Independent Film Tax Credit — 53%/39.75%, but only below £23.5M core."""
    return {
        "territory": "United Kingdom",
        "program": "AVEC (Enhanced/IFTC)",
        "rate_gross": 53.0,
        "rate_net": 39.75,
        "rate_type": "enhanced_tax_credit",
        "qualifying_spend_type": "total",
        "qualifying_spend_cap_pct": 80.0,
        "atl_exempt": True,
        # Budget-eligibility ceiling: above this, IFTC is not available at all.
        "cap_amount": 23_500_000.0,
        "cap_currency": "GBP",
        # The programme's own project cap — the source of the £6.36M figure.
        "rebate_cap_amount": 6_360_000.0,
        "rebate_cap_currency": "GBP",
        "currency": "GBP",
        "is_supplementary": False,
    }


def _uk_vfx(is_supplementary: bool = True) -> dict:
    """VFX Expenditure Credit — 39%/29.25%, VFX-specific spend only."""
    return {
        "territory": "United Kingdom",
        "program": "UK VFX Expenditure Credit (Uplift)",
        "rate_gross": 39.0,
        "rate_net": 29.25,
        "rate_type": "enhanced_tax_credit",
        "qualifying_spend_type": "pdv",
        "qualifying_spend_labour_pct": 15.0,
        "atl_exempt": True,
        "cap_amount": None,
        "rebate_cap_amount": None,
        "currency": "GBP",
        "is_supplementary": is_supplementary,
    }


def _compute(db_row: dict, rows: list[dict], budget: float = LION_KING_BUDGET_GBP):
    return ReportValidator._compute_corrected_rebate(
        db_row, budget, {"United Kingdom": rows}, production_format="feature"
    )


# ── 1. Supplementary credits cannot be selected as primary ───────────────────


def test_vfx_credit_is_not_selected_as_the_replacement_programme() -> None:
    """The defect migration j1k2l3m4n5o6 fixed, which the v4 refresh undid."""
    rows = [_uk_avec(), _uk_iftc(), _uk_vfx()]
    result = _compute(_uk_iftc(), rows)

    assert result["switched_programme"] == "UK Audio-Visual Expenditure Credit (AVEC)"
    assert result["rate_gross"] == 34.0
    assert result["rate_net"] == 25.5


def test_vfx_credit_would_be_selected_without_the_flag() -> None:
    """Characterises the bug, so the regression cannot silently return.

    With is_supplementary cleared — exactly the state the v4 refresh left the
    database in — the engine picks the VFX credit's 39%/29.25%, which is the
    rate the Lion King waterfall showed.
    """
    rows = [_uk_avec(), _uk_iftc(), _uk_vfx(is_supplementary=False)]
    result = _compute(_uk_iftc(), rows)

    assert result["switched_programme"] == "UK VFX Expenditure Credit (Uplift)"
    assert result["rate_net"] == 29.25


# ── 2. Caps and bases belong to the modelled programme ───────────────────────


def test_iftc_project_cap_is_not_applied_to_the_replacement() -> None:
    """The £6,360,000 in the report.

    IFTC is ruled out at this budget, so its project cap must not clamp a
    rebate computed under AVEC — which has no per-project cap.
    """
    rows = [_uk_avec(), _uk_iftc(), _uk_vfx()]
    result = _compute(_uk_iftc(), rows)

    assert result["gross_rebate"] != pytest.approx(6_360_000.0)
    assert result["net_rebate"] != pytest.approx(6_360_000.0)
    assert result.get("rebate_cap_note") is None

    # AVEC: 80% of £37,535,000 = £30,028,000 qualifying, at 25.5% net.
    assert result["qualifying_spend"] == pytest.approx(30_028_000.0)
    assert result["net_rebate"] == pytest.approx(30_028_000.0 * 0.255)


def test_replacement_programme_cap_is_applied_when_it_has_one() -> None:
    """The fix must not simply drop caps — the replacement's own cap still binds."""
    capped_avec = _uk_avec()
    capped_avec["rebate_cap_amount"] = 1_000_000.0
    capped_avec["rebate_cap_currency"] = "GBP"

    result = _compute(_uk_iftc(), [capped_avec, _uk_iftc(), _uk_vfx()])

    assert result["gross_rebate"] == pytest.approx(1_000_000.0)
    assert "capped at" in (result.get("rebate_cap_note") or "")


def test_qualifying_spend_is_recomputed_on_the_replacement_basis() -> None:
    """A PDV credit measures VFX spend; a total credit measures the budget.

    If the only available replacement is PDV-based, its 15% VFX share must be
    used — not the ruled-out programme's 80% of total budget.
    """
    vfx_as_primary = _uk_vfx(is_supplementary=False)
    result = _compute(_uk_iftc(), [_uk_iftc(), vfx_as_primary])

    assert result["switched_programme"] == "UK VFX Expenditure Credit (Uplift)"
    # 15% of budget, not 80%.
    assert result["qualifying_spend"] == pytest.approx(LION_KING_BUDGET_GBP * 0.15)
    assert result["qualifying_spend_pct"] == pytest.approx(15.0)
    assert "post-production" in (result.get("qualifying_spend_note") or "")


def test_below_the_ceiling_iftc_still_applies_with_its_own_cap() -> None:
    """A genuinely independent film is unaffected by all of this."""
    result = _compute(_uk_iftc(), [_uk_avec(), _uk_iftc(), _uk_vfx()],
                      budget=10_000_000.0)

    assert result["switched_programme"] is None
    assert result["rate_net"] == 39.75
    # 80% of £10M = £8M at 39.75% = £3.18M, below the £6.36M project cap.
    assert result["net_rebate"] == pytest.approx(3_180_000.0)


# ── 3. One programme, one rate, one cap per recommendation ───────────────────


def test_report_estimate_names_the_programme_it_actually_modelled() -> None:
    """The internal contradiction in the executive summary.

    ReportBuilder must not label an estimate with the ruled-out programme while
    showing the replacement's figure.
    """
    from app.modules.reports.builder import ReportBuilder

    builder = ReportBuilder(
        {
            "incentives": [_uk_avec(), _uk_iftc(), _uk_vfx()],
            "_territory_financials": {
                "United Kingdom": {
                    "programme": "UK Audio-Visual Expenditure Credit (AVEC)",
                    "rate": "25.5% net (34% gross)",
                    "net_rebate": "£7,657,140",
                    "programme_note": (
                        "Budget exceeds AVEC (Enhanced/IFTC) cap of £23.5M — "
                        "UK Audio-Visual Expenditure Credit (AVEC) applies instead"
                    ),
                }
            },
        },
        {},
    )

    est = builder._build_single_estimate(
        _uk_iftc(), "United Kingdom", "AVEC (Enhanced/IFTC)"
    )

    # Name and rate describe the same programme as the figure.
    assert est["program"] == "UK Audio-Visual Expenditure Credit (AVEC)"
    assert "25.5" in est["rate"]
    assert "39.75" not in est["rate"]
    assert est["estimatedRebate"] == "£7,657,140"
    # And the IFTC's project cap is not carried over.
    assert "6,360,000" not in (est.get("cap") or "")
    assert "6.36" not in (est.get("cap") or "")
    assert est.get("programmeNote")


def test_report_estimate_unchanged_when_no_switch_occurs() -> None:
    from app.modules.reports.builder import ReportBuilder

    builder = ReportBuilder(
        {
            "incentives": [_uk_avec()],
            "_territory_financials": {
                "United Kingdom": {
                    "programme": "UK Audio-Visual Expenditure Credit (AVEC)",
                    "rate": "25.5% net (34% gross)",
                    "net_rebate": "£7,657,140",
                }
            },
        },
        {},
    )

    est = builder._build_single_estimate(
        _uk_avec(), "United Kingdom", "UK Audio-Visual Expenditure Credit (AVEC)"
    )

    assert est["program"] == "UK Audio-Visual Expenditure Credit (AVEC)"
    assert est.get("programmeNote") is None
