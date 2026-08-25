"""The endpoint the wizard renders from.

The frontend must hold no territory logic, so everything about which questions
belong to which jurisdiction arrives from here. Two behaviours carry the weight:
anything that cannot resolve to a canonical jurisdiction is rejected rather than
silently dropped, and co-production partners are limited by a different number
than compared alternatives.
"""
from __future__ import annotations

import pytest

from app.core.dependencies import get_current_user, get_supabase
from app.modules.auth.schemas import AuthUser
from app.modules.incentives.v2_contracts import MAX_COPRODUCTION_PARTNERS
from app.modules.incentives.v2_scenario_service import ScenarioQuestionService


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self._rows = [r for r in self._rows if r.get(field) == value]
        return self

    def execute(self):
        return type("Result", (), {"data": list(self._rows)})()


class _Supabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _Query(self._tables.get(name, []))


_PROGRAMMES = [
    {"programme_id": "GB_AVEC", "program": "UK AVEC", "status": "active",
     "jurisdiction_country": "GB", "jurisdiction_subdivision": None,
     "qs_engine_type": "CORE_LOWER_OF",
     "calculation_verification_status": "blocked"},
    {"programme_id": "GB_IFTC", "program": "UK IFTC", "status": "active",
     "jurisdiction_country": "GB", "jurisdiction_subdivision": None,
     "qs_engine_type": "CORE_LOWER_OF",
     "calculation_verification_status": "blocked"},
    {"programme_id": "CA_BC_PSTC", "program": "BC PSTC", "status": "active",
     "jurisdiction_country": "CA", "jurisdiction_subdivision": "CA-BC",
     "qs_engine_type": "QUALIFIED_LABOUR",
     "calculation_verification_status": "blocked"},
    {"programme_id": "BE_SHELTER", "program": "Belgian Film Tax Shelter",
     "status": "active", "jurisdiction_country": "BE",
     "jurisdiction_subdivision": None,
     "qs_engine_type": "INVESTOR_TAX_SHELTER",
     "calculation_verification_status": "blocked"},
]

_INPUTS = [
    {"programme_id": "GB_AVEC", "input_key": "local_core_expenditure",
     "label": "UK core expenditure", "help_text": "Core costs in the UK.",
     "input_type": "currency", "required_for_exact": True},
    {"programme_id": "GB_AVEC", "input_key": "global_core_expenditure",
     "label": "Relevant global core expenditure",
     "help_text": "Total core costs worldwide.",
     "input_type": "currency", "required_for_exact": True},
    {"programme_id": "GB_IFTC", "input_key": "local_core_expenditure",
     "label": "UK core expenditure", "help_text": "Core costs in the UK.",
     "input_type": "currency", "required_for_exact": True},
    {"programme_id": "GB_IFTC", "input_key": "global_core_expenditure",
     "label": "Relevant global core expenditure",
     "help_text": "Total core costs worldwide.",
     "input_type": "currency", "required_for_exact": True},
    {"programme_id": "CA_BC_PSTC", "input_key": "qualified_labour",
     "label": "Accredited qualified BC labour",
     "help_text": "Accredited BC labour expenditure.",
     "input_type": "currency", "required_for_exact": True},
]


def _user(plan="professional"):
    return AuthUser(
        id="user-1", email="producer@example.com", full_name="A Producer", plan=plan,
    )


@pytest.fixture()
def api(client):
    supabase = _Supabase({
        "incentive_programs": _PROGRAMMES,
        "programme_required_inputs": _INPUTS,
    })
    client.app.dependency_overrides[get_supabase] = lambda: supabase
    client.app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    client.app.dependency_overrides.clear()


# ── the jurisdiction list ────────────────────────────────────────────────────


def test_the_picker_gets_canonical_ids(client):
    """Free text must never resolve to a financial programme, so the client is
    handed the exact set it may choose from."""
    response = client.get("/api/scenarios/jurisdictions")
    assert response.status_code == 200
    jurisdictions = response.json()["jurisdictions"]
    assert len(jurisdictions) >= 53

    by_label = {j["label"]: j for j in jurisdictions}
    assert by_label["British Columbia"]["subdivisionId"] == "CA-BC"
    assert by_label["British Columbia"]["territoryId"] == "CA"
    assert by_label["British Columbia"]["isStatutorySubdivision"] is True
    assert by_label["United Kingdom"]["subdivisionId"] is None
    # Scotland is a region inside one UK-wide regime, not its own.
    assert by_label["Scotland"]["isStatutorySubdivision"] is False


# ── question resolution over the wire ────────────────────────────────────────


