"""Standing the producer's stated territory spend in for an absent base.

A Pro report dated 17 September showed New Mexico a $1.70M rebate. The same
production, the same programme, four days later showed "Needs a cost
breakdown". Nothing about the programme had changed: writing `qs_engine_type`
moved it off the legacy estimator, which derived a qualifying base from the
budget less an estimated 15% ATL deduction, and onto ELIGIBLE_LOCAL_SPEND,
whose base is one supplied figure and which declines to guess.

Declining is right. Guessing from the budget is what these engines exist to
stop. But the producer had already typed an expected spend for that territory,
and refusing to use a figure they stated, for the territory it was stated
about, is a different thing from refusing to invent one.

So the spend is used, and recorded as a planning assumption — which is what
holds the status at CONDITIONAL instead of promoting it to ESTIMATED.
"""
from __future__ import annotations

import pytest

from app.modules.reports.calculation_status import resolve_calculation_status
from app.modules.reports.service import ReportService
from app.modules.reports.statutory_calculation import (
    SPEND_SEEDABLE_ENGINES,
    resolve_statutory_qualifying_spend,
    seed_spend_from_scenario,
)

SPEND = 8_000_000


def _row(engine: str = "ELIGIBLE_LOCAL_SPEND", **over):
    return {
        "program": "New Mexico Film Tax Credit",
        "status": "active",
        "qs_engine_type": engine,
        **over,
    }


def _scenario(**over):
    return {
        "territory": "New Mexico",
        "scenario_spend": SPEND,
        "scenario_currency": "USD",
        "scenario_spend_source": "user_entered",
        "calculation_inputs": [],
        **over,
    }


class TestSeeding:
    def test_stated_spend_becomes_the_qualifying_base(self):
        qs = resolve_statutory_qualifying_spend(_row(), _scenario())
        assert qs is not None
        assert qs.amount == SPEND
        assert qs.inputs_used == {"eligible_local_spend": float(SPEND)}

    def test_the_figure_says_it_rests_on_an_assumption(self):
        # The whole safety of this rests on the report not presenting a seeded
        # base with the confidence of a certified one.
        qs = resolve_statutory_qualifying_spend(_row(), _scenario())
        assert qs is not None and qs.note is not None
        assert "planning assumption" in qs.note

    def test_a_supplied_figure_is_never_overwritten(self):
        scenario = _scenario(
            calculation_inputs=[
                {
                    "input_key": "eligible_local_spend",
                    "amount": 6_800_000,
                    "input_status": "known",
                }
            ]
        )
        qs = resolve_statutory_qualifying_spend(_row(), scenario)
        assert qs is not None
        assert qs.amount == 6_800_000
        assert qs.note is None or "planning assumption" not in qs.note

    def test_zero_is_a_statement_and_is_not_seeded_over(self):
        # Zero means the producer told us there is no qualifying spend here.
        # Treating it as absent and substituting the territory spend would
        # overwrite an answer with a guess.
        scenario = _scenario(
            calculation_inputs=[
                {"input_key": "eligible_local_spend", "amount": 0, "input_status": "known"}
            ]
        )
        qs = resolve_statutory_qualifying_spend(_row(), scenario)
        assert qs is not None and qs.amount == 0


class TestRefusals:
    @pytest.mark.parametrize("engine", ["QUALIFIED_LABOUR", "CORE_LOWER_OF", "QAPE", "MULTI_BUCKET"])
    def test_engines_whose_base_is_not_territory_spend_still_decline(self, engine):
        # Canada's CPTC pays on Canadian labour; the UK compares two core
        # expenditure figures. Seeding either from one spend number produces a
        # confident answer wrong by millions, which is worse than no answer.
        assert engine not in SPEND_SEEDABLE_ENGINES
        assert resolve_statutory_qualifying_spend(_row(engine), _scenario()) is None

    def test_a_blank_spend_field_seeds_nothing(self):
        blank = _scenario(scenario_spend=None, scenario_spend_source="unknown")
        assert resolve_statutory_qualifying_spend(_row(), blank) is None

    def test_an_imported_budget_line_is_not_the_producer_answering(self):
        imported = _scenario(scenario_spend_source="imported_budget")
        assert resolve_statutory_qualifying_spend(_row(), imported) is None

    def test_no_scenario_at_all_seeds_nothing(self):
        assert resolve_statutory_qualifying_spend(_row(), None) is None
        assert seed_spend_from_scenario("ELIGIBLE_LOCAL_SPEND", None, {}) == ({}, ())


