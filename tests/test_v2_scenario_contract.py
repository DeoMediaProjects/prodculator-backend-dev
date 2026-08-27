"""Incentive Engine v2, phases 04 and 05: the scenario ingestion contract.

Two rules do the work here.

Canonical IDs only, for anything that selects a formula. The existing country
validator returns unrecognised input unchanged, which the specification calls
permissive and unsafe once a territory key picks a financial rule. A typo would
become a scenario matching no programme, and the report would show a territory
with no analysis and no explanation of why.

Provenance must match the amount. An amount without a stated status, or a status
asserting a figure that was not supplied, is a contract error rather than
something to guess at, because the difference between "known" and "planning
assumption" decides whether a result is estimated or conditional.
"""
from __future__ import annotations

import pytest

from app.modules.incentives.v2_contracts import (
    CANONICAL_INPUTS,
    INPUT_STATUSES,
    SCENARIO_SPEND_SOURCES,
)
from app.modules.incentives.v2_jurisdictions import (
    UnknownJurisdiction,
    all_scenario_jurisdictions,
    is_resolvable,
    resolve_jurisdiction,
    subdivision_code,
)
from app.modules.reports.schemas import (
    CreateReportRequest,
    ScenarioCalculationInput,
    TerritoryScenario,
)

_BASE = dict(
    script_title="A Production",
    genre=["Drama"],
    budget_amount=20_000_000.0,
    format="Feature Film",
    country="United Kingdom",
)


def _request(**overrides):
    return CreateReportRequest(**{**_BASE, **overrides})


# ── canonical jurisdictions ──────────────────────────────────────────────────


class TestJurisdictionResolution:
    @pytest.mark.parametrize("name,territory_id,subdivision_id", [
        ("United Kingdom", "GB", None),
        ("GB", "GB", None),
        ("British Columbia", "CA", "CA-BC"),
        ("CA-BC", "CA", "CA-BC"),
        ("California", "US", "US-CA"),
        ("US-CA", "US", "US-CA"),
        ("Canary Islands", "ES", "ES-CN"),
        ("Georgia (USA)", "US", "US-GA"),
        ("Quebec", "CA", "CA-QC"),
    ])
    def test_labels_iso_codes_and_subdivision_codes_all_resolve(
        self, name, territory_id, subdivision_id
    ):
        resolved = resolve_jurisdiction(name)
        assert resolved.territory_id == territory_id
        assert resolved.subdivision_id == subdivision_id

    def test_a_country_has_no_subdivision(self):
        assert resolve_jurisdiction("Hungary").subdivision_id is None

    @pytest.mark.parametrize("bad", [
        "Califronia", "Untied Kingdom", "Wakanda", "", "   ", "XX-YY",
    ])
    def test_unrecognised_input_raises_rather_than_passing_through(self, bad):
        with pytest.raises(UnknownJurisdiction):
            resolve_jurisdiction(bad)
        assert is_resolvable(bad) is False

    def test_a_statutory_subdivision_is_marked_as_one(self):
        """British Columbia runs its own credit. Scotland does not: the UK credit
        is UK wide, and treating Scotland as a separate statutory regime would
        invent one."""
        assert resolve_jurisdiction("British Columbia").is_statutory_subdivision
        assert resolve_jurisdiction("California").is_statutory_subdivision
        assert not resolve_jurisdiction("Scotland").is_statutory_subdivision
        assert not resolve_jurisdiction("Bavaria").is_statutory_subdivision

    def test_the_scenario_key_distinguishes_provinces(self):
        """Both provinces carry ISO CA in the registry, so the country code alone
        cannot key a scenario."""
        bc = resolve_jurisdiction("British Columbia")
        ontario = resolve_jurisdiction("Ontario")
        assert bc.territory_id == ontario.territory_id == "CA"
        assert bc.scenario_key != ontario.scenario_key
        assert bc.scenario_key == "CA-BC"

    def test_every_sub_territory_in_the_registry_has_a_code(self):
        """A sub-territory with no code would resolve to its country and silently
        be calculated under the wrong programme."""
        from app.core.territories import Territory

        missing = [
            t.label for t in Territory
            if t.is_sub_territory and subdivision_code(t) is None
        ]
        assert not missing, f"sub-territories with no canonical code: {missing}"

    def test_the_picker_list_is_complete_and_unique(self):
        jurisdictions = all_scenario_jurisdictions()
        assert len(jurisdictions) >= 53
        keys = [(j.scenario_key, j.label) for j in jurisdictions]
        assert len(keys) == len(set(keys))


