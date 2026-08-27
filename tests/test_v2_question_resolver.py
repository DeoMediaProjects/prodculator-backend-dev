"""Which statutory questions the wizard asks, and why the frontend cannot decide.

The specification requires the frontend to be a renderer with no business logic:
no ``if British Columbia, ask labour``. Programme records declare what they need,
and this resolves them per jurisdiction.

Two behaviours matter most. Selecting the United Kingdom brings AVEC, IFTC and the
VFX credit into scope, and AVEC and IFTC both need local and global core
expenditure, so the question must appear once and say it serves both. And a
non-entitlement programme must never be asked for a spend base, because the
question itself would imply the mechanism works in a way it does not.
"""
from __future__ import annotations

import pytest

from app.modules.incentives.v2_jurisdictions import resolve_jurisdiction
from app.modules.incentives.v2_question_resolver import (
    engine_default_inputs,
    programmes_for,
    resolve_questions,
    undeclared_requirements,
)


def _programme(programme_id, name, country, engine, subdivision=None, **extra):
    row = {
        "programme_id": programme_id,
        "program": name,
        "jurisdiction_country": country,
        "jurisdiction_subdivision": subdivision,
        "qs_engine_type": engine,
        "status": "active",
        "calculation_verification_status": "blocked",
    }
    row.update(extra)
    return row


def _declared(programme_id, input_key, label, help_text="", required=True):
    return {
        "programme_id": programme_id,
        "input_key": input_key,
        "label": label,
        "help_text": help_text,
        "input_type": "currency",
        "required_for_exact": required,
    }


_UK_PROGRAMMES = [
    _programme("GB_AVEC", "UK Audio-Visual Expenditure Credit (AVEC)", "GB",
               "CORE_LOWER_OF"),
    _programme("GB_IFTC", "AVEC (Enhanced/IFTC)", "GB", "CORE_LOWER_OF"),
    _programme("GB_VFX_ENHANCED", "UK VFX Expenditure Credit (Uplift)", "GB",
               "VFX_ONLY"),
]

_UK_INPUTS = [
    _declared("GB_AVEC", "local_core_expenditure", "UK core expenditure",
              "Core production costs incurred in the UK."),
    _declared("GB_AVEC", "global_core_expenditure",
              "Relevant global core expenditure", "Total core costs worldwide."),
    _declared("GB_IFTC", "local_core_expenditure", "UK core expenditure",
              "Core production costs incurred in the UK."),
    _declared("GB_IFTC", "global_core_expenditure",
              "Relevant global core expenditure", "Total core costs worldwide."),
    _declared("GB_VFX_ENHANCED", "vfx_expenditure",
              "Qualifying UK visual effects expenditure", "UK VFX costs only."),
]

_CANADA = [
    _programme("CA_FEDERAL_PSTC", "Production Services Tax Credit (Federal)", "CA",
               "QUALIFIED_LABOUR"),
    _programme("CA_CPTC", "Canadian Film or Video Production Tax Credit", "CA",
               "QUALIFIED_LABOUR"),
    _programme("CA_BC_PSTC", "BC Production Services Tax Credit (PSTC)", "CA",
               "QUALIFIED_LABOUR", subdivision="CA-BC"),
]

_CANADA_INPUTS = [
    _declared("CA_FEDERAL_PSTC", "qualified_labour",
              "Qualified Canadian labour expenditure"),
    _declared("CA_CPTC", "qualified_labour", "Qualified labour expenditure"),
    _declared("CA_BC_PSTC", "qualified_labour", "Accredited qualified BC labour"),
]


# ── scope ────────────────────────────────────────────────────────────────────


