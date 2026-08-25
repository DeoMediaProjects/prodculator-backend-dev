"""Incentive Engine v2, phase 00: non-entitlement mechanisms produce no figure.

The v2 handoff forbids "automatic numbers for competitive grants, investor tax
shelters, suspended or blocked programmes". Seven records were producing one. At a
GBP 20,000,000 budget the engine returned GBP 6.2m for the Belgian tax shelter,
GBP 8.0m for Singapore, GBP 5.3m for Japan, GBP 2.9m for India, GBP 2.0m for
Mexico EFICINE, GBP 119k for a suspended Korean programme and GBP 4.0m for a
Brazilian placeholder.

None of those percentages is claimable against production spend. An investor
shelter returns value to a third party through the tax system; a competitive award
is granted at a committee's discretion. The gate is the smallest change that stops
a producer taking those figures to a financier.

These tests pin the gate itself. The migration that classifies the records is
w3x4y5z6a7b8; the classification is asserted in
``test_v2_phase00_migration_targets`` at the bottom.
"""
from __future__ import annotations

import pytest

from app.modules.reports.helpers import (
    MECHANISM_NO_FIGURE_REASON,
    NON_ENTITLEMENT_ENGINES,
    QS_ENGINE_TYPES,
    mechanism_no_figure_reason,
    non_entitlement_mechanism,
)
from app.modules.reports.validator import ReportValidator


def _row(**overrides) -> dict:
    """An otherwise perfectly calculable programme row."""
    row = {
        "territory": "Testland",
        "program": "Test Programme",
        "status": "active",
        "rate_gross": 40.0,
        "rate_net": 30.0,
        "rate_type": "cash_rebate",
        "rate_tier_json": None,
        "atl_exempt": True,
        "qualifying_spend_type": "total",
        "qualifying_spend_cap_pct": None,
        "qualifying_spend_cap_amount": None,
        "qualifying_spend_labour_pct": None,
        "cap_amount": None,
        "cap_per_person": None,
        "rebate_cap_amount": None,
        "currency": "GBP",
        "qs_engine_type": None,
        "is_supplementary": False,
    }
    row.update(overrides)
    return row


def _figure(row: dict) -> float | None:
    result = ReportValidator._compute_corrected_rebate(dict(row), 20_000_000.0, {})
    return None if result is None else result["gross_rebate"]


# ── the classification itself ────────────────────────────────────────────────


class TestMechanismClassification:
    def test_the_engine_vocabulary_matches_the_specification(self):
        """Eleven QS engines plus NO_PROGRAMME, per the Calculation Engine Rules."""
        assert QS_ENGINE_TYPES == {
            "CORE_LOWER_OF", "ELIGIBLE_LOCAL_SPEND", "QUALIFIED_LABOUR",
            "MULTI_BUCKET", "QAPE", "QNZPE", "VFX_ONLY", "PDV_ONLY",
            "TIERED_SPEND", "INVESTOR_TAX_SHELTER", "COMPETITIVE_GRANT",
            "NO_PROGRAMME",
        }

    def test_non_entitlement_set_is_exactly_the_three_forbidden_mechanisms(self):
        assert NON_ENTITLEMENT_ENGINES == {
            "INVESTOR_TAX_SHELTER", "COMPETITIVE_GRANT", "NO_PROGRAMME",
        }

    def test_every_non_entitlement_engine_is_a_valid_engine(self):
        assert NON_ENTITLEMENT_ENGINES <= QS_ENGINE_TYPES

    def test_every_non_entitlement_engine_has_a_reader_facing_reason(self):
        assert set(MECHANISM_NO_FIGURE_REASON) == NON_ENTITLEMENT_ENGINES
        for engine, reason in MECHANISM_NO_FIGURE_REASON.items():
            assert len(reason) > 80, f"{engine} reason is too thin to be useful"

    @pytest.mark.parametrize("engine", sorted(NON_ENTITLEMENT_ENGINES))
    def test_non_entitlement_engines_are_recognised(self, engine):
        assert non_entitlement_mechanism({"qs_engine_type": engine}) is True

    @pytest.mark.parametrize("engine", ["CORE_LOWER_OF", "QUALIFIED_LABOUR",
                                        "MULTI_BUCKET", "QAPE", "TIERED_SPEND"])
    def test_entitlement_engines_are_not_gated(self, engine):
        assert non_entitlement_mechanism({"qs_engine_type": engine}) is False

    def test_case_and_whitespace_do_not_defeat_the_gate(self):
        assert non_entitlement_mechanism({"qs_engine_type": " investor_tax_shelter "})

    def test_an_unmigrated_row_is_not_gated(self):
        """NULL means not yet migrated to a v2 engine, and must behave as before."""
        assert non_entitlement_mechanism({"qs_engine_type": None}) is False
        assert non_entitlement_mechanism({}) is False

    def test_a_non_dict_is_handled(self):
        assert non_entitlement_mechanism(None) is False
        assert mechanism_no_figure_reason("not a row") is None


# ── the gate stops the arithmetic ────────────────────────────────────────────


