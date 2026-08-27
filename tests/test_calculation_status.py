"""The status that tells a reader whether a figure may be relied on.

Two behaviours carry the weight. A status that may not carry an amount does not
carry one, enforced centrally rather than trusted to each surface. And the
governance gate stays separate from the status: an unapproved formula is a fact
about our verification queue, not about the producer's project, and reporting it
as the project's status would be a lie about whose problem it is.
"""
from __future__ import annotations

import pytest

from app.modules.incentives.v2_contracts import NUMERIC_STATUSES
from app.modules.reports.calculation_status import resolve_calculation_status


def _row(**overrides):
    row = {
        "program": "UK Audio-Visual Expenditure Credit",
        "programme_id": "GB_AVEC",
        "territory": "United Kingdom",
        "status": "active",
        "qs_engine_type": "CORE_LOWER_OF",
        "calculation_verification_status": "ready",
    }
    row.update(overrides)
    return row


def _est(**overrides):
    est = {
        "incentiveIsConfirmed": True,
        "incentiveEligibilityStatus": "eligible",
        "programmeEligibility": {"available": True},
    }
    est.update(overrides)
    return est


def _scenario(*inputs):
    return {"calculation_inputs": list(inputs)}


def _input(key, amount, status="known"):
    return {"input_key": key, "amount": amount, "input_status": status}


UK_CORE = ("local_core_expenditure", "global_core_expenditure")


def _uk_inputs(status="known"):
    return _scenario(*[_input(k, 10_000_000, status) for k in UK_CORE])


# ── the happy path ───────────────────────────────────────────────────────────


def test_every_required_figure_present_gives_a_calculation():
    result = resolve_calculation_status(
        _est(), _row(), scenario=_uk_inputs(), declared_inputs=UK_CORE,
    )
    assert result["calculationStatus"] == "ESTIMATED"
    assert result["calculationCarriesFigure"] is True
    assert result["calculationStatusLabel"] == "Calculated"


def test_a_planning_assumption_is_a_scenario_not_a_calculation():
    """The figure is shown, but the reader is told what it rests on."""
    result = resolve_calculation_status(
        _est(),
        _row(),
        scenario=_uk_inputs(status="planning_assumption"),
        declared_inputs=UK_CORE,
    )
    assert result["calculationStatus"] == "CONDITIONAL"
    assert result["calculationCarriesFigure"] is True
    assert "planning assumptions" in " ".join(result["calculationStatusReasons"])


# ── the rule the rebuild exists for ──────────────────────────────────────────


class TestMissingInputs:
    def test_no_cost_base_means_no_figure(self):
        result = resolve_calculation_status(
            _est(), _row(), scenario=None, declared_inputs=UK_CORE,
        )
        assert result["calculationStatus"] == "REQUIRES_COST_BREAKDOWN"
        assert result["calculationCarriesFigure"] is False

    def test_the_missing_figures_are_named(self):
        """A refusal a producer cannot act on is only half an answer."""
        result = resolve_calculation_status(
            _est(),
            _row(),
            scenario=_scenario(_input("local_core_expenditure", 10_000_000)),
            declared_inputs=UK_CORE,
        )
        assert result["calculationStatus"] == "REQUIRES_COST_BREAKDOWN"
        reasons = " ".join(result["calculationStatusReasons"])
        assert "global core expenditure" in reasons
        assert "local core expenditure" not in reasons
        assert result["calculationStatusNextStep"]

    def test_a_known_zero_is_not_a_missing_figure(self):
        """Null means the producer has not told us. Zero means they have told us
        it is nil, which the formula can act on."""
        result = resolve_calculation_status(
            _est(),
            _row(),
            scenario=_scenario(
                _input("local_core_expenditure", 0),
                _input("global_core_expenditure", 10_000_000),
            ),
            declared_inputs=UK_CORE,
        )
        assert result["calculationStatus"] == "ESTIMATED"

    def test_an_explicit_null_is_still_missing(self):
        result = resolve_calculation_status(
            _est(),
            _row(),
            scenario=_scenario(
                _input("local_core_expenditure", None),
                _input("global_core_expenditure", 10_000_000),
            ),
            declared_inputs=UK_CORE,
        )
        assert result["calculationStatus"] == "REQUIRES_COST_BREAKDOWN"


# ── precedence ───────────────────────────────────────────────────────────────


