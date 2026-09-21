"""Reviewing recorded claims in bulk, without lowering the bar for any of them.

The workbook is a convenience for the reviewer and must not be a route around
the ledger. Two behaviours carry that: a blank decision is left alone, and a
self-signed independent QA is refused by identity when the decision is applied —
per row, so one bad signature does not discard a hundred sound ones.

The third group is about what the workbook tells a reviewer before they decide.
A claim that stages nothing because the next call is not announced is correct
research; a claim that stages nothing because its section name does not match is
not. Presenting those the same way would send correct work back.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import alembic
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from openpyxl import load_workbook

from app.modules.reports.verification_ledger import (
    GATE_FESTIVAL_SECTION,
    GATE_INCENTIVE_ENGINE,
    GATE_MARKET_CYCLE,
    GATE_MARKET_RULE,
    SourceClaim,
)
from scripts.build_review_workbook import (
    CONTEXT_COLUMNS,
    DECISION_COLUMNS,
    _engine_outcomes,
    apply_decisions,
    build,
)

ROOT = Path(__file__).resolve().parents[1]
LEDGER_MIGRATION = ROOT / "alembic/versions/u0v1w2x3y4z5_source_verifications.py"
TODAY = date(2026, 9, 20)
READ_ON = date(2026, 9, 18)
PAGE = "https://festival.example.org/submissions"


def _claim(gate: str, subject: str, field_name: str, value, **overrides) -> SourceClaim:
    base = {
        "gate": gate,
        "subject_id": subject,
        "field": field_name,
        "value": value,
        "source_url": PAGE,
        "source_basis": "The official page says so.",
        "verified_on": READ_ON,
        "verified_by": "alex",
        "review_state": "PENDING",
    }
    base.update(overrides)
    return SourceClaim(**base)


def _ledger(tmp_path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
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
    return engine


def _write(tmp_path, claims, decisions) -> Path:
    """Build the workbook, fill some decisions, and save it."""
    book = build(claims, today=TODAY)
    for sheet_name, row, values in decisions:
        page = book[sheet_name]
        for column, name in enumerate(
            [*CONTEXT_COLUMNS, *DECISION_COLUMNS], start=1
        ):
            if name in values:
                page.cell(row=row, column=column, value=values[name])
    path = tmp_path / "review.xlsx"
    book.save(path)
    return path


# ── What the workbook tells a reviewer ───────────────────────────────────────


def test_an_unannounced_call_is_recorded_and_says_it_is_not_actionable():
    """It is a correct answer, and it now stages a cycle rather than nothing.

    A reviewer must still be told the opportunity will not reach a report.
    "stages 1 cycle(s)" alone reads like it will.
    """
    claims = [_claim(GATE_MARKET_CYCLE, "mkt-1", "deadline", "NOT_ANNOUNCED")]
    outcome = _engine_outcomes(claims, today=TODAY)[(GATE_MARKET_CYCLE, "mkt-1")]
    assert "not actionable" in outcome
    assert "not announced" in outcome
    assert "needs rewriting" not in outcome


def test_a_rolling_call_is_recorded_and_actionable():
    """No deadline because it is always open. Locked decision D.4."""
    claims = [_claim(GATE_MARKET_CYCLE, "mkt-2", "deadline", "ROLLING")]
    outcome = _engine_outcomes(claims, today=TODAY)[(GATE_MARKET_CYCLE, "mkt-2")]
    assert "rolling" in outcome
    assert "not actionable" not in outcome


def test_a_section_name_that_matches_nothing_needs_rewriting():
    claims = [
        _claim(GATE_FESTIVAL_SECTION, "fest-1", "section_deadlines", "Competition | 2026-11-15"),
        _claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules", "Panorama | runtime_minutes at_most 40"),
    ]
    outcome = _engine_outcomes(claims, today=TODAY)[(GATE_FESTIVAL_SECTION, "fest-1")]
    assert outcome == "stages 1 cycle(s); 1 line(s) need rewriting"


def test_a_gate_the_bridge_does_not_read_says_so():
    claims = [_claim(GATE_INCENTIVE_ENGINE, "prog-1", "qs_engine_type", "QAPE")]
    outcome = _engine_outcomes(claims, today=TODAY)[(GATE_INCENTIVE_ENGINE, "prog-1")]
    assert outcome == "not a cycle gate"


def test_the_workbook_carries_the_claim_beside_the_decision(tmp_path):
    claims = [_claim(GATE_MARKET_RULE, "mkt-1", "hard_gates", "stage one_of development")]
    path = _write(tmp_path, claims, [])
    page = load_workbook(path)[GATE_MARKET_RULE]
    header = [c.value for c in page[2]]
    assert header == [*CONTEXT_COLUMNS, *DECISION_COLUMNS]
    row = {name: page.cell(row=3, column=n + 1).value for n, name in enumerate(header)}
    assert row["value"] == "stage one_of development"
    assert row["needs_second_reviewer"] == "YES"
    assert row["decision"] is None


# ── Applying decisions ───────────────────────────────────────────────────────


def _record(engine, *claims) -> None:
    from app.modules.reports import verification_store as store

    store.record_claims(engine, list(claims), apply=True, today=TODAY)


def _state(engine, subject: str, field_name: str) -> str:
    from app.modules.reports import verification_store as store

    for claim in store.load_claims(engine):
        if claim.subject_id == subject and claim.field == field_name:
            return claim.review_state
    raise AssertionError("claim not found")


def test_a_blank_decision_leaves_the_claim_alone(tmp_path):
    engine = _ledger(tmp_path)
    claim = _claim(GATE_INCENTIVE_ENGINE, "prog-1", "qs_engine_type", "QAPE")
    _record(engine, claim)
    path = _write(tmp_path, [claim], [])
    applied, refused = apply_decisions(engine, path, today=TODAY, apply=True)
    assert applied == [] and refused == []
    assert _state(engine, "prog-1", "qs_engine_type") == "PENDING"


def test_a_decision_with_no_reviewer_is_refused(tmp_path):
    engine = _ledger(tmp_path)
    claim = _claim(GATE_INCENTIVE_ENGINE, "prog-1", "qs_engine_type", "QAPE")
    _record(engine, claim)
    path = _write(tmp_path, [claim], [(GATE_INCENTIVE_ENGINE, 3, {"decision": "VERIFIED"})])
    _, refused = apply_decisions(engine, path, today=TODAY, apply=True)
    assert len(refused) == 1 and "no reviewer" in refused[0][1]
    assert _state(engine, "prog-1", "qs_engine_type") == "PENDING"


def test_a_named_reviewer_verifies_a_transcription_gate(tmp_path):
    engine = _ledger(tmp_path)
    claim = _claim(GATE_INCENTIVE_ENGINE, "prog-1", "qs_engine_type", "QAPE")
    _record(engine, claim)
    path = _write(
        tmp_path, [claim],
        [(GATE_INCENTIVE_ENGINE, 3, {"decision": "VERIFIED", "reviewed_by": "sam"})],
    )
    applied, refused = apply_decisions(engine, path, today=TODAY, apply=True)
    assert refused == [] and len(applied) == 1
    assert _state(engine, "prog-1", "qs_engine_type") == "VERIFIED"


def test_self_signed_independent_qa_is_refused_by_identity(tmp_path):
    engine = _ledger(tmp_path)
    claim = _claim(GATE_MARKET_RULE, "mkt-1", "hard_gates", "stage one_of development")
    _record(engine, claim)
    path = _write(
        tmp_path, [claim],
        [(GATE_MARKET_RULE, 3, {
            "decision": "VERIFIED", "reviewed_by": "alex", "qa_by": "alex",
        })],
    )
    _, refused = apply_decisions(engine, path, today=TODAY, apply=True)
    assert len(refused) == 1 and "same person" in refused[0][1]
    assert _state(engine, "mkt-1", "hard_gates") == "PENDING"


def test_one_bad_signature_does_not_discard_the_sound_decisions(tmp_path):
    engine = _ledger(tmp_path)
    good = _claim(GATE_INCENTIVE_ENGINE, "prog-1", "qs_engine_type", "QAPE")
    bad = _claim(GATE_MARKET_RULE, "mkt-1", "hard_gates", "stage one_of development")
    _record(engine, good, bad)
    path = _write(
        tmp_path, [good, bad],
        [
            (GATE_INCENTIVE_ENGINE, 3, {"decision": "VERIFIED", "reviewed_by": "sam"}),
            (GATE_MARKET_RULE, 3, {
                "decision": "VERIFIED", "reviewed_by": "alex", "qa_by": "alex",
            }),
        ],
    )
    applied, refused = apply_decisions(engine, path, today=TODAY, apply=True)
    assert len(applied) == 1 and len(refused) == 1
    assert _state(engine, "prog-1", "qs_engine_type") == "VERIFIED"
    assert _state(engine, "mkt-1", "hard_gates") == "PENDING"


def test_preflight_writes_nothing(tmp_path):
    engine = _ledger(tmp_path)
    claim = _claim(GATE_INCENTIVE_ENGINE, "prog-1", "qs_engine_type", "QAPE")
    _record(engine, claim)
    path = _write(
        tmp_path, [claim],
        [(GATE_INCENTIVE_ENGINE, 3, {"decision": "VERIFIED", "reviewed_by": "sam"})],
    )
    applied, _ = apply_decisions(engine, path, today=TODAY, apply=False)
    assert len(applied) == 1
    assert _state(engine, "prog-1", "qs_engine_type") == "PENDING"


@pytest.mark.parametrize("decision", ["yes", "APPROVE", "OK"])
def test_a_decision_outside_the_vocabulary_is_refused(tmp_path, decision):
    engine = _ledger(tmp_path)
    claim = _claim(GATE_INCENTIVE_ENGINE, "prog-1", "qs_engine_type", "QAPE")
    _record(engine, claim)
    path = _write(
        tmp_path, [claim],
        [(GATE_INCENTIVE_ENGINE, 3, {"decision": decision, "reviewed_by": "sam"})],
    )
    _, refused = apply_decisions(engine, path, today=TODAY, apply=True)
    assert len(refused) == 1
    assert _state(engine, "prog-1", "qs_engine_type") == "PENDING"


def test_rejecting_a_claim_keeps_it_out_of_every_engine(tmp_path):
    engine = _ledger(tmp_path)
    claim = _claim(GATE_INCENTIVE_ENGINE, "prog-1", "qs_engine_type", "not an engine name")
    _record(engine, claim)
    path = _write(
        tmp_path, [claim],
        [(GATE_INCENTIVE_ENGINE, 3, {"decision": "REJECTED", "reviewed_by": "sam"})],
    )
    applied, refused = apply_decisions(engine, path, today=TODAY, apply=True)
    assert refused == [] and len(applied) == 1
    assert _state(engine, "prog-1", "qs_engine_type") == "REJECTED"