class TestStatusAgreesWithTheCalculator:
    """The two modules must reach the same verdict.

    A status promising a figure beside a chart that has none is the specific
    contradiction the shared helper exists to prevent.
    """

    def test_seeded_is_conditional_and_carries_a_figure(self):
        out = resolve_calculation_status({}, _row(), scenario=_scenario())
        assert out["calculationStatus"] == "CONDITIONAL"
        assert out["calculationCarriesFigure"] is True
        assert any(
            "planning assumption" in reason
            for reason in out["calculationStatusReasons"]
        )

    def test_a_typed_figure_outranks_a_seeded_one(self):
        scenario = _scenario(
            calculation_inputs=[
                {
                    "input_key": "eligible_local_spend",
                    "amount": 6_800_000,
                    "input_status": "known",
                }
            ]
        )
        out = resolve_calculation_status({}, _row(), scenario=scenario)
        assert out["calculationStatus"] == "ESTIMATED"

    def test_nothing_supplied_still_asks_for_the_breakdown(self):
        blank = _scenario(scenario_spend=None, scenario_spend_source="unknown")
        out = resolve_calculation_status({}, _row(), scenario=blank)
        assert out["calculationStatus"] == "REQUIRES_COST_BREAKDOWN"
        assert out["calculationCarriesFigure"] is False

    def test_labour_engine_is_refused_by_both(self):
        row = _row("QUALIFIED_LABOUR")
        out = resolve_calculation_status({}, row, scenario=_scenario())
        assert out["calculationStatus"] == "REQUIRES_COST_BREAKDOWN"
        assert resolve_statutory_qualifying_spend(row, _scenario()) is None


class TestCurrency:
    """The seeded spend is converted like any other supplied amount.

    ``_statutory_base_for`` converts to GBP because the statutory module works
    in one currency and does not know which. It converted every
    ``calculation_inputs`` amount and copied ``scenario_spend`` through
    untouched, which was harmless only for as long as nothing read it.
    """

    def _base(self, scenario, **over):
        kwargs = {
            "declared_inputs": None,
            "resolve": resolve_statutory_qualifying_spend,
            "budget_currency": "USD",
            "budget_original_amount": 9_000_000.0,
            # 9,000,000 USD == 6,729,300 GBP, the rate a recent report carried.
            "budget_gbp": 6_729_300.0,
            "fx_rates_from_budget": {},
            **over,
        }
        return ReportService._statutory_base_for(_row(), scenario, **kwargs)

    def test_a_usd_scenario_spend_reaches_the_calculator_in_gbp(self):
        result = self._base(_scenario())
        assert result is not None
        # 8,000,000 USD at the budget's own rate, not 8,000,000 treated as GBP.
        expected = SPEND * (6_729_300.0 / 9_000_000.0)
        assert result.amount == pytest.approx(expected, rel=1e-9)
        assert result.amount < SPEND

    def test_it_uses_the_same_rate_a_typed_figure_would_have(self):
        # The point of converting here rather than in the statutory module: a
        # producer who types the same number into the statutory field must get
        # the same answer as one who leaves it to the scenario spend.
        typed = _scenario(
            calculation_inputs=[
                {
                    "input_key": "eligible_local_spend",
                    "amount": SPEND,
                    "input_status": "known",
                }
            ]
        )
        assert self._base(_scenario()).amount == pytest.approx(
            self._base(typed).amount, rel=1e-9
        )
