"""Incentive Engine v2, phases 01 and 02: schema, contracts and null semantics.

Three properties carry the weight here.

The field map is the single answer to "where does this v2 field live". Twenty of
the 51 specification fields already existed under another name, and adding all 51
would have left two columns per concept with no rule about which wins. That is the
failure this codebase has already paid for once, when ``cap``, ``cap_amount`` and
``rebate_cap_amount`` were three different things sharing one vague name.

Null semantics decide statuses. NULL means the producer has not told us and gives
REQUIRES_COST_BREAKDOWN. Zero means the producer told us there is none and gives a
calculated nil. Every fallback bug in the old engine violated that distinction.

Rule versions are selected by date, never by recency, because a newest-row
shortcut silently recalculates a 2025 production under a 2026 rate.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.modules.incentives.v2_contracts import (
    CALCULATION_STATUSES,
    CANONICAL_INPUTS,
    ENGINE_REQUIRED_INPUTS,
    EXCLUDED_FROM_RANKING,
    INPUT_SOURCES,
    INPUT_STATUSES,
    NUMERIC_STATUSES,
    QS_ENGINES,
    REUSED_COLUMNS,
    V2_FIELD_MAP,
    is_unknown,
    missing_required_inputs,
    resolve_statutory_amount,
    status_for_inputs,
    v2_column,
)
from app.modules.incentives.v2_rule_versions import (
    RuleVersionError,
    covers,
    qualifying_date,
    select_rule_version,
)
from app.modules.reports.helpers import QS_ENGINE_TYPES

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "x4y5z6a7b8c9_v2_programme_schema.py"


# ── vocabulary ───────────────────────────────────────────────────────────────


class TestVocabulary:
    def test_engine_list_matches_the_report_layer(self):
        """helpers must not import this module, so the two lists are duplicated
        deliberately. This is the assertion that keeps them honest."""
        assert set(QS_ENGINES) == QS_ENGINE_TYPES

    def test_every_engine_has_a_required_input_entry(self):
        assert set(ENGINE_REQUIRED_INPUTS) == set(QS_ENGINES)

    def test_declared_required_inputs_are_canonical_keys(self):
        for engine, keys in ENGINE_REQUIRED_INPUTS.items():
            for key in keys:
                assert key in CANONICAL_INPUTS, f"{engine} requires unknown {key!r}"

    def test_the_registry_holds_every_key_the_specification_names(self):
        """The ingestion specification lists fourteen. Extensions are allowed and
        expected, so this asserts the specified set is present rather than
        pinning a count that any new programme would break."""
        specified = {
            "eligible_local_spend", "qualified_labour", "resident_labour",
            "nonresident_labour", "vendor_spend", "local_core_expenditure",
            "global_core_expenditure", "vfx_expenditure", "pdv_expenditure",
            "qape", "pdv_qape", "qnzpe", "qualified_production_expenditure",
            "other_programme_bucket",
        }
        assert specified <= set(CANONICAL_INPUTS)

    def test_extensions_beyond_the_specification_are_uplift_bases(self):
        """An uplift pays a higher rate on a subset of spend, so the subset needs
        its own figure. Anything else added here would be a territory-specific
        key by another name, which the specification forbids."""
        specified = {
            "eligible_local_spend", "qualified_labour", "resident_labour",
            "nonresident_labour", "vendor_spend", "local_core_expenditure",
            "global_core_expenditure", "vfx_expenditure", "pdv_expenditure",
            "qape", "pdv_qape", "qnzpe", "qualified_production_expenditure",
            "other_programme_bucket",
        }
        assert set(CANONICAL_INPUTS) - specified == {
            "out_of_zone_expenditure", "local_hire_wages",
        }

    def test_no_registry_key_is_territory_specific(self):
        """The specification forbids uk_core_spend or bc_labour style keys."""
        banned = ("uk_", "bc_", "california", "georgia", "_us_", "australia")
        for key in CANONICAL_INPUTS:
            assert not any(b in key for b in banned), key

    def test_labour_and_core_engines_require_their_own_base(self):
        assert ENGINE_REQUIRED_INPUTS["QUALIFIED_LABOUR"] == ("qualified_labour",)
        assert set(ENGINE_REQUIRED_INPUTS["CORE_LOWER_OF"]) == {
            "local_core_expenditure", "global_core_expenditure",
        }

    def test_non_entitlement_engines_require_no_spend_base(self):
        """Neither is calculated from production spend, so asking for a base
        would imply one could be."""
        for engine in ("INVESTOR_TAX_SHELTER", "COMPETITIVE_GRANT", "NO_PROGRAMME"):
            assert ENGINE_REQUIRED_INPUTS[engine] == ()

    def test_input_provenance_has_no_ai_generated_member(self):
        """The narrative layer may explain an input but never populate one."""
        for value in INPUT_SOURCES:
            assert "ai" not in value.lower()
        assert set(INPUT_STATUSES) == {"known", "planning_assumption", "unknown"}


class TestCalculationStatuses:
    def test_only_estimated_and_conditional_may_carry_a_number(self):
        assert NUMERIC_STATUSES == {"ESTIMATED", "CONDITIONAL"}

    def test_unverified_blocked_suspended_and_no_programme_leave_the_ranking(self):
        assert EXCLUDED_FROM_RANKING == {
            "PROGRAMME_UNVERIFIED", "BLOCKED", "SUSPENDED", "NO_PROGRAMME",
        }

    def test_every_status_declares_both_treatments(self):
        for name, spec in CALCULATION_STATUSES.items():
            assert isinstance(spec["numeric"], bool), name
            assert isinstance(spec["in_ranking"], bool), name
            assert len(spec["meaning"]) > 30, name

    def test_requires_cost_breakdown_stays_in_the_ranking(self):
        """A programme can be worth ranking on non-financial grounds while its
        figure is unavailable. Dropping it would hide real coverage."""
        assert CALCULATION_STATUSES["REQUIRES_COST_BREAKDOWN"]["in_ranking"] is True
        assert CALCULATION_STATUSES["REQUIRES_COST_BREAKDOWN"]["numeric"] is False


# ── the field map ────────────────────────────────────────────────────────────


class TestFieldMap:
    def test_all_fifty_one_specification_fields_are_mapped(self):
        assert len(V2_FIELD_MAP) == 51

    def test_no_field_is_unassigned(self):
        unassigned = [k for k, v in V2_FIELD_MAP.items() if v is None]
        assert not unassigned, f"unassigned v2 fields: {unassigned}"

    def test_no_column_serves_two_v2_fields(self):
        """Two v2 concepts sharing a column would make the column ambiguous,
        which is how the cap fields went wrong."""
        columns = [v for v in V2_FIELD_MAP.values() if v]
        duplicates = {c for c in columns if columns.count(c) > 1}
        assert not duplicates, f"columns serving more than one v2 field: {duplicates}"

    @pytest.mark.parametrize("field,column", [
        ("base_rate", "rate_gross"),
        ("qs_absolute_cap", "qualifying_spend_cap_amount"),
        ("qs_percentage_cap", "qualifying_spend_cap_pct"),
        ("credit_output_cap", "rebate_cap_amount"),
        ("official_source_url", "source_url"),
        ("official_authority", "authority"),
        ("programme_name", "program"),
        ("minimum_spend_amount", "qualifying_spend_min"),
        ("source_verification_status", "verification_status"),
        ("last_rule_verified", "last_verified_at"),
    ])
    def test_existing_concepts_are_reused_not_duplicated(self, field, column):
        assert v2_column(field) == column
        assert field in REUSED_COLUMNS

    def test_roughly_twenty_fields_reuse_an_existing_column(self):
        assert 15 <= len(REUSED_COLUMNS) <= 25, len(REUSED_COLUMNS)

    def test_an_unknown_field_raises_rather_than_inventing_a_column(self):
        with pytest.raises(KeyError, match="not an Incentive Engine v2 field"):
            v2_column("qs_absolut_cap")

    def test_the_migration_adds_exactly_the_new_names(self):
        """The map and the migration must agree, or a v2 field acquires a second
        home the next time someone reads the specification."""
        migration = _load_migration()
        added = {name for name, _type in migration._NEW_PROGRAMME_COLUMNS}
        expected = {
            column for field, column in V2_FIELD_MAP.items()
            if column == field and field not in ("qs_engine_type",)
        }
        assert added == expected, (
            f"migration adds {sorted(added - expected)} that the map does not "
            f"claim as new, and omits {sorted(expected - added)}"
        )


def _load_migration():
    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(
        add_column=lambda *a, **k: None, create_table=lambda *a, **k: None,
    )
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location("_v2_schema", _MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            sys.modules.pop("alembic", None)


# ── null semantics ───────────────────────────────────────────────────────────


class TestNullSemantics:
    def test_none_is_unknown_and_zero_is_not(self):
        assert is_unknown(None) is True
        assert is_unknown(0) is False
        assert is_unknown(0.0) is False

    @pytest.mark.parametrize("raw,expected", [
        (None, None), ("", None), ("   ", None), ("not a number", None),
        (0, 0.0), ("0", 0.0), (7_000_000, 7_000_000.0),
        ("7,000,000", 7_000_000.0), (" 12345.5 ", 12_345.5),
    ])
    def test_amount_resolution_preserves_the_unknown_zero_split(self, raw, expected):
        assert resolve_statutory_amount(raw) == expected

    def test_a_boolean_is_never_an_amount(self):
        """True is 1 in Python, and a checkbox reaching an amount column must not
        silently become a one pound statutory base."""
        assert resolve_statutory_amount(True) is None
        assert resolve_statutory_amount(False) is None

    def test_a_known_zero_satisfies_a_required_input(self):
        assert missing_required_inputs("QUALIFIED_LABOUR", {"qualified_labour": 0}) == []

    def test_an_unknown_amount_does_not_satisfy_it(self):
        assert missing_required_inputs(
            "QUALIFIED_LABOUR", {"qualified_labour": None}
        ) == ["qualified_labour"]

    def test_an_absent_key_is_missing(self):
        assert missing_required_inputs("QUALIFIED_LABOUR", {}) == ["qualified_labour"]

    def test_a_multi_bucket_programme_declares_its_own_buckets(self):
        assert missing_required_inputs("MULTI_BUCKET", {}) == []
        assert missing_required_inputs(
            "MULTI_BUCKET", {"vendor_spend": 1.0},
            declared_required=("vendor_spend", "resident_labour"),
        ) == ["resident_labour"]


class TestStatusForInputs:
    def test_all_inputs_known_gives_estimated(self):
        assert status_for_inputs(
            "CORE_LOWER_OF",
            {"local_core_expenditure": 10.0, "global_core_expenditure": 20.0},
            {"local_core_expenditure": "known", "global_core_expenditure": "known"},
        ) == "ESTIMATED"

    def test_a_planning_assumption_downgrades_to_conditional(self):
        assert status_for_inputs(
            "CORE_LOWER_OF",
            {"local_core_expenditure": 10.0, "global_core_expenditure": 20.0},
            {"local_core_expenditure": "planning_assumption",
             "global_core_expenditure": "known"},
        ) == "CONDITIONAL"

    def test_a_missing_base_gives_requires_cost_breakdown(self):
        assert status_for_inputs(
            "QUALIFIED_LABOUR", {"qualified_labour": None}
        ) == "REQUIRES_COST_BREAKDOWN"

    def test_scenario_spend_cannot_stand_in_for_a_labour_base(self):
        """The single behaviour the rebuild exists to remove."""
        assert status_for_inputs(
            "QUALIFIED_LABOUR", {"eligible_local_spend": 15_000_000.0}
        ) == "REQUIRES_COST_BREAKDOWN"

    def test_non_entitlement_engines_never_reach_estimated(self):
        for engine in ("INVESTOR_TAX_SHELTER", "COMPETITIVE_GRANT"):
            assert status_for_inputs(engine, {"eligible_local_spend": 1.0}) == "CONDITIONAL"

    def test_no_programme_says_so(self):
        assert status_for_inputs("NO_PROGRAMME", {}) == "NO_PROGRAMME"


# ── rule versions ────────────────────────────────────────────────────────────


def _version(version, start, end=None, status="ready"):
    return {
        "rule_version": version,
        "effective_from": start,
        "effective_to": end,
        "calculation_verification_status": status,
    }


class TestRuleVersionSelection:
    _HISTORY = [
        _version("2024.1", "2024-04-01", "2025-03-31"),
        _version("2025.1", "2025-04-01", "2026-03-31"),
        _version("2026.1", "2026-04-01", None),
    ]

    @pytest.mark.parametrize("on,expected", [
        ("2024-06-01", "2024.1"),
        ("2025-03-31", "2024.1"),   # inclusive end
        ("2025-04-01", "2025.1"),   # inclusive start
        ("2026-03-31", "2025.1"),
        ("2026-04-01", "2026.1"),
        ("2030-01-01", "2026.1"),   # open ended current version
    ])
    def test_selection_is_by_date_not_recency(self, on, expected):
        chosen = select_rule_version(self._HISTORY, date.fromisoformat(on))
        assert chosen["rule_version"] == expected

    def test_a_date_before_every_version_raises(self):
        with pytest.raises(RuleVersionError, match="No rule version in force"):
            select_rule_version(self._HISTORY, date(2020, 1, 1))

    def test_it_does_not_fall_back_to_the_newest_version(self):
        """Falling back would produce a figure under a rule that did not apply."""
        with pytest.raises(RuleVersionError):
            select_rule_version(self._HISTORY, date(2019, 1, 1))

    def test_overlapping_periods_raise_rather_than_picking_one(self):
        overlapping = [
            _version("a", "2026-01-01", "2026-12-31"),
            _version("b", "2026-06-01", None),
        ]
        with pytest.raises(RuleVersionError, match="rule versions are in force"):
            select_rule_version(overlapping, date(2026, 8, 1))

    def test_a_version_that_is_not_calculation_ready_cannot_produce_a_figure(self):
        blocked = [_version("2026.1", "2026-01-01", None, status="blocked")]
        with pytest.raises(RuleVersionError, match="not 'ready'"):
            select_rule_version(blocked, date(2026, 8, 1))

    def test_a_blocked_version_is_still_readable_for_admin(self):
        blocked = [_version("2026.1", "2026-01-01", None, status="blocked")]
        chosen = select_rule_version(blocked, date(2026, 8, 1), require_ready=False)
        assert chosen["rule_version"] == "2026.1"

    def test_a_null_end_date_means_current_not_inapplicable(self):
        assert covers(_version("x", "2026-01-01", None), date(2026, 8, 1)) is True

    def test_a_missing_start_date_never_covers(self):
        assert covers({"effective_from": None}, date(2026, 8, 1)) is False


class TestQualifyingDate:
    def test_principal_photography_is_preferred(self):
        assert qualifying_date({
            "filming_start_date": "2026-09-01", "completion_date": "2027-03-01",
        }) == date(2026, 9, 1)

    def test_completion_is_the_fallback(self):
        assert qualifying_date({"completion_date": "2027-03-01"}) == date(2027, 3, 1)

    def test_no_date_returns_none_rather_than_today(self):
        """Today's rate is not necessarily the rate for a future shoot."""
        assert qualifying_date({}) is None
        assert qualifying_date({"filming_start_date": ""}) is None


