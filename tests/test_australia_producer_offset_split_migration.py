"""d2e3f4a5b6c7 splits Australia's blended "Producer Offset" row by format.

Follows the same runs-against-a-real-database pattern as
test_v2_schema_and_contracts.py's migration tests: the full chain cannot run
on SQLite (earlier migrations use Postgres-only constructs), so this exercises
just this revision against a stub table.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import alembic
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
_MIGRATION = _VERSIONS / "d2e3f4a5b6c7_split_australia_producer_offset_by_format.py"

_TERRITORY = "Australia"
_ORIGINAL_PROGRAM = "Producer Offset"
_FEATURE_PROGRAM = "Producer Offset — Theatrical Feature Film"
_TV_PROGRAM = "Producer Offset — Television / Non-Theatrical"


def _stub_table(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "incentive_programs", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("territory", sa.Text()),
        sa.Column("program", sa.Text()),
        sa.Column("programme_id", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("rate_gross", sa.Float()),
        sa.Column("rate_net", sa.Float()),
        sa.Column("applicable_formats", sa.Text()),
        sa.Column("rate_tier_json", sa.Text()),
        sa.Column("last_verified_at", sa.Text()),
        sa.Column("created_at", sa.Text()),
        sa.Column("updated_at", sa.Text()),
        # Governance fields that gate this row out of foreign-production
        # reports — the clone must carry these over unchanged.
        sa.Column("nationality_requirements", sa.Text()),
        sa.Column("spv_eligible", sa.Boolean()),
        sa.Column("source_url", sa.Text()),
    )
    metadata.create_all(engine)


def _run(engine, direction: str):
    """Fresh-load and exec the module for each call.

    ``from alembic import op`` at module scope binds a name at *load* time —
    re-pointing ``alembic.op`` afterward does not change what an
    already-loaded module sees. Each direction therefore needs its own
    spec/module/exec, matching the pattern _run_migration uses elsewhere in
    this suite (never reusing a previously exec'd module across op-bound calls).
    """
    spec = importlib.util.spec_from_file_location(f"_run_d2e3f4a5b6c7_{direction}", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        operations = Operations(context)
        saved = getattr(alembic, "op", None)
        alembic.op = operations
        try:
            spec.loader.exec_module(module)
            getattr(module, direction)()
        finally:
            if saved is not None:
                alembic.op = saved
    return module


def _run_migration(engine):
    return _run(engine, "upgrade")


def _seed_original_row(engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO incentive_programs "
            "(id, territory, program, status, rate_gross, rate_net, "
            " nationality_requirements, spv_eligible, source_url) "
            "VALUES (:id, :territory, :program, 'active', 40, 40, "
            " :nat, 0, :src)"
        ), {
            "id": "original-row-1",
            "territory": _TERRITORY,
            "program": _ORIGINAL_PROGRAM,
            "nat": json.dumps(["AU"]),
            "src": "https://www.screenaustralia.gov.au/funding-and-support/producer-offset",
        })


def _rows(engine) -> list[dict]:
    with engine.connect() as conn:
        result = conn.execute(sa.text(
            "SELECT * FROM incentive_programs WHERE territory = :t ORDER BY program"
        ), {"t": _TERRITORY})
        return [dict(r._mapping) for r in result]


def test_splits_into_two_rows_with_correct_rates(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ao.db'}")
    _stub_table(engine)
    _seed_original_row(engine)

    _run_migration(engine)

    rows = {r["program"]: r for r in _rows(engine)}
    assert set(rows) == {_FEATURE_PROGRAM, _TV_PROGRAM}
    assert rows[_FEATURE_PROGRAM]["rate_gross"] == 40
    assert rows[_TV_PROGRAM]["rate_gross"] == 30


def test_gating_fields_are_preserved_on_the_new_row(tmp_path):
    """The TV row must remain excluded from foreign-production reports exactly
    like the original — this is what makes the split safe."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ao.db'}")
    _stub_table(engine)
    _seed_original_row(engine)

    _run_migration(engine)

    rows = {r["program"]: r for r in _rows(engine)}
    tv_row = rows[_TV_PROGRAM]
    assert json.loads(tv_row["nationality_requirements"]) == ["AU"]
    assert tv_row["spv_eligible"] in (False, 0)
    assert tv_row["source_url"] == (
        "https://www.screenaustralia.gov.au/funding-and-support/producer-offset"
    )


def test_new_row_gets_a_distinct_id_and_programme_id(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ao.db'}")
    _stub_table(engine)
    _seed_original_row(engine)

    _run_migration(engine)

    rows = {r["program"]: r for r in _rows(engine)}
    assert rows[_TV_PROGRAM]["id"] != rows[_FEATURE_PROGRAM]["id"]
    assert rows[_TV_PROGRAM]["programme_id"] is None


def test_is_idempotent(tmp_path):
    """A second run must not duplicate rows or raise."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ao.db'}")
    _stub_table(engine)
    _seed_original_row(engine)

    _run_migration(engine)
    _run_migration(engine)  # second pass: original row is already renamed, so no-op

    rows = _rows(engine)
    assert len(rows) == 2


def test_downgrade_restores_the_single_row(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ao.db'}")
    _stub_table(engine)
    _seed_original_row(engine)

    _run_migration(engine)
    _run(engine, "downgrade")

    rows = _rows(engine)
    assert len(rows) == 1
    assert rows[0]["program"] == _ORIGINAL_PROGRAM
    assert rows[0]["rate_gross"] == 40
