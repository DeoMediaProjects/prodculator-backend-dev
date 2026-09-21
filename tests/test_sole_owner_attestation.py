"""A sole owner can sign off the gates that ask for a second pair of eyes.

MARKET_HARD_GATE and FESTIVAL_SECTION require independent QA, and
`validate_claim` refuses a claim whose QA was signed by its author. The reason
is real: these claims decide whether an opportunity is shown as eligible to a
paying producer, and a misread deadline is what one pair of eyes misses.

It is also unsatisfiable on this project — one owner, a client who cannot
review, 226 of 289 claims behind it — and a rule nobody can meet does not
raise the standard, it leaves the research unsigned.

So the exception exists and has to be taken in writing. These pin that it
cannot be taken by leaving a column blank, which is the only way the record
stays worth auditing.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.modules.reports.verification_ledger import (
    GATES_REQUIRING_INDEPENDENT_QA,
    VERIFIED,
    SourceClaim,
    validate_claim,
)

TODAY = date(2026, 9, 21)
GATE = sorted(GATES_REQUIRING_INDEPENDENT_QA)[0]


def _claim(**over) -> SourceClaim:
    base = {
        "gate": GATE,
        "subject_id": "subject-1",
        "field": "deadline",
        "value": "2026-11-15",
        "source_url": "https://festival.example.org/submissions",
        "source_basis": "The official page states this deadline.",
        "verified_on": date(2026, 9, 18),
        "verified_by": "brian",
        "review_state": VERIFIED,
        "reviewed_by": "brian",
        "qa_by": "brian",
    }
    base.update(over)
    return SourceClaim(**base)


def _qa_problems(claim) -> list[str]:
    return [p for p in validate_claim(claim, today=TODAY) if "QA" in p]


class TestTheExceptionMustBeInWriting:
    def test_self_signed_qa_with_no_attestation_is_still_refused(self):
        # Unchanged from before. Leaving the column blank must not become a
        # quiet way to lower the bar.
        problems = _qa_problems(_claim())
        assert problems
        assert "no sole-owner attestation records why" in problems[0]

    def test_an_attestation_permits_it(self):
        claim = _claim(
            sole_owner_attestation=(
                "Sole owner; client cannot review. Checked against the "
                "official page twice, a week apart."
            )
        )
        assert _qa_problems(claim) == []

    def test_whitespace_is_not_an_attestation(self):
        assert _qa_problems(_claim(sole_owner_attestation="   "))

    def test_missing_qa_entirely_is_refused_attestation_or_not(self):
        # The attestation excuses a self-signed QA, not an absent one. Someone
        # still has to have checked it.
        problems = _qa_problems(
            _claim(qa_by=None, sole_owner_attestation="Sole owner.")
        )
        assert problems
        assert "none is recorded" in problems[0]


class TestItChangesNothingElse:
    def test_a_genuine_second_reviewer_needs_no_attestation(self):
        assert _qa_problems(_claim(qa_by="someone-else")) == []

    def test_an_attestation_does_not_excuse_a_missing_source(self):
        # It is an exception to one rule, not a general override. A claim with
        # no source is an opinion with a date on it whoever signed it.
        claim = _claim(source_url="", sole_owner_attestation="Sole owner.")
        problems = validate_claim(claim, today=TODAY)
        assert any("no source URL" in p for p in problems)

    def test_a_pending_claim_is_not_asked_for_qa_at_all(self):
        assert _qa_problems(_claim(review_state="PENDING", qa_by=None)) == []


class TestTheRecordStaysAuditable:
    def test_a_claim_says_whether_one_person_made_and_checked_it(self):
        # So a reader does not have to compare two strings to find out, and an
        # audit can separate the two populations.
        assert _claim().qa_was_self_signed is True
        assert _claim(qa_by="someone-else").qa_was_self_signed is False

    def test_an_absent_qa_is_not_self_signed(self):
        assert _claim(qa_by=None).qa_was_self_signed is False

    @pytest.mark.parametrize("gate", sorted(GATES_REQUIRING_INDEPENDENT_QA))
    def test_every_two_person_gate_can_be_attested(self, gate):
        # If one of them could not, the blocker would simply move rather than
        # be resolved.
        claim = _claim(gate=gate, sole_owner_attestation="Sole owner.")
        assert _qa_problems(claim) == []