# ── the migration actually runs ──────────────────────────────────────────────


def test_the_migration_executes_against_a_real_database(tmp_path):
    """Runs upgrade() through a genuine Alembic operations context.

    The full chain cannot run on SQLite because earlier migrations use Postgres
    only constructs, so this exercises just this revision against a stub table.
    It proves the DDL is valid and the tables are created, which importing the
    module does not.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'v2.db'}")
    metadata = sa.MetaData()
    sa.Table(
        "incentive_programs", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("territory", sa.Text()),
        sa.Column("program", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("calculation_verification_status", sa.String(24)),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO incentive_programs (id, territory, program, status) "
            "VALUES ('1', 'United Kingdom', 'AVEC', 'active')"
        ))

    spec = importlib.util.spec_from_file_location("_v2_run", _MIGRATION)
    module = importlib.util.module_from_spec(spec)

    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        operations = Operations(context)
        import alembic

        saved = getattr(alembic, "op", None)
        alembic.op = operations
        try:
            spec.loader.exec_module(module)
            module.upgrade()
        finally:
            if saved is not None:
                alembic.op = saved

    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    for expected in (
        "programme_rule_versions", "programme_required_inputs",
        "territory_scenarios", "scenario_calculation_inputs",
        "calculation_results",
    ):
        assert expected in tables, f"{expected} was not created"

    columns = {c["name"] for c in inspector.get_columns("incentive_programs")}
    for name, _type in module._NEW_PROGRAMME_COLUMNS:
        assert name in columns, f"{name} was not added"

    # Existing rows default to blocked, so a source-verified badge cannot imply
    # the formula is ready.
    with engine.connect() as conn:
        status = conn.execute(sa.text(
            "SELECT calculation_verification_status FROM incentive_programs"
        )).scalar()
    assert status == "blocked"


def test_scenario_spend_and_input_amounts_are_nullable(tmp_path):
    """A selected territory with no spend entered yet is a real state. Making the
    column NOT NULL would force a zero or a copied budget, which is the
    substitution the rebuild removes."""
    migration = _load_migration()
    source = _MIGRATION.read_text(encoding="utf-8")
    # scenario_spend and amount must not be declared nullable=False
    for column in ("scenario_spend", "amount"):
        pattern = f'sa.Column("{column}", sa.Float()'
        assert pattern in source, f"{column} declaration changed"
        after = source.split(pattern, 1)[1][:60]
        assert "nullable=False" not in after, (
            f"{column} became NOT NULL, which forces a substituted value"
        )
    assert migration is not None


def _run_migration(filename: str, engine) -> object:
    """Execute one migration's upgrade() through a real Alembic context."""
    import alembic
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location(
        f"_run_{filename}", _VERSIONS / filename
    )
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        operations = Operations(context)
        saved = getattr(alembic, "op", None)
        alembic.op = operations
        try:
            spec.loader.exec_module(module)
            module.upgrade()
        finally:
            if saved is not None:
                alembic.op = saved
    return module


