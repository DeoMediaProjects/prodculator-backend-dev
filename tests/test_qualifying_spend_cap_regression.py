"""Regression tests for the cap constraints the v4 ingest silently discarded.

Four defects, all reported as "the calculations stopped following the rules we
had already put in", all in the path between an ingested cap and a rendered one:

1. ``qualifying_spend_cap_pct`` was applied to the money and rendered nowhere, so
   UK AVEC reduced qualifying spend by 20% and reported "No cap".
2. The absolute qualifying-expenditure ceiling was dropped at ingest — the UK
   IFTC's "80% of core expenditure, capped at GBP 12,000,000" stored 80.0 alone,
   so qualifying spend scaled with the budget instead of stopping at £12M.
3. ``rebate_cap_amount`` is a GROSS ceiling and was assigned to the net figure
   too, overstating the investor-facing number by the whole gross-to-net haircut.
4. The cap display chain filtered only "no formal cap", so the v4 rows' "No cap"
   string reached the report as this programme's cap.

The authority for the UK figures is HMRC/BFI: qualifying expenditure is
MIN(80% of core expenditure, £12M), where £12M is 80% of a fixed £15M reference
amount, and the maximum IFTC credit is £6.36M = £15M x 80% x 53%.
https://www.gov.uk/guidance/audio-visual-expenditure-credit
"""
from __future__ import annotations

import pytest

from app.modules.reports.helpers import (
    format_qualifying_spend_cap,
    is_vacuous_cap_label,
)
from app.modules.reports.validator import ReportValidator


def _uk_row(
    *,
    program: str,
    rate_gross: float,
    rate_net: float,
    qs_cap_pct: float | None = 80.0,
    qs_cap_amount: float | None = None,
    rebate_cap_amount: float | None = None,
    cap_amount: float | None = None,
) -> dict:
    """A UK row shaped like the v4 dataset produces one.

    ``atl_exempt`` is True for every UK programme (AVEC applies a flat rate to
    all qualifying UK expenditure), so these cases isolate the cap logic without
    the 15% ATL deduction confounding the arithmetic.
    """
    return {
        "territory": "United Kingdom",
        "program": program,
        "rate_gross": rate_gross,
        "rate_net": rate_net,
        "rate_type": "tax_credit",
        "atl_exempt": True,
        "qualifying_spend_type": "total",
        "qualifying_spend_cap_pct": qs_cap_pct,
        "qualifying_spend_cap_amount": qs_cap_amount,
        "qualifying_spend_cap_currency": "GBP" if qs_cap_amount else None,
        "qualifying_spend_labour_pct": None,
        "rebate_cap_amount": rebate_cap_amount,
        "rebate_cap_currency": "GBP" if rebate_cap_amount else None,
        "cap_amount": cap_amount,
        "cap_currency": "GBP",
        "cap_per_person": None,
        "cap_per_person_currency": None,
        "currency": "GBP",
        "rate_tier_json": None,
        "payment_timeline_notes": None,
        "last_verified_at": None,
    }


def _iftc(**overrides) -> dict:
    return _uk_row(
        program="AVEC (Enhanced/IFTC)",
        rate_gross=53.0,
        rate_net=39.75,
        qs_cap_amount=12_000_000.0,
        rebate_cap_amount=6_360_000.0,
        cap_amount=23_500_000.0,
        **overrides,
    )


def _avec(**overrides) -> dict:
    return _uk_row(
        program="UK Audio-Visual Expenditure Credit (AVEC)",
        rate_gross=34.0,
        rate_net=25.5,
        **overrides,
    )


# ── 1. The absolute qualifying-expenditure ceiling binds ─────────────────────


class TestAbsoluteQualifyingSpendCeiling:
    @pytest.mark.parametrize(
        "budget_gbp,expected_qs",
        [
            (10_000_000.0, 8_000_000.0),   # below the reference amount: 80% applies
            (15_000_000.0, 12_000_000.0),  # exactly at it: the two agree
            (18_700_000.0, 12_000_000.0),  # above it: the fixed ceiling binds
            (23_000_000.0, 12_000_000.0),  # still the ceiling, not 80% of budget
        ],
    )
    def test_qualifying_spend_is_min_of_pct_and_absolute_cap(
        self, budget_gbp, expected_qs
    ):
        result = ReportValidator._compute_corrected_rebate(_iftc(), budget_gbp, {})
        assert result is not None
        assert result["qualifying_spend"] == pytest.approx(expected_qs, rel=1e-6)

    def test_qualifying_spend_does_not_scale_with_budget_above_the_ceiling(self):
        """The regression itself: a bigger budget must not buy a bigger base."""
        smaller = ReportValidator._compute_corrected_rebate(_iftc(), 18_700_000.0, {})
        larger = ReportValidator._compute_corrected_rebate(_iftc(), 23_000_000.0, {})
        assert smaller["qualifying_spend"] == larger["qualifying_spend"] == 12_000_000.0

    def test_a_row_without_an_absolute_cap_still_scales(self):
        """AVEC has no fixed ceiling — 80% must keep tracking the budget."""
        result = ReportValidator._compute_corrected_rebate(_avec(), 60_000_000.0, {})
        assert result["qualifying_spend"] == pytest.approx(48_000_000.0, rel=1e-6)