class TestProgrammeScope:
    def test_a_country_gets_its_national_programmes(self):
        rows = programmes_for(resolve_jurisdiction("United Kingdom"), _UK_PROGRAMMES)
        assert {r["programme_id"] for r in rows} == {
            "GB_AVEC", "GB_IFTC", "GB_VFX_ENHANCED",
        }

    def test_a_country_does_not_pick_up_subdivision_programmes(self):
        """Selecting Canada must not ask for BC's accredited labour."""
        rows = programmes_for(resolve_jurisdiction("Canada"), _CANADA)
        assert {r["programme_id"] for r in rows} == {"CA_FEDERAL_PSTC", "CA_CPTC"}

    def test_a_subdivision_gets_only_its_own(self):
        """British Columbia must not pick up every Canadian record, or a scenario
        for one province would ask for another's statutory base."""
        rows = programmes_for(resolve_jurisdiction("British Columbia"), _CANADA)
        assert {r["programme_id"] for r in rows} == {"CA_BC_PSTC"}

    def test_an_inactive_programme_is_out_of_scope(self):
        rows = programmes_for(
            resolve_jurisdiction("United Kingdom"),
            _UK_PROGRAMMES + [
                _programme("GB_OLD", "Retired", "GB", "CORE_LOWER_OF",
                           status="suspended"),
            ],
        )
        assert "GB_OLD" not in {r["programme_id"] for r in rows}

    def test_an_unrelated_territory_contributes_nothing(self):
        assert programmes_for(resolve_jurisdiction("Hungary"), _UK_PROGRAMMES) == []


# ── question resolution ──────────────────────────────────────────────────────


class TestUnitedKingdom:
    @pytest.fixture()
    def resolved(self):
        return resolve_questions(
            resolve_jurisdiction("United Kingdom"), _UK_PROGRAMMES, _UK_INPUTS,
        )

    def test_the_lower_of_rule_asks_for_both_core_figures(self):
        """QS is MIN(UK core, 80 percent of global core). Asking for one would
        leave the rule unable to run, which is where the co-production demo's
        single base per territory falls short."""
        resolved = resolve_questions(
            resolve_jurisdiction("United Kingdom"), _UK_PROGRAMMES, _UK_INPUTS,
        )
        keys = {q.input_key for q in resolved.questions}
        assert "local_core_expenditure" in keys
        assert "global_core_expenditure" in keys

    def test_a_shared_question_appears_once(self, resolved):
        """AVEC and IFTC both need core expenditure. Asking twice would let a
        producer enter two different figures for one statutory quantity."""
        keys = [q.input_key for q in resolved.questions]
        assert len(keys) == len(set(keys))
        assert len(keys) == 3

    def test_a_shared_question_names_every_programme_it_serves(self, resolved):
        core = next(
            q for q in resolved.questions if q.input_key == "local_core_expenditure"
        )
        assert len(core.used_by) == 2
        assert "AVEC" in " ".join(core.used_by)
        assert "IFTC" in " ".join(core.used_by)

    def test_the_vfx_question_belongs_to_one_programme(self, resolved):
        vfx = next(q for q in resolved.questions if q.input_key == "vfx_expenditure")
        assert vfx.used_by == ("UK VFX Expenditure Credit (Uplift)",)

    def test_help_text_explains_the_statutory_term(self, resolved):
        for question in resolved.questions:
            assert question.help_text, f"{question.input_key} has no explanation"

    def test_the_card_lists_the_programmes_in_scope(self, resolved):
        assert len(resolved.programmes) == 3
        assert resolved.territory_id == "GB"
        assert resolved.subdivision_id is None


class TestCanada:
    def test_british_columbia_asks_only_for_accredited_labour(self):
        resolved = resolve_questions(
            resolve_jurisdiction("British Columbia"), _CANADA, _CANADA_INPUTS,
        )
        assert [q.input_key for q in resolved.questions] == ["qualified_labour"]
        assert resolved.questions[0].label == "Accredited qualified BC labour"
        assert resolved.subdivision_id == "CA-BC"

    def test_federal_canada_asks_once_for_two_programmes(self):
        resolved = resolve_questions(
            resolve_jurisdiction("Canada"), _CANADA, _CANADA_INPUTS,
        )
        assert [q.input_key for q in resolved.questions] == ["qualified_labour"]
        assert len(resolved.questions[0].used_by) == 2


class TestNonEntitlementProgrammes:
    _BELGIUM = [
        _programme("BE_TAX_SHELTER", "Belgian Film Tax Shelter", "BE",
                   "INVESTOR_TAX_SHELTER"),
    ]

    def test_no_spend_question_is_asked(self):
        """A question implying a shelter is calculated from production spend
        would mislead before the producer even answers it."""
        resolved = resolve_questions(
            resolve_jurisdiction("Belgium"), self._BELGIUM,
            [_declared("BE_TAX_SHELTER", "eligible_local_spend", "Belgian spend")],
        )
        assert resolved.questions == []

    def test_the_reason_is_surfaced_instead(self):
        """An empty accordion must read as a statement, not an oversight."""
        resolved = resolve_questions(
            resolve_jurisdiction("Belgium"), self._BELGIUM, [],
        )
        assert len(resolved.non_calculating) == 1
        assert "investor tax shelter" in resolved.non_calculating[0]["reason"].lower()