class TestGateSuppressesTheFigure:
    def test_an_unclassified_row_still_calculates(self):
        """Guards against the gate suppressing everything."""
        assert _figure(_row()) == pytest.approx(8_000_000.0)

    @pytest.mark.parametrize("engine", sorted(NON_ENTITLEMENT_ENGINES))
    def test_a_non_entitlement_row_produces_no_figure(self, engine):
        assert _figure(_row(qs_engine_type=engine)) is None

    def test_the_gate_runs_before_the_rate_is_read(self):
        """A shelter with a rate, a cap and a tier set still yields nothing, so
        the suppression cannot be defeated by adding calculable data."""
        row = _row(
            qs_engine_type="INVESTOR_TAX_SHELTER",
            rate_gross=42.0, rate_net=42.0,
            rebate_cap_amount=7_250_000.0, rebate_cap_currency="EUR",
            qualifying_spend_cap_pct=80.0,
        )
        assert _figure(row) is None

    @pytest.mark.parametrize("engine", sorted(NON_ENTITLEMENT_ENGINES))
    def test_the_reason_is_available_wherever_the_figure_was(self, engine):
        reason = mechanism_no_figure_reason(_row(qs_engine_type=engine))
        assert reason and reason == MECHANISM_NO_FIGURE_REASON[engine]

    def test_an_entitlement_with_a_shelter_rate_type_still_calculates(self):
        """rate_type is not the gate. Only qs_engine_type is, so a mislabelled
        rate_type cannot silently suppress a real credit."""
        assert _figure(_row(rate_type="tax_shelter")) == pytest.approx(8_000_000.0)


# ── reasons say the right thing ──────────────────────────────────────────────


class TestReasonWording:
    def test_the_shelter_reason_explains_who_receives_the_benefit(self):
        reason = MECHANISM_NO_FIGURE_REASON["INVESTOR_TAX_SHELTER"].lower()
        assert "investor" in reason
        assert "not a production rebate" in reason

    def test_the_grant_reason_denies_entitlement(self):
        reason = MECHANISM_NO_FIGURE_REASON["COMPETITIVE_GRANT"].lower()
        assert "not an entitlement" in reason
        assert "ceiling" in reason

    def test_no_reason_promises_a_figure(self):
        for reason in MECHANISM_NO_FIGURE_REASON.values():
            lowered = reason.lower()
            for banned in ("guaranteed", "secured", "will receive", "qualifies for"):
                assert banned not in lowered, f"{banned!r} in {reason!r}"


# ── the migration targets the right records ──────────────────────────────────


def test_v2_phase00_migration_targets():
    """The seven records the plan named, with the engine each should carry.

    Read from the migration module rather than restated, so a change to the
    migration has to be a deliberate change to this expectation too.
    """
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "w3x4y5z6a7b8_v2_phase00_mechanism_gate.py"
    )
    assert path.exists(), "phase 00 migration is missing"

    import sys
    import types

    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location("_phase00", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            sys.modules.pop("alembic", None)

    mechanisms = {(t, e) for t, _like, e, _row in module._MECHANISMS}
    assert mechanisms == {
        ("Belgium", "INVESTOR_TAX_SHELTER"),
        ("Mexico", "INVESTOR_TAX_SHELTER"),
        ("Singapore", "COMPETITIVE_GRANT"),
        ("Japan", "COMPETITIVE_GRANT"),
        ("Brazil", "NO_PROGRAMME"),
        ("Nigeria", "NO_PROGRAMME"),
    }

    statuses = {(t, s) for t, _like, s, _row in module._STATUS_DISPOSITIONS}
    assert statuses == {("South Korea", "suspended"), ("India", "blocked")}

    for _t, _like, engine, _row in module._MECHANISMS:
        assert engine in NON_ENTITLEMENT_ENGINES, (
            f"{engine} is classified by the migration but the engine would still "
            f"calculate a figure for it"
        )


def test_the_admin_availability_path_refuses_a_gated_mechanism():
    """The reason must reach a surface, not merely be available in a helper."""
    from app.modules.incentives import service as incentives_service

    source = incentives_service.__file__
    text = open(source, encoding="utf-8").read()
    assert "mechanism_no_figure_reason(row)" in text, (
        "calculate_qualifying_spend does not consult the mechanism gate, so an "
        "investor shelter would still return a number through the admin path"
    )
    assert '"blocked"' in text, "the blocked status has no reader-facing wording"


def test_the_loader_and_the_migration_classify_the_same_programmes():
    """Two places set qs_engine_type and both are load bearing.

    Migration w3x4y5z6a7b8 fixes existing databases. The v4 loader
    (ab2c3d4e5f61) deletes and reinserts every row, so a fresh build takes its
    classification from there instead. If the two disagree, a rebuild silently
    un-suppresses a programme, which is how the IFTC cap fix became a no-op once
    before.
    """
    import importlib.util
    import sys
    import types
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"

    def load(filename, name):
        saved = sys.modules.get("alembic")
        stub = types.ModuleType("alembic")
        stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
        sys.modules["alembic"] = stub
        try:
            spec = importlib.util.spec_from_file_location(name, versions / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        finally:
            if saved is not None:
                sys.modules["alembic"] = saved
            else:
                sys.modules.pop("alembic", None)

    migration = load("w3x4y5z6a7b8_v2_phase00_mechanism_gate.py", "_p00")
    loader = load("ab2c3d4e5f61_incentives_v4_refresh.py", "_v4")

    from_migration = {(t, e) for t, _like, e, _row in migration._MECHANISMS}
    from_loader = {(t, e) for (t, _stem), e in loader._MECHANISM_BY_PROGRAMME.items()}
    assert from_migration == from_loader, (
        "the migration and the fresh-build loader disagree about which "
        "programmes are non-entitlement mechanisms"
    )

    # And the loader must actually apply it to a real source row.
    classified = {
        (r["territory"], r["qs_engine_type"])
        for r in (loader._build_row(s, "2026-08-22") for s in loader._SOURCE_ROWS)
        if r["qs_engine_type"]
    }
    assert classified == from_loader, (
        "a classification is declared but does not match any source row, so the "
        "programme name has probably changed"
    )