class TestQuestions:
    def test_the_uk_returns_both_core_figures_once(self, api):
        response = api.get("/api/scenarios/questions", params={"territories": "GB"})
        assert response.status_code == 200
        scenarios = response.json()["scenarios"]
        assert len(scenarios) == 1

        keys = [q["inputKey"] for q in scenarios[0]["questions"]]
        assert sorted(keys) == ["global_core_expenditure", "local_core_expenditure"]
        # Two programmes need the same figure; it is asked once and says so.
        core = next(q for q in scenarios[0]["questions"]
                    if q["inputKey"] == "local_core_expenditure")
        assert len(core["usedBy"]) == 2

    def test_a_subdivision_code_resolves(self, api):
        response = api.get(
            "/api/scenarios/questions", params={"territories": "CA-BC"},
        )
        assert response.status_code == 200
        scenario = response.json()["scenarios"][0]
        assert scenario["subdivisionId"] == "CA-BC"
        assert [q["inputKey"] for q in scenario["questions"]] == ["qualified_labour"]

    def test_several_jurisdictions_come_back_in_one_call(self, api):
        response = api.get(
            "/api/scenarios/questions", params={"territories": "GB,CA-BC"},
        )
        assert response.status_code == 200
        assert len(response.json()["scenarios"]) == 2

    def test_a_label_and_its_iso_code_collapse_to_one_card(self, api):
        """Otherwise the wizard would show two cards competing for one scenario."""
        response = api.get(
            "/api/scenarios/questions",
            params={"territories": "United Kingdom,GB"},
        )
        assert response.status_code == 200
        assert len(response.json()["scenarios"]) == 1

    def test_a_non_entitlement_programme_asks_nothing_and_says_why(self, api):
        response = api.get("/api/scenarios/questions", params={"territories": "BE"})
        assert response.status_code == 200
        scenario = response.json()["scenarios"][0]
        assert scenario["questions"] == []
        assert len(scenario["nonCalculating"]) == 1
        assert "investor tax shelter" in scenario["nonCalculating"][0]["reason"].lower()

    def test_a_territory_with_no_migrated_programme_returns_an_empty_card(self, api):
        """Not an error. The territory is real, its programmes are simply not yet
        on a v2 engine, so there is nothing to ask."""
        response = api.get(
            "/api/scenarios/questions", params={"territories": "Hungary"},
        )
        assert response.status_code == 200
        scenario = response.json()["scenarios"][0]
        assert scenario["questions"] == []
        assert scenario["programmes"] == []


class TestRejections:
    def test_free_text_is_rejected_not_skipped(self, api):
        """A silently dropped territory leaves a card with no questions and no
        reason, which reads as a bug."""
        response = api.get(
            "/api/scenarios/questions", params={"territories": "Califronia"},
        )
        assert response.status_code == 422
        assert "not a recognised jurisdiction" in response.json()["detail"]

    def test_one_bad_entry_rejects_the_whole_request(self, api):
        response = api.get(
            "/api/scenarios/questions", params={"territories": "GB,Nowhereland"},
        )
        assert response.status_code == 422

    def test_an_unknown_mode_is_rejected(self, api):
        response = api.get(
            "/api/scenarios/questions",
            params={"territories": "GB", "mode": "whatever"},
        )
        assert response.status_code == 422
        assert "mode must be one of" in response.json()["detail"]

    def test_no_territories_returns_an_empty_set(self, api):
        response = api.get("/api/scenarios/questions", params={"territories": " , "})
        assert response.status_code == 200
        assert response.json()["scenarios"] == []


# ── the two different limits ─────────────────────────────────────────────────


class TestLimits:
    def test_comparison_uses_the_plan_limit(self):
        assert ScenarioQuestionService.limit_for("comparison", "free") == 3
        assert ScenarioQuestionService.limit_for("comparison", "professional") == 5
        assert ScenarioQuestionService.limit_for("comparison", "producer") is None

    def test_undecided_is_limited_as_comparison(self):
        assert ScenarioQuestionService.limit_for("undecided", "free") == 3

    def test_coproduction_uses_the_partner_limit_regardless_of_plan(self):
        """A multilateral co-production needs at least three co-producers, so
        Explorer's comparison limit of three would permit only the bare legal
        minimum and make a four-partner structure unmodellable."""
        for plan in ("free", "professional", "producer", "studio"):
            assert ScenarioQuestionService.limit_for("coproduction", plan) == (
                MAX_COPRODUCTION_PARTNERS
            )

    def test_the_partner_limit_leaves_room_above_the_legal_minimum(self):
        from app.modules.incentives.v2_contracts import (
            MULTILATERAL_MINIMUM_PARTNERS,
        )

        assert MAX_COPRODUCTION_PARTNERS > MULTILATERAL_MINIMUM_PARTNERS

    def test_exceeding_the_comparison_limit_is_refused(self, client):
        supabase = _Supabase({
            "incentive_programs": _PROGRAMMES,
            "programme_required_inputs": _INPUTS,
        })
        client.app.dependency_overrides[get_supabase] = lambda: supabase
        client.app.dependency_overrides[get_current_user] = lambda: _user("free")
        try:
            response = client.get(
                "/api/scenarios/questions",
                params={"territories": "GB,CA-BC,US-CA,Hungary"},
            )
            assert response.status_code == 409
            assert "up to 3 territories" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.clear()

    def test_four_partners_are_allowed_in_coproduction_mode(self, client):
        """The same four jurisdictions an Explorer cannot compare are a legitimate
        co-production structure."""
        supabase = _Supabase({
            "incentive_programs": _PROGRAMMES,
            "programme_required_inputs": _INPUTS,
        })
        client.app.dependency_overrides[get_supabase] = lambda: supabase
        client.app.dependency_overrides[get_current_user] = lambda: _user("free")
        try:
            response = client.get(
                "/api/scenarios/questions",
                params={
                    "territories": "GB,CA-BC,US-CA,Hungary",
                    "mode": "coproduction",
                },
            )
            assert response.status_code == 200
            assert len(response.json()["scenarios"]) == 4
        finally:
            client.app.dependency_overrides.clear()

    def test_the_limit_is_reported_so_the_form_can_show_it(self, api):
        response = api.get("/api/scenarios/questions", params={"territories": "GB"})
        assert response.json()["limit"] == 5
