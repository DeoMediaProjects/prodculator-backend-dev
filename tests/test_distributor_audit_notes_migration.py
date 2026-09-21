"""a9b8c7d6e5f4 moves distributor audit annotations out of client-facing prose.

PROD-FIX-006 one table over. `g7b8c9d0e1f2` did this for `incentive_programs`
and gave them `internal_audit_notes`; `distributors` had no such column, so the
report's last-line guard has been firing on every generation:

    PROD-FIX-006 guard fired: internal audit annotation reached client-bound
    report output at 1 location(s):
    report.distributorRecommendations[2].submissionProcess

Same harness as test_australia_producer_offset_split_migration.py: the full
chain cannot run on SQLite, so this exercises just this revision against a stub
table.
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
_MIGRATION = _VERSIONS / "a9b8c7d6e5f4_distributor_audit_notes.py"

_ANNOTATION = (
    "[FLAGGED 2026-08: submission route taken from a 2023 interview, needs "
    "direct confirmation from the acquisitions desk before publishing.]"
)
_CLEAN = "Primarily via sales agents/festivals, not blind submission"


def _stub_table(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "distributors", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("submission_process", sa.Text()),
        sa.Column("notes", sa.Text()),
    )
    metadata.create_all(engine)


def _run(engine, direction: str):
    # `from alembic import op` binds at load time, so each direction needs its
    # own spec/module/exec — same constraint the Australia test documents.
    spec = importlib.util.spec_from_file_location(
        f"_run_a9b8c7d6e5f4_{direction}", _MIGRATION
    )
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


def _seed(engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                sa.text(
                    "INSERT INTO distributors (id, name, submission_process, notes) "
                    "VALUES (:id, :name, :submission_process, :notes)"
                ),
                {"notes": None, "submission_process": None, **row},
            )


def _fetch(engine, name: str) -> dict:
    with engine.begin() as conn:
        return dict(
            conn.execute(
                sa.text(
                    "SELECT submission_process, notes, internal_audit_notes "
                    "FROM distributors WHERE name = :name"
                ),
                {"name": name},
            ).mappings().one()
        )


@pytest.fixture()
def engine():
    eng = sa.create_engine("sqlite://")
    _stub_table(eng)
    return eng


def test_the_annotation_leaves_the_client_facing_column(engine):
    _seed(engine, [{
        "id": "d1",
        "name": "Dark Sky Films",
        "submission_process": f"{_CLEAN} {_ANNOTATION}",
    }])
    _run(engine, "upgrade")
    row = _fetch(engine, "Dark Sky Films")
    assert _ANNOTATION not in (row["submission_process"] or "")
    assert row["submission_process"].strip() == _CLEAN


def test_the_annotation_is_kept_not_discarded(engine):
    # The data team wrote it for a reason. It moves out of the report's reach;
    # it does not get deleted.
    _seed(engine, [{
        "id": "d1",
        "name": "Dark Sky Films",
        "submission_process": f"{_CLEAN} {_ANNOTATION}",
    }])
    _run(engine, "upgrade")
    kept = _fetch(engine, "Dark Sky Films")["internal_audit_notes"]
    assert _ANNOTATION in kept
    # Labelled with the column it came off, so the team can see what it was
    # attached to.
    assert kept.startswith("[submission_process]")


def test_notes_are_scrubbed_too(engine):
    # Not read by the report today. Included for the same reason the incentive
    # migration went wider than the columns then in use.
    _seed(engine, [{
        "id": "d2", "name": "Neon", "notes": f"Horror specialist. {_ANNOTATION}",
    }])
    _run(engine, "upgrade")
    row = _fetch(engine, "Neon")
    assert _ANNOTATION not in (row["notes"] or "")
    assert "[notes]" in row["internal_audit_notes"]


def test_a_clean_record_is_left_alone(engine):
    _seed(engine, [{
        "id": "d3", "name": "A24", "submission_process": _CLEAN, "notes": "Verified.",
    }])
    _run(engine, "upgrade")
    row = _fetch(engine, "A24")
    assert row["submission_process"] == _CLEAN
    assert row["notes"] == "Verified."
    assert row["internal_audit_notes"] is None


def test_the_guard_would_no_longer_fire(engine):
    # The detector the report boundary uses, applied to what the migration
    # leaves behind. This is the actual acceptance criterion: the ERROR stops.
    from app.core.audit_notes import contains_audit_text

    _seed(engine, [
        {"id": "d1", "name": "Dark Sky Films",
         "submission_process": f"{_CLEAN} {_ANNOTATION}"},
        {"id": "d2", "name": "Neon", "notes": f"Horror. {_ANNOTATION}"},
        {"id": "d3", "name": "A24", "submission_process": _CLEAN},
    ])
    _run(engine, "upgrade")
    with engine.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT submission_process, notes FROM distributors")
        ).mappings().all()
    for row in rows:
        for value in (row["submission_process"], row["notes"]):
            assert not (value and contains_audit_text(value))


def test_downgrade_returns_the_text_rather_than_losing_it(engine):
    _seed(engine, [{
        "id": "d1", "name": "Dark Sky Films",
        "submission_process": f"{_CLEAN} {_ANNOTATION}", "notes": "Horror.",
    }])
    _run(engine, "upgrade")
    _run(engine, "downgrade")
    with engine.begin() as conn:
        notes = conn.execute(
            sa.text("SELECT notes FROM distributors WHERE name = 'Dark Sky Films'")
        ).scalar()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("distributors")}
    # The inline position is not recoverable; the content is.
    assert _ANNOTATION in notes
    assert "internal_audit_notes" not in columns
