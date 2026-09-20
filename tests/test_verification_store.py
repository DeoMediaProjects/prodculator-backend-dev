"""The ledger's read and write path.

Between the contract and the table there was nothing, so the harness was a
contract with no traffic. What these tests guard is that adding the traffic did
not add a way around the refusals: recording is still not reviewing, a
correction is still a conflict rather than an overwrite, and a claim that fails
validation is still unreadable however it got written.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.modules.reports import verification_store as store
from app.modules.reports.verification_ledger import (
    GATE_INCENTIVE_ENGINE,
    GATE_MARKET_RULE,
    PENDING,
    REJECTED,
    VERIFIED,
    SourceClaim,
)

TODAY = date(2026, 9, 20)
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/u0v1w2x3y4z5_source_verifications.py"
)


@pytest.fixture()
def engine():
    import alembic
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    db = sa.create_engine("sqlite://")
    spec = importlib.util.spec_from_file_location("_ledger_up", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    with db.begin() as conn:
        previous = alembic.op
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            module.upgrade()
        finally:
            alembic.op = previous
    return db


def claim(**overrides) -> SourceClaim:
    base = {
        "gate": GATE_INCENTIVE_ENGINE,
        "subject_id": "uk-avec",
        "field": "qs_engine_type",
        "value": "CORE_LOWER_OF",
        "source_url": "https://www.gov.uk/guidance/avec",
        "source_basis": "Qualifying expenditure is the lower of UK core and 80% of global core.",
        "verified_on": date(2026, 9, 19),
        "verified_by": "researcher-a",
    }
    base.update(overrides)
    return SourceClaim(**base)


# ── Recording ────────────────────────────────────────────────────────────────


def test_a_preflight_writes_nothing(engine):
    result = store.record_claims(engine, [claim()], today=TODAY)
    assert result.inserted == 1
    assert not result.applied
    assert store.load_claims(engine) == []


def test_apply_inserts_the_claim(engine):
    store.record_claims(engine, [claim()], apply=True, today=TODAY)
    recorded = store.load_claims(engine)
    assert len(recorded) == 1
    assert recorded[0].subject_id == "uk-avec"


def test_a_recorded_claim_arrives_pending_however_confident_its_author(engine):
    """Writing something down is not reviewing it."""
    store.record_claims(
        engine, [claim(review_state=VERIFIED, reviewed_by="researcher-a")],
        apply=True, today=TODAY,
    )
    assert store.load_claims(engine)[0].review_state == PENDING


def test_replaying_the_same_claim_changes_nothing(engine):
    store.record_claims(engine, [claim()], apply=True, today=TODAY)
    again = store.record_claims(engine, [claim()], apply=True, today=TODAY)
    assert again.inserted == 0
    assert again.unchanged == 1
    assert len(store.load_claims(engine)) == 1


def test_a_changed_value_is_a_conflict_not_an_overwrite(engine):
    """A correction that silently replaces loses the earlier reading."""
    store.record_claims(engine, [claim()], apply=True, today=TODAY)
    result = store.record_claims(
        engine, [claim(value="ELIGIBLE_LOCAL_SPEND")], apply=True, today=TODAY
    )
    assert result.conflicts
    recorded_claim, existing = result.conflicts[0]
    assert existing == "CORE_LOWER_OF"
    assert recorded_claim.value == "ELIGIBLE_LOCAL_SPEND"
    assert store.load_claims(engine)[0].value == "CORE_LOWER_OF"
    assert not result.is_clean


def test_an_invalid_claim_is_rejected_with_every_reason(engine):
    result = store.record_claims(
        engine, [claim(source_url="", source_basis="")], today=TODAY
    )
    assert result.inserted == 0
    _, problems = result.rejected[0]
    assert len(problems) >= 2


def test_a_circular_source_is_rejected_at_the_door(engine):
    result = store.record_claims(
        engine,
        [claim(source_url="https://prodculator.com/admin/incentives/uk-avec")],
        today=TODAY,
    )
    assert result.rejected
    assert any("cannot verify itself" in p for _, ps in result.rejected for p in ps)


def test_a_duplicate_inside_one_batch_is_only_inserted_once(engine):
    result = store.record_claims(engine, [claim(), claim()], apply=True, today=TODAY)
    assert result.inserted == 1
    assert len(store.load_claims(engine)) == 1


# ── Reviewing ────────────────────────────────────────────────────────────────


def _record(engine, **overrides):
    store.record_claims(engine, [claim(**overrides)], apply=True, today=TODAY)


def test_a_review_makes_a_claim_readable(engine):
    _record(engine)
    store.review(
        engine,
        gate=GATE_INCENTIVE_ENGINE,
        subject_id="uk-avec",
        field_name="qs_engine_type",
        reviewer="reviewer-b",
        today=TODAY,
    )
    assert store.readable_values(engine, GATE_INCENTIVE_ENGINE, today=TODAY) == {
        "uk-avec": {"qs_engine_type": "CORE_LOWER_OF"}
    }


def test_an_unreviewed_claim_is_not_readable(engine):
    _record(engine)
    assert store.readable_values(engine, GATE_INCENTIVE_ENGINE, today=TODAY) == {}


def test_a_rejected_claim_is_not_readable(engine):
    _record(engine)
    store.review(
        engine,
        gate=GATE_INCENTIVE_ENGINE,
        subject_id="uk-avec",
        field_name="qs_engine_type",
        reviewer="reviewer-b",
        state=REJECTED,
        today=TODAY,
    )
    assert store.readable_values(engine, GATE_INCENTIVE_ENGINE, today=TODAY) == {}
    assert store.rejected_count(engine) == 1


def test_a_market_rule_cannot_be_verified_without_independent_qa(engine):
    _record(engine, gate=GATE_MARKET_RULE, field="stage_gate")
    with pytest.raises(ValueError, match="requires independent QA"):
        store.review(
            engine,
            gate=GATE_MARKET_RULE,
            subject_id="uk-avec",
            field_name="stage_gate",
            reviewer="reviewer-b",
            today=TODAY,
        )


def test_a_market_rule_cannot_be_qa_signed_by_its_author(engine):
    _record(engine, gate=GATE_MARKET_RULE, field="stage_gate")
    with pytest.raises(ValueError, match="same person who made the claim"):
        store.review(
            engine,
            gate=GATE_MARKET_RULE,
            subject_id="uk-avec",
            field_name="stage_gate",
            reviewer="reviewer-b",
            qa_by="researcher-a",
            today=TODAY,
        )


def test_independent_qa_lets_a_market_rule_through(engine):
    _record(engine, gate=GATE_MARKET_RULE, field="stage_gate")
    store.review(
        engine,
        gate=GATE_MARKET_RULE,
        subject_id="uk-avec",
        field_name="stage_gate",
        reviewer="reviewer-b",
        qa_by="reviewer-c",
        today=TODAY,
    )
    assert store.readable_values(engine, GATE_MARKET_RULE, today=TODAY)


def test_reviewing_a_claim_nobody_recorded_is_an_error(engine):
    with pytest.raises(LookupError):
        store.review(
            engine,
            gate=GATE_INCENTIVE_ENGINE,
            subject_id="nothing",
            field_name="qs_engine_type",
            reviewer="reviewer-b",
            today=TODAY,
        )


def test_a_review_records_who_performed_it(engine):
    _record(engine)
    with pytest.raises(ValueError, match="records who performed it"):
        store.review(
            engine,
            gate=GATE_INCENTIVE_ENGINE,
            subject_id="uk-avec",
            field_name="qs_engine_type",
            reviewer="  ",
            today=TODAY,
        )


def test_pending_is_not_a_review_outcome(engine):
    _record(engine)
    with pytest.raises(ValueError, match="not a review outcome"):
        store.review(
            engine,
            gate=GATE_INCENTIVE_ENGINE,
            subject_id="uk-avec",
            field_name="qs_engine_type",
            reviewer="reviewer-b",
            state=PENDING,
            today=TODAY,
        )


# ── The read path is silent ──────────────────────────────────────────────────


def test_a_claim_that_went_stale_after_review_is_withheld_not_raised(engine):
    """Report generation is not the moment to surface a research problem."""
    _record(engine)
    store.review(
        engine,
        gate=GATE_INCENTIVE_ENGINE,
        subject_id="uk-avec",
        field_name="qs_engine_type",
        reviewer="reviewer-b",
        today=TODAY,
    )
    # The source verification now sits in the future relative to the run.
    assert store.readable_values(
        engine, GATE_INCENTIVE_ENGINE, today=date(2026, 9, 1)
    ) == {}


def test_progress_counts_against_the_real_ledger(engine):
    _record(engine)
    before = store.progress(
        engine, GATE_INCENTIVE_ENGINE, ["uk-avec", "fr-trip"], today=TODAY
    )
    assert before.verified == 0
    assert before.outstanding == 2

    store.review(
        engine,
        gate=GATE_INCENTIVE_ENGINE,
        subject_id="uk-avec",
        field_name="qs_engine_type",
        reviewer="reviewer-b",
        today=TODAY,
    )
    after = store.progress(
        engine, GATE_INCENTIVE_ENGINE, ["uk-avec", "fr-trip"], today=TODAY
    )
    assert after.verified == 1
    assert not after.is_closed


# ── Without the table ────────────────────────────────────────────────────────


def test_an_absent_ledger_says_which_migration_is_missing():
    bare = sa.create_engine("sqlite://")
    with pytest.raises(store.LedgerUnavailable, match="u0v1w2x3y4z5"):
        store.load_claims(bare)
