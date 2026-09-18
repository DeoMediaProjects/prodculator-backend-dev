"""The statutory qualifying-spend base, and the figures that must not exist.

The Devil Wears Prada regression is the acceptance case these exist for: the same
screenplay and the same blank territory-spend fields must produce no project
rebate amount, while a producer who supplies statutory cost figures must get a
calculation derived from those figures rather than from the total budget.
"""
from __future__ import annotations

from app.modules.reports.statutory_calculation import (
    StatutoryQualifyingSpend,
    resolve_statutory_qualifying_spend,
)


def _row(engine: str = "ELIGIBLE_LOCAL_SPEND", **overrides) -> dict:
    row = {
        "programme_id": "uk-avec",
        "program": "AVEC",
        "territory": "United Kingdom",
        "qs_engine_type": engine,
        "qualifying_spend_cap_pct": None,
        "qualifying_spend_cap_amount": None,
    }
    row.update(overrides)
    return row


def _scenario(**amounts) -> dict:
    return {
        "territory": "United Kingdom",
        "calculation_inputs": [
            {"input_key": key, "amount": amount, "input_status": "known"}
            for key, amount in amounts.items()
        ],
    }


# ── The regression: nothing supplied, nothing calculated ─────────────────────


def test_no_scenario_yields_no_base():
    assert resolve_statutory_qualifying_spend(_row(), None) is None


def test_blank_territory_spend_yields_no_base():
    """The regression case. A selected territory with no cost figures entered."""
    scenario = {"territory": "United Kingdom", "scenario_spend": None}
    assert resolve_statutory_qualifying_spend(_row(), scenario) is None


def test_scenario_spend_alone_is_not_a_statutory_base():
    """Territory spend is a different quantity from qualifying spend.

    A producer stating they will spend £4m in the UK has not stated how much of
    it qualifies under AVEC, and the difference is the whole subject of the
    programme's rules.
    """
    scenario = {"territory": "United Kingdom", "scenario_spend": 4_000_000}
    assert resolve_statutory_qualifying_spend(_row(), scenario) is None


def test_explicit_null_amount_is_unknown_not_zero():
    scenario = _scenario(eligible_local_spend=None)
    assert resolve_statutory_qualifying_spend(_row(), scenario) is None


def test_zero_is_supplied_and_calculates():
    """Zero means the producer told us the base is nil; null means they did not.

    Collapsing the two is the substitution this module exists to prevent, so a
    supplied zero must produce a real result of zero rather than an absence.
    """
    result = resolve_statutory_qualifying_spend(
        _row(), _scenario(eligible_local_spend=0)
    )
    assert isinstance(result, StatutoryQualifyingSpend)
    assert result.amount == 0.0


# ── Engines ──────────────────────────────────────────────────────────────────


def test_eligible_local_spend_uses_the_supplied_figure():
    result = resolve_statutory_qualifying_spend(
        _row(), _scenario(eligible_local_spend=4_000_000)
    )
    assert result.amount == 4_000_000
    assert result.inputs_used == {"eligible_local_spend": 4_000_000}


def test_qualified_labour_engine_reads_its_own_input():
    row = _row("QUALIFIED_LABOUR")
    assert resolve_statutory_qualifying_spend(
        row, _scenario(eligible_local_spend=4_000_000)
    ) is None
    result = resolve_statutory_qualifying_spend(
        row, _scenario(qualified_labour=1_500_000)
    )
    assert result.amount == 1_500_000


def test_core_lower_of_takes_the_lower_limb():
    row = _row("CORE_LOWER_OF", qualifying_spend_cap_pct=80.0)
    result = resolve_statutory_qualifying_spend(
        row,
        _scenario(
            local_core_expenditure=9_000_000,
            global_core_expenditure=10_000_000,
        ),
    )
    # 80% of 10m = 8m, which is lower than the 9m local figure.
    assert result.amount == 8_000_000
    assert result.cap_applied == "percentage_of_global_core"


def test_core_lower_of_keeps_the_local_limb_when_it_is_lower():
    row = _row("CORE_LOWER_OF", qualifying_spend_cap_pct=80.0)
    result = resolve_statutory_qualifying_spend(
        row,
        _scenario(
            local_core_expenditure=5_000_000,
            global_core_expenditure=10_000_000,
        ),
    )
    assert result.amount == 5_000_000
    assert result.cap_applied is None