class TestPrecedence:
    def test_no_programme_outranks_everything(self):
        result = resolve_calculation_status(
            _est(), _row(program=None, qs_engine_type="NO_PROGRAMME"),
        )
        assert result["calculationStatus"] == "NO_PROGRAMME"

    def test_a_suspended_programme_is_not_judged_on_eligibility(self):
        """A closed programme cannot be failed on eligibility grounds; there is
        nothing to qualify for."""
        result = resolve_calculation_status(
            _est(programmeEligibility={"available": False}),
            _row(status="suspended"),
            scenario=_uk_inputs(),
            declared_inputs=UK_CORE,
        )
        assert result["calculationStatus"] == "SUSPENDED"
        # And the territory is not written off along with its incentive.
        assert "location, crew" in result["calculationStatusNextStep"]

    def test_ineligibility_outranks_a_missing_cost_base(self):
        """Asking a producer to prepare a cost breakdown for a programme their
        project cannot use wastes their time."""
        result = resolve_calculation_status(
            _est(programmeEligibility={
                "available": False,
                "reasons": [{"detail": "Budget below the GBP 1,000,000 floor"}],
            }),
            _row(),
            scenario=None,
            declared_inputs=UK_CORE,
        )
        assert result["calculationStatus"] == "NOT_ELIGIBLE"
        assert result["calculationCarriesFigure"] is False
        assert "1,000,000" in " ".join(result["calculationStatusReasons"])

    def test_a_non_entitlement_mechanism_is_conditional_whatever_is_supplied(self):
        """No quantity of cost detail turns a competitive grant into an amount a
        production can count on."""
        result = resolve_calculation_status(
            _est(),
            _row(qs_engine_type="COMPETITIVE_GRANT", program="Eurimages"),
            scenario=_uk_inputs(),
            declared_inputs=UK_CORE,
        )
        assert result["calculationStatus"] == "CONDITIONAL"

    def test_an_investor_shelter_is_never_promoted_to_estimated(self):
        result = resolve_calculation_status(
            _est(),
            _row(qs_engine_type="INVESTOR_TAX_SHELTER", program="Belgian Tax Shelter"),
            scenario=_uk_inputs(),
            declared_inputs=UK_CORE,
        )
        assert result["calculationStatus"] == "CONDITIONAL"

    def test_an_unmigrated_programme_is_indicative_not_calculated(self):
        """No statutory engine means the deterministic formula did not run, and
        saying ESTIMATED would imply it had."""
        result = resolve_calculation_status(
            _est(), _row(qs_engine_type=None),
        )
        assert result["calculationStatus"] == "CONDITIONAL"
        assert "not yet been migrated" in " ".join(result["calculationStatusReasons"])


# ── the two gates stay apart ──────────────────────────────────────────────────


class TestVerificationIsSeparate:
    def test_a_blocked_formula_does_not_become_the_projects_status(self):
        """Reporting BLOCKED as the calculation status would tell a producer their
        project has a problem, when the queue is ours."""
        result = resolve_calculation_status(
            _est(),
            _row(calculation_verification_status="blocked"),
            scenario=_uk_inputs(),
            declared_inputs=UK_CORE,
        )
        assert result["calculationStatus"] == "ESTIMATED"
        assert result["calculationVerification"] == "blocked"
        assert result["calculationIsApproved"] is False

    def test_an_approved_formula_says_so_separately(self):
        result = resolve_calculation_status(
            _est(), _row(calculation_verification_status="ready"),
            scenario=_uk_inputs(), declared_inputs=UK_CORE,
        )
        assert result["calculationIsApproved"] is True
        assert result["calculationVerificationLabel"] == "Formula approved"

    def test_a_missing_gate_value_is_treated_as_unapproved(self):
        """Defaulting the other way would let an unmigrated row present itself as
        approved simply by saying nothing."""
        result = resolve_calculation_status(
            _est(), _row(calculation_verification_status=None),
        )
        assert result["calculationVerification"] == "blocked"
        assert result["calculationIsApproved"] is False


# ── the contract's own invariants ─────────────────────────────────────────────


@pytest.mark.parametrize("status", sorted(NUMERIC_STATUSES))
def test_only_the_two_numeric_statuses_may_carry_a_figure(status):
    assert status in {"ESTIMATED", "CONDITIONAL"}


def test_every_status_the_resolver_can_return_has_a_label():
    from app.modules.reports.calculation_status import STATUS_LABELS
    from app.modules.incentives.v2_contracts import CALCULATION_STATUSES

    assert set(STATUS_LABELS) == set(CALCULATION_STATUSES)