# ── the scenario contract ────────────────────────────────────────────────────


class TestTerritoryScenario:
    def test_a_scenario_normalises_to_canonical_ids(self):
        scenario = TerritoryScenario(territory="British Columbia", scenario_spend=15e6)
        assert scenario.territory_id == "CA"
        assert scenario.subdivision_id == "CA-BC"

    def test_free_text_is_rejected(self):
        with pytest.raises(ValueError, match="not a recognised jurisdiction"):
            TerritoryScenario(territory="Califronia")

    def test_spend_may_be_absent(self):
        """A territory selected before a figure is entered is a real state."""
        assert TerritoryScenario(territory="GB").scenario_spend is None

    def test_spend_may_be_zero(self):
        """Distinct from absent: the producer has said they will spend nothing."""
        assert TerritoryScenario(territory="GB", scenario_spend=0).scenario_spend == 0

    def test_negative_spend_is_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            TerritoryScenario(territory="GB", scenario_spend=-1)

    def test_spend_above_the_budget_is_allowed(self):
        """Scenarios use different currencies and alternative structures, so the
        specification says warn rather than clamp."""
        request = _request(territory_scenarios=[
            {"territory": "GB", "scenario_spend": 999_000_000.0},
        ])
        assert request.territory_scenarios[0].scenario_spend == 999_000_000.0

    def test_the_same_statutory_input_cannot_be_supplied_twice(self):
        with pytest.raises(ValueError, match="same statutory input twice"):
            TerritoryScenario(territory="CA-BC", calculation_inputs=[
                {"input_key": "qualified_labour", "amount": 1.0,
                 "input_status": "known"},
                {"input_key": "qualified_labour", "amount": 2.0,
                 "input_status": "known"},
            ])

    def test_default_spend_source_is_unknown(self):
        assert TerritoryScenario(territory="GB").scenario_spend_source == "unknown"

    def test_spend_source_vocabulary_matches_the_contracts_module(self):
        field = TerritoryScenario.model_fields["scenario_spend_source"]
        from typing import get_args

        assert set(get_args(field.annotation)) == set(SCENARIO_SPEND_SOURCES)


class TestScenarioCalculationInput:
    def test_a_canonical_key_is_accepted(self):
        supplied = ScenarioCalculationInput(
            input_key="qualified_labour", amount=7e6, input_status="known",
        )
        assert supplied.amount == 7e6

    @pytest.mark.parametrize("bad_key", [
        "uk_core_spend", "bc_labour", "california_vendor_spend", "made_up",
    ])
    def test_a_territory_specific_or_invented_key_is_rejected(self, bad_key):
        """The specification forbids territory specific bases; a key the engine
        has no rule for would be a silent no-op rather than an error."""
        with pytest.raises(ValueError, match="not a canonical statutory input"):
            ScenarioCalculationInput(input_key=bad_key, amount=1.0,
                                     input_status="known")

    def test_an_amount_of_zero_is_a_statement_not_an_absence(self):
        supplied = ScenarioCalculationInput(
            input_key="vfx_expenditure", amount=0, input_status="known",
        )
        assert supplied.amount == 0
        assert supplied.input_status == "known"

    def test_an_absent_amount_must_be_status_unknown(self):
        assert ScenarioCalculationInput(
            input_key="qualified_labour"
        ).input_status == "unknown"

    def test_a_status_asserting_a_figure_without_one_is_rejected(self):
        with pytest.raises(ValueError, match="must be 'unknown' when no amount"):
            ScenarioCalculationInput(
                input_key="qualified_labour", input_status="known",
            )

    def test_an_amount_without_a_stated_status_is_rejected(self):
        """known versus planning_assumption decides estimated versus conditional,
        so it cannot be left to a default."""
        with pytest.raises(ValueError, match="Say whether it is 'known'"):
            ScenarioCalculationInput(input_key="qualified_labour", amount=5.0)

    def test_a_negative_base_is_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            ScenarioCalculationInput(
                input_key="qualified_labour", amount=-1.0, input_status="known",
            )

    def test_status_vocabulary_matches_the_contracts_module(self):
        from typing import get_args

        field = ScenarioCalculationInput.model_fields["input_status"]
        assert set(get_args(field.annotation)) == set(INPUT_STATUSES)

    def test_no_source_option_is_ai_generated(self):
        from typing import get_args

        field = ScenarioCalculationInput.model_fields["input_source"]
        options = [a for a in get_args(field.annotation) if isinstance(a, str)]
        # Unwrap the optional union.
        if not options:
            inner = get_args(field.annotation)[0]
            options = [a for a in get_args(inner) if isinstance(a, str)]
        assert options
        assert not any("ai" in o.lower() for o in options)