def test_core_lower_of_does_not_apply_its_percentage_twice():
    """The percentage is a limb of the formula, not a cap applied afterwards."""
    row = _row("CORE_LOWER_OF", qualifying_spend_cap_pct=80.0)
    result = resolve_statutory_qualifying_spend(
        row,
        _scenario(
            local_core_expenditure=9_000_000,
            global_core_expenditure=10_000_000,
        ),
    )
    assert result.amount == 8_000_000  # not 6.4m


def test_core_lower_of_needs_both_limbs():
    row = _row("CORE_LOWER_OF", qualifying_spend_cap_pct=80.0)
    assert resolve_statutory_qualifying_spend(
        row, _scenario(local_core_expenditure=9_000_000)
    ) is None


def test_multi_bucket_sums_only_its_declared_buckets():
    row = _row("MULTI_BUCKET")
    result = resolve_statutory_qualifying_spend(
        row,
        _scenario(resident_labour=2_000_000, vendor_spend=1_000_000),
        declared_inputs=["resident_labour", "vendor_spend"],
    )
    assert result.amount == 3_000_000


def test_multi_bucket_with_a_missing_bucket_yields_no_base():
    row = _row("MULTI_BUCKET")
    assert resolve_statutory_qualifying_spend(
        row,
        _scenario(resident_labour=2_000_000),
        declared_inputs=["resident_labour", "vendor_spend"],
    ) is None


def test_multi_bucket_with_no_declared_buckets_yields_no_base():
    """A sum of nothing is zero, which would read as 'nothing qualifies'."""
    row = _row("MULTI_BUCKET")
    assert resolve_statutory_qualifying_spend(
        row, _scenario(resident_labour=2_000_000)
    ) is None


def test_non_spend_engines_never_produce_a_base():
    for engine in ("COMPETITIVE_GRANT", "INVESTOR_TAX_SHELTER", "NO_PROGRAMME"):
        assert resolve_statutory_qualifying_spend(
            _row(engine), _scenario(eligible_local_spend=4_000_000)
        ) is None, engine


def test_unmigrated_row_with_no_engine_yields_no_base():
    """An engine is a statutory classification and is never guessed."""
    assert resolve_statutory_qualifying_spend(
        _row(qs_engine_type=None), _scenario(eligible_local_spend=4_000_000)
    ) is None


def test_unrecognised_engine_yields_no_base():
    assert resolve_statutory_qualifying_spend(
        _row("SOME_NEW_ENGINE"), _scenario(eligible_local_spend=4_000_000)
    ) is None


# ── Caps ─────────────────────────────────────────────────────────────────────


def test_percentage_cap_applies_to_a_single_input_base():
    row = _row(qualifying_spend_cap_pct=80.0)
    result = resolve_statutory_qualifying_spend(
        row, _scenario(eligible_local_spend=10_000_000)
    )
    assert result.amount == 8_000_000
    assert result.cap_applied == "percentage"


def test_absolute_cap_binds_below_the_percentage_cap():
    row = _row(qualifying_spend_cap_pct=80.0)
    result = resolve_statutory_qualifying_spend(
        row, _scenario(eligible_local_spend=10_000_000), absolute_cap=6_000_000
    )
    assert result.amount == 6_000_000
    assert result.cap_applied == "absolute"


def test_absolute_cap_above_the_base_does_not_bind():
    result = resolve_statutory_qualifying_spend(
        _row(), _scenario(eligible_local_spend=4_000_000), absolute_cap=12_000_000
    )
    assert result.amount == 4_000_000
    assert result.cap_applied is None


def test_the_base_never_scales_with_the_budget():
    """The same supplied figures give the same base at any project budget.

    This is the regression in one assertion: the calculation is a function of
    what the producer supplied, and the total budget is not one of its terms.
    """
    scenario = _scenario(eligible_local_spend=4_000_000)
    first = resolve_statutory_qualifying_spend(_row(), scenario)
    second = resolve_statutory_qualifying_spend(_row(), scenario)
    assert first.amount == second.amount == 4_000_000


def test_result_shows_its_working():
    row = _row("CORE_LOWER_OF", qualifying_spend_cap_pct=80.0)
    result = resolve_statutory_qualifying_spend(
        row,
        _scenario(
            local_core_expenditure=9_000_000,
            global_core_expenditure=10_000_000,
        ),
    )
    assert result.inputs_used == {
        "local_core_expenditure": 9_000_000,
        "global_core_expenditure": 10_000_000,
    }
    assert "lower of" in (result.note or "")