def _stub_incentive_table(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "incentive_programs", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("territory", sa.Text()),
        sa.Column("program", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("calculation_verification_status", sa.String(24)),
    )
    metadata.create_all(engine)


def test_the_coproduction_migration_executes(tmp_path):
    """y5z6a7b8c9d0 had never been run anywhere before this test.

    Asking someone to apply unexecuted DDL to a production database is how a
    deploy fails halfway through a transaction, so it runs here first.
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'coprod.db'}")
    _stub_incentive_table(engine)
    _run_migration("x4y5z6a7b8c9_v2_programme_schema.py", engine)
    module = _run_migration("y5z6a7b8c9d0_v2_coproduction_structure.py", engine)

    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert "co_production_structures" in tables
    assert "supranational_support_records" in tables

    scenario_columns = {
        c["name"] for c in inspector.get_columns("territory_scenarios")
    }
    for name, _type in module._SCENARIO_COLUMNS:
        assert name in scenario_columns, f"{name} was not added"

    # The two defaults that keep an unreviewed structure from reading as finance.
    structure = {c["name"]: c for c in inspector.get_columns("co_production_structures")}
    assert "not_checked" in str(structure["cumulation_status"]["default"])
    assert "comparison" in str(structure["mode"]["default"])

    support = {
        c["name"]: c for c in inspector.get_columns("supranational_support_records")
    }
    assert "potential" in str(support["support_status"]["default"])


def test_migrations_are_rerunnable(tmp_path):
    """Each guards its own DDL, so a partially applied chain can be resumed."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'rerun.db'}")
    _stub_incentive_table(engine)
    for filename in (
        "x4y5z6a7b8c9_v2_programme_schema.py",
        "y5z6a7b8c9d0_v2_coproduction_structure.py",
    ):
        _run_migration(filename, engine)
        _run_migration(filename, engine)  # second pass must not raise

    inspector = sa.inspect(engine)
    assert "co_production_structures" in set(inspector.get_table_names())