# ── the request as a whole ───────────────────────────────────────────────────


class TestRequestIntegration:
    def test_scenarios_are_optional(self):
        """Missing cost detail must not block the wider report."""
        assert _request().territory_scenarios == []

    def test_several_alternative_territories_are_accepted(self):
        request = _request(territory_scenarios=[
            {"territory": "GB", "scenario_spend": 10e6, "scenario_currency": "GBP"},
            {"territory": "CA-BC", "scenario_spend": 15e6, "scenario_currency": "CAD"},
            {"territory": "US-CA", "scenario_spend": 12e6, "scenario_currency": "USD"},
        ])
        assert [s.scenario_key if hasattr(s, "scenario_key") else s.subdivision_id
                or s.territory_id for s in request.territory_scenarios] == [
            "GB", "CA-BC", "US-CA",
        ]

    def test_scenarios_need_not_sum_to_the_budget(self):
        """They are alternatives, not allocations."""
        request = _request(budget_amount=20e6, territory_scenarios=[
            {"territory": "GB", "scenario_spend": 18e6},
            {"territory": "CA-BC", "scenario_spend": 19e6},
        ])
        total = sum(s.scenario_spend for s in request.territory_scenarios)
        assert total > request.budget_amount

    def test_one_scenario_per_jurisdiction(self):
        with pytest.raises(ValueError, match="may appear once"):
            _request(territory_scenarios=[
                {"territory": "GB"}, {"territory": "United Kingdom"},
            ])

    def test_two_provinces_of_one_country_are_not_duplicates(self):
        request = _request(territory_scenarios=[
            {"territory": "CA-BC"}, {"territory": "CA-ON"},
        ])
        assert len(request.territory_scenarios) == 2

    def test_legacy_territories_considering_stays_permissive(self):
        """It is compatibility only from v2 and not authoritative for a
        calculation, so an unrecognised entry may still travel for creative
        analysis rather than failing the whole request."""
        request = _request(territories_considering=["Nowhereland", "GB"])
        assert "Nowhereland" in request.territories_considering

    def test_a_full_scenario_round_trips(self):
        request = _request(territory_scenarios=[{
            "territory": "British Columbia",
            "scenario_spend": 15_000_000.0,
            "scenario_currency": "CAD",
            "scenario_spend_source": "user_entered",
            "calculation_inputs": [{
                "input_key": "qualified_labour",
                "amount": 7_000_000.0,
                "currency": "CAD",
                "input_status": "planning_assumption",
                "input_source": "user_entered",
                "programme_id": "CA_BC_PSTC",
            }],
        }])
        scenario = request.territory_scenarios[0]
        assert scenario.subdivision_id == "CA-BC"
        supplied = scenario.calculation_inputs[0]
        assert supplied.input_status == "planning_assumption"
        assert supplied.programme_id == "CA_BC_PSTC"

    def test_every_canonical_key_is_accepted_by_the_contract(self):
        """The registry and the validator must not drift."""
        for key in CANONICAL_INPUTS:
            supplied = ScenarioCalculationInput(
                input_key=key, amount=1.0, input_status="known",
            )
            assert supplied.input_key == key
