"""Reading and writing the source verification ledger.

WHAT THIS IS FOR
----------------
``verification_ledger`` says what a verification must carry and migration
``u0v1w2x3y4z5`` gives it a table. Between them there was nothing: no way for a
researcher to record a claim and no way for an engine to read one, so the
harness was a contract with no traffic.

This is the traffic. It is deliberately small — load, record, review, count —
because the ledger's value is in what it refuses, and every convenience added
here is a place a refusal could be bypassed.

THE TWO RULES THAT SHAPE THE WRITE PATH
---------------------------------------
Insert, never overwrite. A second claim about the same field of the same
subject is a correction, and a correction that silently replaces its predecessor
loses the fact that someone once read the source differently. The table's unique
constraint makes that a conflict; this module reports the conflict with both
values rather than letting it surface as an integrity error nobody can read.

Recording is not reviewing. A claim arrives PENDING however confident its author
is, and reaching VERIFIED takes a second call by a named reviewer. For the gates
that need independent QA, that reviewer cannot be the author — enforced by
``validate_claim`` on the way out, so a claim written straight to VERIFIED by a
caller who bypassed ``review`` is still unreadable.

THE READ PATH IS SILENT
-----------------------
``readable_values`` withholds a bad claim rather than raising. It runs during
report generation, which is not the moment to surface a research problem; the
withheld fact simply stays UNKNOWN, which every engine already handles. The
worklist script is where problems become visible, because that is where someone
can act on them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

import sqlalchemy as sa

from app.modules.reports.verification_ledger import (
    PENDING,
    REJECTED,
    REVIEW_STATES,
    VERIFIED,
    SourceClaim,
    gate_progress,
    readable_claims,
    validate_claim,
)

TABLE = "source_verifications"


class LedgerUnavailable(RuntimeError):
    """The ledger table is not present in this database."""


@dataclass
class RecordResult:
    """What a recording run did, or would do in preflight."""

    inserted: int = 0
    unchanged: int = 0
    rejected: list[tuple[SourceClaim, tuple[str, ...]]] = field(default_factory=list)
    conflicts: list[tuple[SourceClaim, Any]] = field(default_factory=list)
    applied: bool = False

    @property
    def is_clean(self) -> bool:
        return not self.rejected and not self.conflicts


def _table(conn: sa.Connection) -> sa.Table:
    if not sa.inspect(conn).has_table(TABLE):
        raise LedgerUnavailable(
            f"{TABLE} is not present; apply migration u0v1w2x3y4z5 first"
        )
    return sa.Table(TABLE, sa.MetaData(), autoload_with=conn)


def _to_claim(row: Any) -> SourceClaim:
    mapping = row._mapping if hasattr(row, "_mapping") else row
    verified_on = mapping["verified_on"]
    if isinstance(verified_on, str):
        verified_on = date.fromisoformat(verified_on[:10])
    return SourceClaim(
        gate=mapping["gate"],
        subject_id=mapping["subject_id"],
        field=mapping["field"],
        value=mapping["value"],
        source_url=mapping["source_url"],
        source_basis=mapping["source_basis"],
        verified_on=verified_on,
        verified_by=mapping["verified_by"],
        review_state=mapping["review_state"],
        reviewed_by=mapping["reviewed_by"],
        requires_independent_qa=bool(mapping["requires_independent_qa"]),
        qa_by=mapping["qa_by"],
        notes=mapping["notes"],
    )


def load_claims(
    engine: sa.Engine, *, gate: str | None = None, subject_ids: Iterable[str] | None = None
) -> list[SourceClaim]:
    """Every recorded claim, whatever its review state.

    Unfiltered by state on purpose: the worklist needs to see rejected and
    pending claims, and only the read path filters them out.
    """
    with engine.connect() as conn:
        table = _table(conn)
        query = sa.select(table)
        if gate:
            query = query.where(table.c.gate == gate)
        if subject_ids is not None:
            wanted = [str(item) for item in subject_ids]
            if not wanted:
                return []
            query = query.where(table.c.subject_id.in_(wanted))
        return [_to_claim(row) for row in conn.execute(query)]


def readable_values(
    engine: sa.Engine, gate: str, *, today: date
) -> dict[str, dict[str, Any]]:
    """Verified facts an engine may act on, as ``{subject_id: {field: value}}``.

    Anything unverified, rejected or malformed is absent rather than reported.
    A withheld fact stays UNKNOWN, which is the state every engine already
    handles, and the worklist is where the problem becomes visible instead.
    """
    claims = readable_claims(load_claims(engine, gate=gate), today=today)
    values: dict[str, dict[str, Any]] = {}
    for claim in claims:
        values.setdefault(claim.subject_id, {})[claim.field] = claim.value
    return values


def record_claims(
    engine: sa.Engine, claims: Sequence[SourceClaim], *, apply: bool = False, today: date
) -> RecordResult:
    """Insert new claims. Read-only unless ``apply``.

    A claim that fails validation is rejected with every reason, so a researcher
    fixing one does not meet the next on the following run. A claim whose
    subject and field already carry a different value is a conflict, reported
    with both values rather than silently replacing the earlier reading.
    """
    result = RecordResult(applied=apply)

    with engine.connect() as conn:
        table = _table(conn)
        existing = {
            (row.gate, row.subject_id, row.field): row
            for row in conn.execute(
                sa.select(
                    table.c.gate, table.c.subject_id, table.c.field, table.c.value
                )
            )
        }

    # Keys already queued in this batch, tracked separately from the recorded
    # ones. Marking them in `existing` would need a sentinel, and a None
    # sentinel reads as "not present" to the check below — which inserts the
    # duplicate and trips the unique constraint at flush time.
    queued: set[tuple[str, str, str]] = set()

    pending: list[dict[str, Any]] = []
    for claim in claims:
        # Validated as PENDING regardless of the state the caller asked for:
        # a claim is not reviewed by the act of being written down, and the
        # reviewer fields are checked when `review` sets them.
        problems = validate_claim(
            SourceClaim(**{**claim.__dict__, "review_state": PENDING}), today=today
        )
        if problems:
            result.rejected.append((claim, problems))
            continue

        key = (claim.gate, claim.subject_id, claim.field)
        if key in queued:
            result.unchanged += 1
            continue
        prior = existing.get(key)
        if prior is not None:
            if prior.value == claim.value:
                result.unchanged += 1
            else:
                result.conflicts.append((claim, prior.value))
            continue

        pending.append(
            {
                "id": uuid.uuid4().hex,
                "gate": claim.gate,
                "subject_id": claim.subject_id,
                "field": claim.field,
                "value": claim.value,
                "source_url": claim.source_url,
                "source_basis": claim.source_basis,
                "verified_on": claim.verified_on,
                "verified_by": claim.verified_by,
                "review_state": PENDING,
                "reviewed_by": None,
                "requires_independent_qa": claim.needs_independent_qa,
                "qa_by": None,
                "notes": claim.notes,
            }
        )
        queued.add(key)

    result.inserted = len(pending)
    if apply and pending:
        with engine.begin() as conn:
            conn.execute(_table(conn).insert(), pending)
    return result


@dataclass(frozen=True)
class ReviewDecision:
    """One reviewer's verdict on one recorded claim."""

    gate: str
    subject_id: str
    field: str
    reviewer: str
    state: str = VERIFIED
    qa_by: str | None = None


