"""The ledger every v2 source verification passes through.

What is under test is mostly refusal. A claim with no source is not a
verification, a claim citing our own data cannot verify itself, and a market
rule signed off by the person who wrote it has not been independently reviewed.
None of these can be forced through, because the check lives in the loader
rather than in a policy someone in a hurry can skip.
"""
from __future__ import annotations

from datetime import date

from app.modules.reports.verification_ledger import (
    GATE_FESTIVAL_SECTION,
    GATE_INCENTIVE_ENGINE,
    GATE_MARKET_CYCLE,
    GATE_MARKET_RULE,
    PENDING,
    REJECTED,
    VERIFIED,
    SourceClaim,
    gate_progress,
    readable_claims,
    validate_claim,
)

TODAY = date(2026, 9, 18)


def _claim(**overrides) -> SourceClaim:
    base = {
        "gate": GATE_INCENTIVE_ENGINE,
        "subject_id": "uk-avec",
        "field": "qs_engine_type",
        "value": "CORE_LOWER_OF",
        "source_url": "https://www.gov.uk/guidance/audio-visual-expenditure-credit",
        "source_basis": "Qualifying expenditure is the lower of UK core expenditure and 80% of total core expenditure.",
        "verified_on": date(2026, 9, 17),
        "verified_by": "researcher-a",
        "review_state": VERIFIED,
        "reviewed_by": "reviewer-b",
    }
    base.update(overrides)
    return SourceClaim(**base)


# ── What a verification must carry ───────────────────────────────────────────


def test_a_complete_claim_stands():
    assert validate_claim(_claim(), today=TODAY) == ()


def test_a_claim_with_no_source_is_not_a_verification():
    problems = validate_claim(_claim(source_url=""), today=TODAY)
    assert any("opinion with a date on it" in p for p in problems)


def test_a_claim_citing_our_own_data_cannot_verify_itself():
    """The bulk-classification trap: deriving the new field from the old one."""
    problems = validate_claim(
        _claim(source_url="https://prodculator.com/admin/incentives/uk-avec"),
        today=TODAY,
    )
    assert any("cannot verify itself" in p for p in problems)


def test_a_localhost_source_is_circular_too():
    problems = validate_claim(
        _claim(source_url="https://localhost:5432/incentive_programs"), today=TODAY
    )
    assert any("cannot verify itself" in p for p in problems)


def test_a_claim_must_record_what_the_source_says():
    problems = validate_claim(_claim(source_basis="  "), today=TODAY)
    assert any("what the source actually says" in p for p in problems)


def test_a_claim_verified_in_the_future_is_not_evidence():
    problems = validate_claim(_claim(verified_on=date(2026, 12, 1)), today=TODAY)
    assert any("verified in the future" in p for p in problems)


def test_a_claim_verified_today_is_fine():
    assert validate_claim(_claim(verified_on=TODAY), today=TODAY) == ()


def test_an_unknown_gate_is_rejected():
    problems = validate_claim(_claim(gate="VIBES"), today=TODAY)
    assert any("not a known verification gate" in p for p in problems)


def test_every_problem_is_reported_not_just_the_first():
    """A researcher fixing one should not meet the next on the following run."""
    problems = validate_claim(
        _claim(source_url="", source_basis="", verified_by=""), today=TODAY
    )
    assert len(problems) >= 3


# ── Independent QA ───────────────────────────────────────────────────────────


def test_a_market_rule_needs_a_second_reviewer():
    """Turning prose into a typed rule is interpretation, not transcription."""
    claim = _claim(gate=GATE_MARKET_RULE, field="stage_gate")
    assert claim.needs_independent_qa
    problems = validate_claim(claim, today=TODAY)
    assert any("requires independent QA" in p for p in problems)


def test_self_signed_qa_is_rejected_by_identity():
    claim = _claim(
        gate=GATE_MARKET_RULE,
        field="stage_gate",
        verified_by="researcher-a",
        qa_by="researcher-a",
    )
    problems = validate_claim(claim, today=TODAY)
    assert any("same person who made the claim" in p for p in problems)


def test_a_genuinely_independent_qa_passes():
    claim = _claim(
        gate=GATE_MARKET_RULE,
        field="stage_gate",
        verified_by="researcher-a",
        qa_by="researcher-b",
    )
    assert validate_claim(claim, today=TODAY) == ()


def test_a_festival_section_also_needs_independent_qa():
    assert _claim(gate=GATE_FESTIVAL_SECTION).needs_independent_qa


def test_reading_a_published_deadline_does_not_need_a_second_reviewer():
    """Transcription is not interpretation; requiring QA everywhere is noise."""
    claim = _claim(gate=GATE_MARKET_CYCLE, field="deadline")
    assert not claim.needs_independent_qa
    assert validate_claim(claim, today=TODAY) == ()