class TestResolverSafety:
    def test_a_programme_with_no_declared_inputs_asks_nothing(self):
        """The engine default decides whether it can calculate, but must not
        invent a question whose wording nobody reviewed."""
        resolved = resolve_questions(
            resolve_jurisdiction("United Kingdom"), _UK_PROGRAMMES, [],
        )
        assert resolved.questions == []
        assert len(resolved.programmes) == 3

    def test_a_non_canonical_input_key_is_ignored(self):
        """No engine reads it, so a question for it could never change a result."""
        resolved = resolve_questions(
            resolve_jurisdiction("United Kingdom"), _UK_PROGRAMMES,
            [_declared("GB_AVEC", "uk_core_spend", "Invented key")],
        )
        assert resolved.questions == []

    def test_required_questions_sort_before_optional_ones(self):
        resolved = resolve_questions(
            resolve_jurisdiction("United Kingdom"), _UK_PROGRAMMES,
            _UK_INPUTS + [
                _declared("GB_AVEC", "vendor_spend", "Optional vendor spend",
                          required=False),
            ],
        )
        assert resolved.questions[-1].input_key == "vendor_spend"
        assert all(q.required_for_exact for q in resolved.questions[:-1])

    def test_the_output_serialises_for_the_frontend(self):
        payload = resolve_questions(
            resolve_jurisdiction("United Kingdom"), _UK_PROGRAMMES, _UK_INPUTS,
        ).as_dict()
        assert payload["territoryId"] == "GB"
        assert {q["inputKey"] for q in payload["questions"]} == {
            "local_core_expenditure", "global_core_expenditure", "vfx_expenditure",
        }
        assert all("helpText" in q for q in payload["questions"])


class TestAdminDiagnostics:
    def test_an_engine_requiring_an_undeclared_input_is_reported(self):
        """Such a programme can never calculate, which an administrator should be
        told rather than discovering through a permanently blank figure."""
        row = _programme("GB_AVEC", "AVEC", "GB", "CORE_LOWER_OF")
        missing = undeclared_requirements(
            row, [_declared("GB_AVEC", "local_core_expenditure", "UK core")],
        )
        assert missing == ["global_core_expenditure"]

    def test_a_fully_declared_programme_reports_nothing(self):
        row = _programme("GB_AVEC", "AVEC", "GB", "CORE_LOWER_OF")
        assert undeclared_requirements(row, _UK_INPUTS) == []

    def test_a_non_entitlement_programme_needs_no_declarations(self):
        row = _programme("BE_TS", "Shelter", "BE", "INVESTOR_TAX_SHELTER")
        assert undeclared_requirements(row, []) == []

    def test_engine_defaults_match_the_contract(self):
        assert set(engine_default_inputs("CORE_LOWER_OF")) == {
            "local_core_expenditure", "global_core_expenditure",
        }
        assert engine_default_inputs("QUALIFIED_LABOUR") == ("qualified_labour",)


# ── California, sourced from the official guidelines ─────────────────────────