@dataclass
class ReviewResult:
    reviewed: list[tuple[ReviewDecision, SourceClaim]] = field(default_factory=list)
    refused: list[tuple[ReviewDecision, str]] = field(default_factory=list)
    applied: bool = False


def review_many(
    engine: sa.Engine,
    decisions: Sequence[ReviewDecision],
    *,
    today: date,
    apply: bool = True,
) -> ReviewResult:
    """Move many claims at once, validating each exactly as ``review`` does.

    Batched because the per-claim path was not merely slower but unusable: it
    reflected the whole table twice for every claim, so two hundred and
    forty-five decisions meant four hundred and ninety schema reflections
    against a remote database, and the run took longer than the review had.

    The saving is in round trips and nowhere else. Every decision is still
    turned into the claim it would become and passed through ``validate_claim``,
    so a reviewer signing off their own market rule is refused here as it was
    before. A refusal is collected rather than raised: one bad signature must
    not discard the sound decisions filed beside it.
    """
    result = ReviewResult(applied=apply)

    with engine.connect() as conn:
        table = _table(conn)
        recorded = {
            (row.gate, row.subject_id, row.field): row
            for row in conn.execute(sa.select(table))
        }

    updates: list[dict[str, Any]] = []
    for decision in decisions:
        if decision.state not in REVIEW_STATES or decision.state == PENDING:
            result.refused.append((decision, f"{decision.state!r} is not a review outcome"))
            continue
        if not str(decision.reviewer or "").strip():
            result.refused.append((decision, "A review records who performed it"))
            continue

        row = recorded.get((decision.gate, decision.subject_id, decision.field))
        if row is None:
            result.refused.append((
                decision,
                f"No claim recorded for {decision.gate}/{decision.subject_id}/{decision.field}",
            ))
            continue

        reviewed = SourceClaim(
            **{
                **_to_claim(row).__dict__,
                "review_state": decision.state,
                "reviewed_by": decision.reviewer,
                "qa_by": decision.qa_by,
            }
        )
        if decision.state == VERIFIED:
            problems = validate_claim(reviewed, today=today)
            if problems:
                result.refused.append(
                    (decision, "This claim cannot be verified: " + " ".join(problems))
                )
                continue

        result.reviewed.append((decision, reviewed))
        updates.append({
            "b_gate": decision.gate,
            "b_subject": decision.subject_id,
            "b_field": decision.field,
            "b_state": decision.state,
            "b_reviewer": decision.reviewer,
            "b_qa": decision.qa_by,
        })

    if apply and updates:
        with engine.begin() as conn:
            table = _table(conn)
            conn.execute(
                table.update()
                .where(
                    sa.and_(
                        table.c.gate == sa.bindparam("b_gate"),
                        table.c.subject_id == sa.bindparam("b_subject"),
                        table.c.field == sa.bindparam("b_field"),
                    )
                )
                .values(
                    review_state=sa.bindparam("b_state"),
                    reviewed_by=sa.bindparam("b_reviewer"),
                    qa_by=sa.bindparam("b_qa"),
                ),
                updates,
            )
    return result


