"""b1c2d3e4f5a6 puts the United States row back, and stops case deciding things.

The 2026-09-21 production inventory found three statuses reading "Active" with
a capital A, one of them the United States row that `x3y4z5a6b7c8` had
corrected to `no_programme`. Lowercased, 'Active' reads as active, the picker
gives the country `hasOwnIncentive: true`, and it stops being a grouping
control — so "Expected spend per territory" asks for a figure against a
programme whose own name says it does not exist.

Same harness as the other migration tests: the full chain cannot run on
SQLite, so this exercises just this revision against a stub table.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import alembic
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
_MIGRATION = _VERSIONS / "b1c2d3e4f5a6_us_row_active_again.py"

_CORRECTED = "No federal film incentive (state programmes apply)"


def _stub_table(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "incentive_programs", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("territory", sa.Text()),
        sa.Column("program", sa.Text()),
        sa.Column("status", sa.Text()),
    )
    metadata.create_all(engine)


def _run(engine):
    spec = importlib.util.spec_from_file_location("_run_b1c2d3e4f5a6", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        operations = Operations(MigrationContext.configure(conn))
        saved = getattr(alembic, "op", None)
        alembic.op = operations
        try:
            spec.loader.exec_module(module)
            module.upgrade()
        finally:
            if saved is not None:
                alembic.op = saved


def _seed(engine, rows: list[tuple[str, str, str, str | None]]) -> None:
    with engine.begin() as conn:
        for row_id, territory, program, status in rows:
            conn.execute(
                sa.text(
                    "INSERT INTO incentive_programs (id, territory, program, status) "
                    "VALUES (:id, :t, :p, :s)"
                ),
                {"id": row_id, "t": territory, "p": program, "s": status},
            )


def _statuses(engine) -> dict[str, str | None]:
    with engine.begin() as conn:
        return {
            r[0]: r[1]
            for r in conn.execute(sa.text("SELECT id, status FROM incentive_programs"))
        }


@pytest.fixture()
def engine():
    eng = sa.create_engine("sqlite://")
    _stub_table(eng)
    return eng


def test_the_united_states_row_goes_back_to_no_programme(engine):
    _seed(engine, [("us", "United States", _CORRECTED, "Active")])
    _run(engine)
    assert _statuses(engine)["us"] == "no_programme"


def test_it_catches_a_us_row_under_any_other_status(engine):
    # The row came back as "Active" this time. The correction is about the
    # country conferring nothing, not about one particular status string.
    _seed(engine, [
        ("a", "United States", _CORRECTED, "active"),
        ("b", "United States", "Some federal scheme", "admin_verify_required"),
        ("c", "United States", "Another", None),
    ])
    _run(engine)
    assert set(_statuses(engine).values()) == {"no_programme"}


def test_the_states_keep_their_real_programmes(engine):
    # The whole point of the correction is that the incentives live here.
    _seed(engine, [
        ("ny", "New York", "New York State Film Tax Credit", "active"),
        ("ca", "California", "California Program 4", "Active"),
        ("us", "United States", _CORRECTED, "Active"),
    ])
    _run(engine)
    statuses = _statuses(engine)
    assert statuses["ny"] == "active"
    assert statuses["ca"] == "active"  # lowercased, not suppressed
    assert statuses["us"] == "no_programme"


def test_every_status_is_lowercased(engine):
    # `= 'active'` is case-sensitive in Postgres. The v2 scenario service's own
    # docstring records four programmes that contributed no wizard questions at
    # all because of exactly this, silently.
    _seed(engine, [
        ("a", "Canada", "PSTC", "Active"),
        ("b", "United Kingdom", "Enhanced AVEC", "Active"),
        ("c", "Spain", "General", "SUSPENDED"),
    ])
    _run(engine)
    assert _statuses(engine) == {"a": "active", "b": "active", "c": "suspended"}


def test_running_it_twice_changes_nothing_further(engine):
    _seed(engine, [
        ("us", "United States", _CORRECTED, "Active"),
        ("ny", "New York", "NY Credit", "active"),
    ])
    _run(engine)
    first = _statuses(engine)
    _run(engine)
    assert _statuses(engine) == first
