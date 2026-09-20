"""Verified engine classifications reaching the programmes that calculate on them.

One behaviour carries this file: it writes only where the column is empty. An
engine is a statutory classification, and the difference between
QUALIFIED_LABOUR and ELIGIBLE_LOCAL_SPEND is several million pounds on the same
production — so a verified claim that disagrees with a populated column is a
conflict between two people, reported and written nowhere.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import alembic
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.modules.reports.verification_ledger import GATE_INCENTIVE_ENGINE, SourceClaim
from scripts.apply_verified_incentive_engines import apply_verified_engines

ROOT = Path(__file__).resolve().parents[1]
LEDGER_MIGRATION = ROOT / "alembic/versions/u0v1w2x3y4z5_source_verifications.py"
TODAY = date(2026, 9, 20)
PAGE = "https://revenue.example.gov/film-relief"


def _database(tmp_path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'incentives.db'}")
    spec = importlib.util.spec_from_file_location("_ledger_up", LEDGER_MIGRATION)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        previous = alembic.op
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            module.upgrade()
        finally:
            alembic.op = previous
    # Only the three columns this script touches. The real table has fifty.
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE incentive_programs ("
            "id VARCHAR(64) PRIMARY KEY, program TEXT, qs_engine_type VARCHAR(40))"
        ))
    return engine


def _programme(engine, programme_id: str, name: str, current=None) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO incentive_programs (id, program, qs_engine_type) "
                "VALUES (:id, :program, :engine)"
            ),
            {"id": programme_id, "program": name, "engine": current},
        )


def _claim(engine, subject: str, value: str, *, verified: bool = True) -> None:
    from app.modules.reports import verification_store as store

    claim = SourceClaim(
        gate=GATE_INCENTIVE_ENGINE,
        subject_id=subject,
        field="qs_engine_type",
        value=value,
        source_url=PAGE,
        source_basis="The statute names the expenditure the rate applies to.",
        verified_on=date(2026, 9, 18),
        verified_by="researcher",
    )
    store.record_claims(engine, [claim], apply=True, today=TODAY)
    if verified:
        store.review(
            engine, gate=GATE_INCENTIVE_ENGINE, subject_id=subject,
            field_name="qs_engine_type", reviewer="reviewer", today=TODAY,
        )


def _engine_of(engine, programme_id: str):
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT qs_engine_type FROM incentive_programs WHERE id = :id"),
            {"id": programme_id},
        ).scalar()


def test_a_verified_claim_classifies_an_unclassified_programme(tmp_path):
    db = _database(tmp_path)
    _programme(db, "prog-1", "Example Film Relief")
    _claim(db, "prog-1", "CORE_LOWER_OF")
    result = apply_verified_engines(db, apply=True, today=TODAY)
    assert len(result.written) == 1 and result.is_clean
    assert _engine_of(db, "prog-1") == "CORE_LOWER_OF"


def test_a_pending_claim_classifies_nothing(tmp_path):
    db = _database(tmp_path)
    _programme(db, "prog-1", "Example Film Relief")
    _claim(db, "prog-1", "CORE_LOWER_OF", verified=False)
    result = apply_verified_engines(db, apply=True, today=TODAY)
    assert result.verified_claims == 0 and result.written == []
    assert _engine_of(db, "prog-1") is None


def test_a_populated_column_is_never_overwritten(tmp_path):
    """Several million pounds on the same production, changed by a spreadsheet."""
    db = _database(tmp_path)
    _programme(db, "prog-1", "Example Film Relief", current="QUALIFIED_LABOUR")
    _claim(db, "prog-1", "ELIGIBLE_LOCAL_SPEND")
    result = apply_verified_engines(db, apply=True, today=TODAY)
    assert result.written == []
    assert result.conflicts == [
        ("prog-1", "Example Film Relief", "QUALIFIED_LABOUR", "ELIGIBLE_LOCAL_SPEND")
    ]
    assert not result.is_clean
    assert _engine_of(db, "prog-1") == "QUALIFIED_LABOUR"


def test_agreeing_with_a_populated_column_is_not_a_conflict(tmp_path):
    db = _database(tmp_path)
    _programme(db, "prog-1", "Example Film Relief", current="qape")
    _claim(db, "prog-1", "QAPE")
    result = apply_verified_engines(db, apply=True, today=TODAY)
    assert (result.unchanged, result.conflicts, result.written) == (1, [], [])


def test_a_value_outside_the_twelve_engines_is_refused(tmp_path):
    db = _database(tmp_path)
    _programme(db, "prog-1", "Example Film Relief")
    _claim(db, "prog-1", "GENEROUS_REBATE")
    result = apply_verified_engines(db, apply=True, today=TODAY)
    assert result.unknown_values == [("prog-1", "GENEROUS_REBATE")]
    assert _engine_of(db, "prog-1") is None


def test_a_claim_naming_no_programme_is_reported(tmp_path):
    db = _database(tmp_path)
    _claim(db, "prog-missing", "QAPE")
    result = apply_verified_engines(db, apply=True, today=TODAY)
    assert result.missing_programmes == ["prog-missing"]
    assert not result.is_clean


def test_preflight_writes_nothing(tmp_path):
    db = _database(tmp_path)
    _programme(db, "prog-1", "Example Film Relief")
    _claim(db, "prog-1", "CORE_LOWER_OF")
    result = apply_verified_engines(db, apply=False, today=TODAY)
    assert len(result.written) == 1
    assert _engine_of(db, "prog-1") is None


def test_rerunning_changes_nothing(tmp_path):
    db = _database(tmp_path)
    _programme(db, "prog-1", "Example Film Relief")
    _claim(db, "prog-1", "CORE_LOWER_OF")
    apply_verified_engines(db, apply=True, today=TODAY)
    again = apply_verified_engines(db, apply=True, today=TODAY)
    assert (again.written, again.unchanged, again.conflicts) == ([], 1, [])


def test_a_classified_programme_can_reach_the_statutory_calculator(tmp_path):
    """The point of the whole script, asserted rather than assumed."""
    from app.modules.reports.statutory_calculation import engine_of

    db = _database(tmp_path)
    _programme(db, "prog-1", "Example Film Relief")
    _claim(db, "prog-1", "ELIGIBLE_LOCAL_SPEND")
    apply_verified_engines(db, apply=True, today=TODAY)
    with db.connect() as conn:
        row = dict(conn.execute(
            sa.text("SELECT * FROM incentive_programs WHERE id = 'prog-1'")
        ).mappings().one())
    assert engine_of(row) == "ELIGIBLE_LOCAL_SPEND"