# ── 2. The cap is stated, not applied in silence ─────────────────────────────


class TestCapIsExplained:
    def test_percentage_cap_produces_a_note(self):
        result = ReportValidator._compute_corrected_rebate(_avec(), 20_000_000.0, {})
        note = result.get("qualifying_spend_note") or ""
        assert "80%" in note, f"the 80% reduction went unstated: {note!r}"

    def test_absolute_cap_produces_a_note_naming_the_ceiling(self):
        result = ReportValidator._compute_corrected_rebate(_iftc(), 23_000_000.0, {})
        note = result.get("qualifying_spend_note") or ""
        assert "12,000,000" in note, f"the £12M ceiling went unstated: {note!r}"

    def test_uncapped_programme_gets_no_spurious_cap_note(self):
        result = ReportValidator._compute_corrected_rebate(
            _avec(qs_cap_pct=None), 20_000_000.0, {},
        )
        note = result.get("qualifying_spend_note") or ""
        assert "capped" not in note.lower()


# ── 3. A gross ceiling must not be written to the net figure ──────────────────


class TestRebateCapAppliesToGrossOnly:
    def test_net_is_scaled_by_the_rate_pair_not_set_to_the_gross_cap(self):
        result = ReportValidator._compute_corrected_rebate(_iftc(), 23_000_000.0, {})
        assert result["gross_rebate"] == pytest.approx(6_360_000.0, rel=1e-6)
        # £6.36M gross x (39.75 / 53) = £4.77M net. Previously this was £6.36M.
        assert result["net_rebate"] == pytest.approx(4_770_000.0, rel=1e-6)

    def test_net_never_exceeds_gross_once_the_cap_binds(self):
        result = ReportValidator._compute_corrected_rebate(_iftc(), 23_000_000.0, {})
        assert result["net_rebate"] <= result["gross_rebate"]

    def test_uncapped_case_is_untouched(self):
        """A budget under the ceiling must still compute from the real rates."""
        result = ReportValidator._compute_corrected_rebate(_iftc(), 10_000_000.0, {})
        assert result["gross_rebate"] == pytest.approx(8_000_000.0 * 0.53, rel=1e-6)
        assert result["net_rebate"] == pytest.approx(8_000_000.0 * 0.3975, rel=1e-6)

    def test_ceiling_and_base_agree_at_the_reference_amount(self):
        """£15M budget: 80% x £15M x 53% is exactly the £6.36M published maximum,
        so the qualifying-spend cap and the rebate cap must not double-apply."""
        result = ReportValidator._compute_corrected_rebate(_iftc(), 15_000_000.0, {})
        assert result["gross_rebate"] == pytest.approx(6_360_000.0, rel=1e-6)
        assert result["net_rebate"] == pytest.approx(4_770_000.0, rel=1e-6)


# ── 4. Display: "No cap" is not a cap ────────────────────────────────────────


class TestCapLabelFiltering:
    @pytest.mark.parametrize(
        "label",
        [
            "No cap",
            "no cap",
            "No cap identified",
            "No formal cap (annual budget limited)",
            "No flat cap identified — interim payments available once ...",
            "Uncapped",
            "Not stated as a hard per-project cap in available sources",
            "",
            None,
        ],
    )
    def test_absence_assertions_are_rejected(self, label):
        assert is_vacuous_cap_label(label) is True

    @pytest.mark.parametrize(
        "label",
        [
            "Budget cap £23.5M",
            "€12.5M ATL expenditure cap",
            "$42M per production (35% of first $120M qualified expenditure)",
        ],
    )
    def test_real_cap_labels_survive(self, label):
        assert is_vacuous_cap_label(label) is False

    def test_qualifying_spend_cap_renders_percentage(self):
        assert "80%" in (format_qualifying_spend_cap(80.0) or "")

    def test_qualifying_spend_cap_renders_both_halves(self):
        rendered = format_qualifying_spend_cap(80.0, 12_000_000.0, "GBP") or ""
        assert "80%" in rendered
        assert "12M" in rendered

    def test_hundred_percent_is_not_a_cap(self):
        """100% means no restriction — it must not render as a cap."""
        assert format_qualifying_spend_cap(100.0) is None

    def test_no_cap_data_renders_nothing(self):
        assert format_qualifying_spend_cap(None, None) is None