def test_the_seed_migration_declares_only_programmes_with_authoritative_rules():
    """z6a7b8c9d0e1 uses ILIKE and cannot execute on SQLite, so its content is
    asserted rather than its execution. Every declared input must be a canonical
    key, and every engine must be one the dispatcher knows."""
    module = _load_named("z6a7b8c9d0e1_v2_seed_batch1_inputs.py")

    from app.modules.incentives.v2_contracts import CANONICAL_INPUTS, QS_ENGINES

    programme_ids = {p for p, *_ in module._IDENTITY}
    assert programme_ids == {
        "GB_AVEC", "GB_IFTC", "GB_VFX_ENHANCED",
        "CA_FEDERAL_PSTC", "CA_CPTC", "CA_BC_PSTC",
    }

    for _pid, _terr, _like, _country, _sub, engine in module._IDENTITY:
        assert engine in QS_ENGINES, engine

    for programme_id, input_key, label, _required, help_text in module._REQUIRED_INPUTS:
        assert programme_id in programme_ids, programme_id
        assert input_key in CANONICAL_INPUTS, input_key
        assert label and help_text, f"{programme_id}:{input_key} lacks producer copy"

    # The lower-of rule needs both core figures or it cannot run at all.
    for programme_id in ("GB_AVEC", "GB_IFTC"):
        keys = {
            k for p, k, *_ in module._REQUIRED_INPUTS if p == programme_id
        }
        assert keys == {"local_core_expenditure", "global_core_expenditure"}, (
            f"{programme_id} is a lower-of programme and needs both core figures"
        )


def _load_named(filename: str):
    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(
        add_column=lambda *a, **k: None, create_table=lambda *a, **k: None,
    )
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location("_named", _VERSIONS / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            sys.modules.pop("alembic", None)
