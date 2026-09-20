"""Correcting a recorded claim, and what the correction costs.

Two behaviours carry this file. A correction replaces the recorded reading
rather than sitting beside it — the unique constraint leaves no third option and
the ledger migration says so in its own words — and the previous reading is kept
in the claim's notes so it does not vanish.

And a corrected claim goes back to PENDING with no reviewer and no QA
signature. That is the point rather than a side effect: the people who signed
the old reading did not sign the new words.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import alembic
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.modules.reports import verification_store as store
from app.modules.reports.verification_ledger import (
    GATE_FESTIVAL_SECTION,
    GATE_MARKET_RULE,
    SourceClaim,
)
from app.modules.reports.verified_cycles import build_cycles
from scripts.build_correction_workbook import (
    CONTEXT_COLUMNS,
    CORRECTION_COLUMNS,
    build,
    needs_correction,
    read_corrections,
)

ROOT = Path(__file__).resolve().parents[1]
LEDGER_MIGRATION = ROOT / "alembic/versions/u0v1w2x3y4z5_source_verifications.py"
TODAY = date(2026, 9, 20)
READ_ON = date(2026, 9, 18)
PAGE = "https://festival.example.org/submissions"


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


def _claim(gate, subject, field_name, value, **overrides) -> SourceClaim:
    base = {
        "gate": gate,
        "subject_id": subject,
        "field": field_name,
        "value": value,
        "source_url": PAGE,
        "source_basis": "The official page says so.",
        "verified_on": READ_ON,
        "verified_by": "alex",
    }
    base.update(overrides)
    return SourceClaim(**base)


def _recorded(engine, subject: str, field_name: str):
    for claim in store.load_claims(engine):
        if claim.subject_id == subject and claim.field == field_name:
            return claim
    raise AssertionError("claim not found")


# ── Superseding ──────────────────────────────────────────────────────────────


def test_a_correction_replaces_the_recorded_reading(tmp_path):
    engine = _ledger(tmp_path)
    original = _claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules", "Competition | x")
    store.record_claims(engine, [original], apply=True, today=TODAY)

    corrected = _claim(
        GATE_FESTIVAL_SECTION, "fest-1", "section_rules",
        "Feature Competition | runtime_minutes at_least 60",
        verified_by="sam", verified_on=date(2026, 9, 20),
    )
    result = store.supersede_claims(engine, [corrected], apply=True, today=TODAY)
    assert len(result.replaced) == 1 and result.is_clean
    assert _recorded(engine, "fest-1", "section_rules").value == (
        "Feature Competition | runtime_minutes at_least 60"
    )


def test_the_previous_reading_survives_in_the_notes(tmp_path):
    engine = _ledger(tmp_path)
    store.record_claims(
        engine,
        [_claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules", "Competition | x")],
        apply=True, today=TODAY,
    )
    store.supersede_claims(
        engine,
        [_claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules", "Feature Competition | y",
                verified_by="sam")],
        apply=True, today=TODAY,
    )
    notes = _recorded(engine, "fest-1", "section_rules").notes or ""
    assert "Competition | x" in notes
    assert "Superseded" in notes and "sam" in notes


def test_a_correction_loses_its_reviewer_and_qa_signature(tmp_path):
    """The people who signed the old reading did not sign the new words."""
    engine = _ledger(tmp_path)
    original = _claim(
        GATE_MARKET_RULE, "mkt-1", "hard_gates", "stage one_of develpoment"
    )
    store.record_claims(engine, [original], apply=True, today=TODAY)
    store.review(
        engine, gate=GATE_MARKET_RULE, subject_id="mkt-1", field_name="hard_gates",
        reviewer="reviewer-b", qa_by="reviewer-c", today=TODAY,
    )
    assert _recorded(engine, "mkt-1", "hard_gates").review_state == "VERIFIED"

    store.supersede_claims(
        engine,
        [_claim(GATE_MARKET_RULE, "mkt-1", "hard_gates", "stage one_of development",
                verified_by="sam")],
        apply=True, today=TODAY,
    )
    fixed = _recorded(engine, "mkt-1", "hard_gates")
    assert fixed.review_state == "PENDING"
    assert fixed.reviewed_by is None and fixed.qa_by is None
    assert store.readable_values(engine, GATE_MARKET_RULE, today=TODAY) == {}


def test_an_identical_correction_is_not_a_replacement(tmp_path):
    engine = _ledger(tmp_path)
    original = _claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules", "A | b")
    store.record_claims(engine, [original], apply=True, today=TODAY)
    result = store.supersede_claims(engine, [original], apply=True, today=TODAY)
    assert (result.unchanged, result.replaced) == (1, [])


def test_a_correction_for_a_claim_nobody_recorded_is_not_inserted(tmp_path):
    """A typo in a subject id must fail, not create a claim."""
    engine = _ledger(tmp_path)
    result = store.supersede_claims(
        engine,
        [_claim(GATE_FESTIVAL_SECTION, "fest-typo", "section_rules", "A | b")],
        apply=True, today=TODAY,
    )
    assert len(result.missing) == 1 and result.replaced == []
    assert store.load_claims(engine) == []


def test_a_correction_with_no_source_is_refused(tmp_path):
    engine = _ledger(tmp_path)
    store.record_claims(
        engine, [_claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules", "A | b")],
        apply=True, today=TODAY,
    )
    result = store.supersede_claims(
        engine,
        [_claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules", "C | d", source_url="")],
        apply=True, today=TODAY,
    )
    assert len(result.refused) == 1 and not result.is_clean
    assert _recorded(engine, "fest-1", "section_rules").value == "A | b"


def test_a_preflight_replaces_nothing(tmp_path):
    engine = _ledger(tmp_path)
    store.record_claims(
        engine, [_claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules", "A | b")],
        apply=True, today=TODAY,
    )
    result = store.supersede_claims(
        engine,
        [_claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules", "C | d")],
        apply=False, today=TODAY,
    )
    assert len(result.replaced) == 1
    assert _recorded(engine, "fest-1", "section_rules").value == "A | b"


# ── The workbook that finds them ─────────────────────────────────────────────


def _with_mismatch(engine) -> None:
    store.record_claims(
        engine,
        [
            _claim(GATE_FESTIVAL_SECTION, "fest-1", "section_deadlines",
                   "Feature Competition | 2026-11-15"),
            _claim(GATE_FESTIVAL_SECTION, "fest-1", "section_rules",
                   "Competition | runtime_minutes at_least 60"),
        ],
        apply=True, today=TODAY,
    )


def test_only_the_claim_at_fault_is_listed(tmp_path):
    """The deadline claim is blameless; sending it back would rewrite good work."""
    engine = _ledger(tmp_path)
    _with_mismatch(engine)
    rows = needs_correction(engine, today=TODAY)
    assert [(c.subject_id, c.field) for c, _, _, _ in rows] == [
        ("fest-1", "section_rules")
    ]


def test_the_row_names_the_sections_that_do_carry_a_date(tmp_path):
    engine = _ledger(tmp_path)
    _with_mismatch(engine)
    _, trouble, sections, _ = needs_correction(engine, today=TODAY)[0]
    assert sections == ["Feature Competition"]
    assert "has no verified deadline to attach to" in trouble[0]


def test_both_problems_on_one_line_are_reported_together():
    """One trip, not two: rename the section AND fix the field."""
    claims = [
        SourceClaim(
            gate=GATE_FESTIVAL_SECTION, subject_id="fest-1", field="section_deadlines",
            value="Feature Competition | 2026-11-15", source_url=PAGE,
            source_basis="x", verified_on=READ_ON, verified_by="alex",
            review_state="VERIFIED", reviewed_by="r", qa_by="q",
        ),
        SourceClaim(
            gate=GATE_FESTIVAL_SECTION, subject_id="fest-1", field="section_rules",
            value="Competition | georgia_premiere equals required", source_url=PAGE,
            source_basis="x", verified_on=READ_ON, verified_by="alex",
            review_state="VERIFIED", reviewed_by="r", qa_by="q",
        ),
    ]
    _, problems = build_cycles(claims, today=TODAY)
    reason = next(p.reason for p in problems if p.field == "section_rules")
    assert "has no verified deadline" in reason
    assert "not a Project DNA field" in reason


def test_a_settled_answer_needs_no_correction(tmp_path):
    """NOT_ANNOUNCED is the finding the research asked for."""
    engine = _ledger(tmp_path)
    store.record_claims(
        engine,
        [_claim("MARKET_CYCLE", "mkt-1", "deadline", "NOT_ANNOUNCED")],
        apply=True, today=TODAY,
    )
    assert needs_correction(engine, today=TODAY) == []


def test_a_rejected_claim_is_not_offered_for_correction(tmp_path):
    engine = _ledger(tmp_path)
    _with_mismatch(engine)
    store.review(
        engine, gate=GATE_FESTIVAL_SECTION, subject_id="fest-1",
        field_name="section_rules", reviewer="sam", state="REJECTED", today=TODAY,
    )
    assert needs_correction(engine, today=TODAY) == []


def test_the_workbook_round_trips(tmp_path):
    engine = _ledger(tmp_path)
    _with_mismatch(engine)
    book = build(needs_correction(engine, today=TODAY))
    page = book[GATE_FESTIVAL_SECTION]
    assert [c.value for c in page[2]] == [*CONTEXT_COLUMNS, *CORRECTION_COLUMNS]

    headers = [*CONTEXT_COLUMNS, *CORRECTION_COLUMNS]
    filled = {
        "corrected_value": "Feature Competition | runtime_minutes at_least 60",
        "verified_by": "sam",
        "verified_on": "2026-09-20",
        "source_url": PAGE,
        "source_basis": "The rules page lists the section under that name.",
    }
    for column, name in enumerate(headers, start=1):
        if name in filled:
            page.cell(row=3, column=column, value=filled[name])
    path = tmp_path / "fixes.xlsx"
    book.save(path)

    claims, unreadable = read_corrections(path)
    assert unreadable == [] and len(claims) == 1
    result = store.supersede_claims(engine, claims, apply=True, today=TODAY)
    assert len(result.replaced) == 1 and result.is_clean
    assert needs_correction(engine, today=TODAY) == []


def test_a_blank_row_corrects_nothing(tmp_path):
    engine = _ledger(tmp_path)
    _with_mismatch(engine)
    path = tmp_path / "untouched.xlsx"
    build(needs_correction(engine, today=TODAY)).save(path)
    claims, unreadable = read_corrections(path)
    assert claims == [] and unreadable == []