class TestCaliforniaRuleRecord:
    """Program 4.0 was in neither handoff pack and gates the milestone.

    Sourced from the California Film Commission Program Guidelines dated
    1 January 2026. The Commission's own summary page states 25 percent; the
    guidelines state 35 percent. The guidelines are the operative document and
    carry a version date, so that is what the record uses.
    """

    @staticmethod
    def _module():
        return _load_california()

    def test_three_regimes_are_recorded_not_one(self):
        """The guidelines define three that differ in rate, ceiling and
        transferability, and the QA matrix requires independent and
        non-independent to apply different ceilings."""
        module = self._module()
        by_id = {r[0]: r for r in module._RECORDS}
        assert set(by_id) == {
            "US_CA_PROGRAM_4_NON_INDEPENDENT",
            "US_CA_PROGRAM_4_RELOCATING_TV",
            "US_CA_PROGRAM_4_INDEPENDENT",
        }

    def test_the_rates_and_ceilings_match_the_guidelines(self):
        module = self._module()
        expected = {
            "US_CA_PROGRAM_4_NON_INDEPENDENT": (35.0, 120_000_000.0, 42_000_000.0),
            "US_CA_PROGRAM_4_RELOCATING_TV": (40.0, 120_000_000.0, 48_000_000.0),
            "US_CA_PROGRAM_4_INDEPENDENT": (35.0, 20_000_000.0, 7_000_000.0),
        }
        for programme_id, _name, rate, ceiling, cap, _transferable in module._RECORDS:
            assert (rate, ceiling, cap) == expected[programme_id], programme_id

    def test_each_effective_cap_is_the_rate_times_the_ceiling(self):
        """A ceiling and a cap that disagree would mean one of them is wrong."""
        module = self._module()
        for programme_id, _n, rate, ceiling, cap, _t in module._RECORDS:
            assert ceiling * rate / 100 == pytest.approx(cap), programme_id

    def test_only_the_independent_regime_is_transferable(self):
        module = self._module()
        transferable = {
            p for p, _n, _r, _c, _cap, t in module._RECORDS if t
        }
        assert transferable == {"US_CA_PROGRAM_4_INDEPENDENT"}

    def test_uplifts_are_stored_against_their_own_spend_bucket(self):
        """Summing 35 plus 5 plus 5 plus 10 to 55 percent would be the same
        mistake as adding federal and provincial Canadian rates."""
        from app.modules.incentives.v2_contracts import CANONICAL_INPUTS

        module = self._module()
        for uplift in module._UPLIFTS:
            assert uplift["applies_to_input"] in CANONICAL_INPUTS, uplift["key"]
            assert uplift["rate_percent"] in (5.0, 10.0)
            assert uplift["condition"]

    def test_the_vfx_uplift_records_its_gate(self):
        module = self._module()
        vfx = next(u for u in module._UPLIFTS if u["key"] == "visual_effects")
        assert "10,000,000" in vfx["condition"]
        assert "75 percent" in vfx["condition"]

    def test_relocating_tv_gets_the_reduced_local_hire_rate(self):
        module = self._module()
        local = next(u for u in module._UPLIFTS if u["key"] == "local_hire_labour")
        assert local["rate_percent"] == 10.0
        assert local["reduced_rate_percent"] == 5.0
        assert "Relocating TV series" in local["reduced_for_categories"]

    def test_every_declared_input_is_canonical(self):
        from app.modules.incentives.v2_contracts import CANONICAL_INPUTS

        module = self._module()
        for input_key, label, _required, help_text in module._INPUTS:
            assert input_key in CANONICAL_INPUTS, input_key
            assert label and help_text

    def test_only_the_base_is_required_for_a_figure(self):
        """An absent uplift base means the uplift is not earned, not that the
        programme cannot be calculated at all."""
        module = self._module()
        required = {k for k, _l, r, _h in module._INPUTS if r}
        assert required == {"qualified_production_expenditure"}

    def test_provenance_is_complete(self):
        """No programme may be calculation ready without official provenance, so
        the record carries the source, the authority and effective dates."""
        module = self._module()
        assert "film.ca.gov" in module._SOURCE_URL
        assert module._AUTHORITY == "California Film Commission"
        assert "1 January 2026" in module._LEGAL_REFERENCE
        assert module._EFFECTIVE_FROM == "2025-07-01"
        assert module._EFFECTIVE_TO == "2030-06-30"
        assert module._ANNUAL_POOL == 750_000_000.0

    def test_the_migration_does_not_self_approve(self):
        """Sourcing is not administrator approval. Every record must land
        blocked, or research would silently become permission to calculate."""
        source = (
            _VERSIONS_CA / "a7b8c9d0e1f2_v2_california_program_4.py"
        ).read_text(encoding="utf-8")
        assert "'blocked'" in source
        assert "calculation_verification_status = 'ready'" not in source
        assert "= 'ready'" not in source


from pathlib import Path  # noqa: E402

_VERSIONS_CA = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load_california():
    import importlib.util
    import sys
    import types

    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "_ca", _VERSIONS_CA / "a7b8c9d0e1f2_v2_california_program_4.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            sys.modules.pop("alembic", None)
