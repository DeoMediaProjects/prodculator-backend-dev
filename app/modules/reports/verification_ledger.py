"""One ledger for every source verification the v2 cutover depends on.

WHY ONE LEDGER FOR FIVE ENGINES
-------------------------------
The cutover is blocked on roughly six hundred individual facts: a festival
section's deadline, a market track's stage gate, a company's acquisition scope,
a grant row's destination, a programme's statutory engine. They live in five
different tables and are researched by different people at different times.

What they have in common is the shape of the claim. Someone asserts that a named
field of a named record holds a particular value; they say where they read it;
they say when. Everything downstream — whether a festival may take a paid slot,
whether an incentive may produce a figure — is a question about that assertion's
standing.

Kept per engine, that shape drifts: one table stores a URL and another a note,
one records a date and another a boolean, and "verified" comes to mean five
different things. Kept here, a single rule decides what counts, and adding an
engine adds rows rather than a sixth definition of the word.

WHAT THE LEDGER REFUSES
-----------------------
A claim with no source URL is not a verification, it is an opinion with a date
on it. A claim whose source is the record's own database row is circular. A
claim verified in the future has a typo or a clock problem, and either way is
not evidence. None of these are admitted, and none can be forced through with a
flag — an operator who disagrees fixes the claim, not the loader.

The state machine is deliberately small. A claim is PENDING until someone
reviews it, then VERIFIED or REJECTED. Only VERIFIED is readable by an engine.

INDEPENDENT QA
--------------
Some claims need a second pair of eyes. The Markets handoff says so explicitly:
its 203 hard gates are prose, and turning prose into a typed rule is
interpretation, so the person who wrote the rule may not be the person who
confirms it. A claim marked ``requires_independent_qa`` is not readable until a
DIFFERENT reviewer has signed it off. Self-QA is rejected by identity, not by
policy — the check is in the loader, so it cannot be skipped by whoever is in a
hurry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

# ── Review states ────────────────────────────────────────────────────────────

PENDING = "PENDING"
VERIFIED = "VERIFIED"
REJECTED = "REJECTED"

REVIEW_STATES: frozenset[str] = frozenset({PENDING, VERIFIED, REJECTED})

#: The only state an engine may read. Named as a set rather than tested with
#: ``!= REJECTED`` so that adding a future state cannot accidentally make it
#: readable.
READABLE_STATES: frozenset[str] = frozenset({VERIFIED})

# ── The gates a claim can belong to ──────────────────────────────────────────

GATE_INCENTIVE_ENGINE = "INCENTIVE_ENGINE_CLASSIFICATION"
GATE_GRANTS_MIGRATION = "GRANTS_MIGRATION_DECISION"
GATE_COMMERCIAL_PROFILE = "COMMERCIAL_COMPANY_PROFILE"
GATE_MARKET_RULE = "MARKET_HARD_GATE"
GATE_MARKET_CYCLE = "MARKET_CYCLE"
GATE_FESTIVAL_SECTION = "FESTIVAL_SECTION"

GATES: frozenset[str] = frozenset(
    {
        GATE_INCENTIVE_ENGINE,
        GATE_GRANTS_MIGRATION,
        GATE_COMMERCIAL_PROFILE,
        GATE_MARKET_RULE,
        GATE_MARKET_CYCLE,
        GATE_FESTIVAL_SECTION,
    }
)

#: Gates whose claims are interpretation rather than transcription, and so need
#: a second reviewer. Translating a prose hard gate into a typed rule is the
#: case the Markets handoff names; a festival section's rules are the same kind
#: of judgement. Reading a published deadline off an official page is not.
GATES_REQUIRING_INDEPENDENT_QA: frozenset[str] = frozenset(
    {GATE_MARKET_RULE, GATE_FESTIVAL_SECTION}
)

#: Sources that are not evidence about the world. A claim citing the system's
#: own data as proof of itself is circular, and it is an easy mistake to make
#: when bulk-classifying from an existing column.
_CIRCULAR_SOURCE_MARKERS: tuple[str, ...] = (
    "prodculator",
    "localhost",
    "127.0.0.1",
    "file://",
)


class LedgerRejection(ValueError):
    """A claim that cannot stand as a verification."""


@dataclass(frozen=True)
class SourceClaim:
    """One asserted fact about one field of one record."""

    gate: str
    #: The record this is about, in its own engine's ID space.
    subject_id: str
    #: The field being asserted, e.g. ``qs_engine_type`` or ``section_deadline``.
    field: str
    value: Any
    #: Where it was read. An official page for the programme, festival or
    #: company — not a directory, aggregator or our own database.
    source_url: str
    #: What the source actually says, in its own words. Kept so a later reviewer
    #: can check the reading without visiting the page, and so a claim that
    #: quietly drifts from its source is visible.
    source_basis: str
    verified_on: date
    verified_by: str
    review_state: str = PENDING
    reviewed_by: str | None = None
    #: Set for gates where interpretation is involved. Defaults from the gate,
    #: so a caller cannot lower the bar for a market rule by omitting it.
    requires_independent_qa: bool | None = None
    qa_by: str | None = None
    notes: str | None = None

    @property
    def needs_independent_qa(self) -> bool:
        if self.requires_independent_qa is not None:
            return self.requires_independent_qa
        return self.gate in GATES_REQUIRING_INDEPENDENT_QA


def validate_claim(claim: SourceClaim, *, today: date) -> tuple[str, ...]:
    """Every reason this claim cannot be read as verified. Empty means it can.

    Returns all the reasons rather than the first, because a researcher fixing
    one problem should not discover the next one on the following run.
    """
    problems: list[str] = []

    if claim.gate not in GATES:
        problems.append(f"{claim.gate!r} is not a known verification gate")
    if not str(claim.subject_id or "").strip():
        problems.append("Claim names no subject record")
    if not str(claim.field or "").strip():
        problems.append("Claim names no field")
    if claim.review_state not in REVIEW_STATES:
        problems.append(f"{claim.review_state!r} is not a review state")

    url = str(claim.source_url or "").strip()
    if not url:
        problems.append("Claim has no source URL, so it is an opinion with a date on it")
    elif not url.lower().startswith("https://"):
        # http:// is not refused for security; it is refused because every
        # official source in this catalogue serves https, so a plain-http URL
        # is almost always a transcription slip.
        problems.append("Source URL is not an https official source")
    elif any(marker in url.lower() for marker in _CIRCULAR_SOURCE_MARKERS):
        problems.append("Source cites our own data, which cannot verify itself")

    if not str(claim.source_basis or "").strip():
        problems.append("Claim does not record what the source actually says")
    if not str(claim.verified_by or "").strip():
        problems.append("Claim records no verifier")

    if claim.verified_on > today:
        problems.append("Claim is verified in the future")

    if claim.review_state == VERIFIED:
        if not str(claim.reviewed_by or "").strip():
            problems.append("Verified claim records no reviewer")
        if claim.needs_independent_qa:
            qa = str(claim.qa_by or "").strip()
            if not qa:
                problems.append(
                    "This gate requires independent QA and none is recorded"
                )
            elif qa == str(claim.verified_by).strip():
                problems.append(
                    "Independent QA was signed by the same person who made the "
                    "claim"
                )

    return tuple(problems)


def readable_claims(
    claims: Iterable[SourceClaim], *, today: date
) -> tuple[SourceClaim, ...]:
    """The claims an engine may act on. Everything else is silently withheld.

    Silently because this is the runtime path: a report generation is not the
    moment to surface a research problem, and the worklist script exists to
    surface it at the moment someone can act. A withheld claim leaves its fact
    UNKNOWN, which every engine already handles.
    """
    return tuple(
        claim
        for claim in claims
        if claim.review_state in READABLE_STATES
        and not validate_claim(claim, today=today)
    )


@dataclass(frozen=True)
class GateProgress:
    gate: str
    required: int
    verified: int

    @property
    def outstanding(self) -> int:
        return max(self.required - self.verified, 0)

    @property
    def is_closed(self) -> bool:
        return self.required > 0 and self.outstanding == 0


def gate_progress(
    gate: str, required_subject_ids: Iterable[str], claims: Iterable[SourceClaim], *, today: date
) -> GateProgress:
    """How far one gate is from closed.

    Counts distinct subjects rather than claims, because one record needing
    three fields verified is one record either way, and counting claims would
    let a gate look further along for having been researched in more detail.
    """
    required = {str(item) for item in required_subject_ids if str(item).strip()}
    done = {
        claim.subject_id
        for claim in readable_claims(claims, today=today)
        if claim.gate == gate and claim.subject_id in required
    }
    return GateProgress(gate=gate, required=len(required), verified=len(done))