def review(
    engine: sa.Engine,
    *,
    gate: str,
    subject_id: str,
    field_name: str,
    reviewer: str,
    state: str = VERIFIED,
    qa_by: str | None = None,
    today: date,
) -> SourceClaim:
    """Move one claim to VERIFIED or REJECTED, and return what it became.

    A thin wrapper over ``review_many`` so the two paths cannot drift: a rule
    tightened for the batch is tightened for the single claim on the same line.
    It raises where the batch collects, because a caller naming one claim wants
    to hear that that claim failed.
    """
    decision = ReviewDecision(gate, subject_id, field_name, reviewer, state, qa_by)
    outcome = review_many(engine, [decision], today=today, apply=True)
    if outcome.refused:
        reason = outcome.refused[0][1]
        if reason.startswith("No claim recorded"):
            raise LookupError(reason)
        raise ValueError(reason)
    return outcome.reviewed[0][1]


def progress(
    engine: sa.Engine, gate: str, required_subject_ids: Iterable[str], *, today: date
):
    """How far one gate is from closed, against the real ledger."""
    return gate_progress(
        gate, required_subject_ids, load_claims(engine, gate=gate), today=today
    )


def rejected_count(engine: sa.Engine, gate: str | None = None) -> int:
    return sum(
        1 for claim in load_claims(engine, gate=gate) if claim.review_state == REJECTED
    )


def readable_claims_for_worklist(
    engine: sa.Engine, *, today: date
) -> list[SourceClaim]:
    """Every verified claim across all gates, for subtracting from the worklist.

    Separate from ``readable_values`` because the worklist needs the claims
    themselves — gate, subject and field — rather than the facts they carry.
    """
    return list(readable_claims(load_claims(engine), today=today))