def test_a_caller_cannot_lower_the_bar_by_omitting_the_flag():
    claim = _claim(gate=GATE_MARKET_RULE, requires_independent_qa=None)
    assert claim.needs_independent_qa


# ── What an engine may read ──────────────────────────────────────────────────


def test_a_pending_claim_is_not_readable():
    assert readable_claims([_claim(review_state=PENDING)], today=TODAY) == ()


def test_a_rejected_claim_is_not_readable():
    assert readable_claims([_claim(review_state=REJECTED)], today=TODAY) == ()


def test_a_verified_claim_with_no_reviewer_is_not_readable():
    assert readable_claims([_claim(reviewed_by=None)], today=TODAY) == ()


def test_an_invalid_verified_claim_is_withheld_rather_than_trusted():
    """A bad claim marked VERIFIED still does not reach an engine."""
    assert readable_claims([_claim(source_url="")], today=TODAY) == ()


def test_a_good_claim_is_readable():
    assert len(readable_claims([_claim()], today=TODAY)) == 1


# ── Gate progress ────────────────────────────────────────────────────────────


def test_progress_counts_distinct_subjects_not_claims():
    """Researching one record in more detail does not advance the gate."""
    claims = [
        _claim(field="qs_engine_type"),
        _claim(field="qualifying_spend_cap_pct"),
    ]
    progress = gate_progress(
        GATE_INCENTIVE_ENGINE, ["uk-avec", "fr-trip"], claims, today=TODAY
    )
    assert progress.required == 2
    assert progress.verified == 1
    assert progress.outstanding == 1
    assert not progress.is_closed


def test_a_gate_closes_when_every_subject_is_verified():
    claims = [_claim(subject_id="uk-avec"), _claim(subject_id="fr-trip")]
    progress = gate_progress(
        GATE_INCENTIVE_ENGINE, ["uk-avec", "fr-trip"], claims, today=TODAY
    )
    assert progress.is_closed
    assert progress.outstanding == 0


def test_a_claim_for_a_subject_nobody_required_does_not_advance_the_gate():
    claims = [_claim(subject_id="something-else")]
    progress = gate_progress(GATE_INCENTIVE_ENGINE, ["uk-avec"], claims, today=TODAY)
    assert progress.verified == 0


def test_an_empty_gate_is_not_closed():
    """Nothing required is not the same as everything done."""
    assert not gate_progress(GATE_INCENTIVE_ENGINE, [], [], today=TODAY).is_closed


# ── The ledger table ─────────────────────────────────────────────────────────


def _run_migration(engine, direction):
    import importlib.util
    from pathlib import Path

    import alembic
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/u0v1w2x3y4z5_source_verifications.py"
    )
    spec = importlib.util.spec_from_file_location(f"_ledger_{direction}", migration)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        previous = alembic.op
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            getattr(module, direction)()
        finally:
            alembic.op = previous


def test_the_ledger_table_is_additive_and_replayable():
    import sqlalchemy as sa

    engine = sa.create_engine("sqlite://")
    _run_migration(engine, "upgrade")
    _run_migration(engine, "upgrade")  # replay must not fail
    assert "source_verifications" in sa.inspect(engine).get_table_names()


def test_the_ledger_refuses_to_be_dropped_while_it_holds_research():
    import sqlalchemy as sa
    import pytest

    engine = sa.create_engine("sqlite://")
    _run_migration(engine, "upgrade")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO source_verifications "
                "(id, gate, subject_id, field, source_url, source_basis, "
                " verified_on, verified_by, review_state, requires_independent_qa) "
                "VALUES ('1', 'MARKET_CYCLE', 'x', 'deadline', 'https://e.org', "
                "'says so', '2026-09-17', 'a', 'VERIFIED', 0)"
            )
        )
    with pytest.raises(RuntimeError, match="recorded verifications"):
        _run_migration(engine, "downgrade")


def test_one_current_claim_per_field_of_a_subject():
    """A correction replaces its predecessor rather than competing with it."""
    import sqlalchemy as sa

    engine = sa.create_engine("sqlite://")
    _run_migration(engine, "upgrade")
    insert = (
        "INSERT INTO source_verifications "
        "(id, gate, subject_id, field, source_url, source_basis, verified_on, "
        " verified_by, review_state, requires_independent_qa) "
        "VALUES (:id, 'MARKET_CYCLE', 'x', 'deadline', 'https://e.org', 'says so', "
        "'2026-09-17', 'a', 'PENDING', 0)"
    )
    with engine.begin() as conn:
        conn.execute(sa.text(insert), {"id": "1"})
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(insert), {"id": "2"})
    except sa.exc.IntegrityError:
        pass
    else:  # pragma: no cover - the constraint is the point of the test
        raise AssertionError("A second claim for the same field must not stand")
