"""Grants Engine v2 payload models.

These mirror ``04_REPORT_AND_API/matching_result_schema.json`` and
``report_payload_schema.json`` field-for-field, because the report, the PDF, the
QA harness and any future admin endpoint all read the same object and the schema
files are the contract they were signed against.

WHY ``passed`` IS ALIASED TO ``pass``
-------------------------------------
The sample payload spells a gate outcome ``{"gate": "format", "pass": true}`` and
``pass`` is a reserved word in Python, so the field cannot carry that name. The
alias keeps the wire format identical to the schema while the attribute stays
legal — but it only works if serialisation goes through ``by_alias=True``, which
``as_payload_dict`` below enforces so no call site has to remember.

WHY INELIGIBLE RESULTS ARE STILL MATCHRESULTS
---------------------------------------------
v1 dropped a record that failed a gate with a bare ``continue``, so the report could
never answer "why is the fund I expected not here". Logic Guide §5 requires the
opposite: mark it INELIGIBLE with gate reasons. Every evaluated record therefore
produces a MatchResult; what changes is which list it lands in. ``raw_score`` stays a
required number because the JSON schema types it as one, but an INELIGIBLE result
carries 0.0 and ``portfolio_rank=None`` and is never placed in ``recommendations``.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.grants.v2_contracts import (
    CROSS_ENGINE_CAVEATS,
    GRANTS_DATABASE_VERSION,
)


class HardGateResult(BaseModel):
    """One gate's verdict on one record.

    ``tested`` is the field that keeps the engine honest. A gate whose required fact
    was unknown reports ``passed=True, tested=False`` — it did not clear the record,
    it declined to judge it. Collapsing that into a plain pass is how "we have no
    idea whether you qualify" becomes "you qualify".
    """

    model_config = ConfigDict(populate_by_name=True)

    gate: str
    passed: bool = Field(alias="pass", serialization_alias="pass")
    reason_code: str | None = None
    reason: str | None = None
    tested: bool = True


class ScoreComponent(BaseModel):
    """One signal that contributed points, with the code that names it.

    ``detail`` keeps the producer-facing sentence beside the machine code so the
    narrative layer never has to reconstruct English from a token.
    """

    reason_code: str
    points: float
    detail: str | None = None
    weight_version: str | None = None


class VerificationState(BaseModel):
    """What has actually been checked about this record, field by field.

    Developer Guide §4 requires identity, official source and current cycle to be
    independent facts. In the master they genuinely diverge — 253 records claim
    identity verification, 104 carry a verified source, 103 a verified cycle — so a
    single ``verified`` boolean cannot express the state the report needs to describe.
    """

    model_config = ConfigDict(extra="allow")

    record_verified: bool | None = None
    official_source_verified: bool | None = None
    current_cycle_verified: bool | None = None
    verified_at: str | None = None
    composite_state: str | None = None
    staleness_days: int | None = None


class DisplayFields(BaseModel):
    """Everything a renderer needs, so no consumer re-reads the grants table.

    ``amount`` and ``deadline`` are None — never ``""``, never ``0``, never the string
    ``"Rolling"`` — when nothing is verified. Three call sites in the current codebase
    render ``opp.deadline or 'Rolling'``, which invents a rolling deadline for every
    record that simply has no date; ``deadline_state`` exists so a renderer can say
    what is true without guessing.
    """

    model_config = ConfigDict(extra="allow")

    title: str
    funding_body: str | None = None
    territory: str | None = None
    amount: str | None = None
    deadline: str | None = None
    official_source: str | None = None

    # Structured companions. readiness.py currently regex-parses money back out of a
    # display string; these exist so it never has to.
    amount_value: float | None = None
    amount_currency: str | None = None
    amount_is_per_project: bool | None = None
    deadline_state: str | None = None
    deadline_date: str | None = None
    days_until_deadline: int | None = None
    opportunity_type: str | None = None
    production_stage: list[str] = Field(default_factory=list)
    eligible_formats: list[str] = Field(default_factory=list)
    key_rule: str | None = None
    eligibility_summary: str | None = None
    badges: list[str] = Field(default_factory=list)


class CoProductionContext(BaseModel):
    """What the treaty engine said, carried rather than re-derived.

    Logic Guide §11: a fund requiring official co-production "should read the
    treaty/co-production result object, not infer from selected territories alone".
    """

    treaty_route_identified: bool = False
    status: str = "NOT_APPLICABLE"
    route: str | None = None
    note: str | None = None


class MatchResult(BaseModel):
    """One opportunity evaluated against one project."""

    model_config = ConfigDict(populate_by_name=True)

    opportunity_id: str
    eligibility_status: Literal[
        "ELIGIBLE", "INELIGIBLE", "CONDITIONAL", "CURRENT_CYCLE_UNVERIFIED"
    ]
    hard_gate_results: list[HardGateResult] = Field(default_factory=list)
    raw_score: float = 0.0
    score_components: list[ScoreComponent] = Field(default_factory=list)
    portfolio_rank: int | None = None
    match_reasons: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    verification: VerificationState = Field(default_factory=VerificationState)
    display_fields: DisplayFields
    source_project_facts: dict[str, Any] | None = None
    co_production_context: CoProductionContext | None = None

    @property
    def is_presentable(self) -> bool:
        """Whether this result may appear in the report's ranked list.

        CONDITIONAL and CURRENT_CYCLE_UNVERIFIED are presentable — with their caveats
        — because the contract demotes them rather than deleting them. Only INELIGIBLE
        is withheld.
        """
        return self.eligibility_status != "INELIGIBLE"

    @property
    def is_actionable(self) -> bool:
        """Whether the report may present this as something to act on now.

        Logic Guide §3: a record whose current cycle is unverified "must not be
        rendered as an actionable current opportunity".
        """
        return self.eligibility_status == "ELIGIBLE"


class NarrativeContext(BaseModel):
    """The sentences the report is allowed to say about this result set.

    ``not_committed_finance`` defaults True and nothing sets it False. It is a
    standing assertion, not a computed one: Logic Guide §9 says a matched grant is an
    opportunity until awarded, so there is no state of this engine in which the answer
    is anything else.
    """

    summary_statement: str = ""
    financing_stack_role: str = "Selective public funding / co-production support"
    not_committed_finance: bool = True
    cross_engine_caveats: list[str] = Field(default_factory=lambda: list(CROSS_ENGINE_CAVEATS))


class GrantsReportPayload(BaseModel):
    """The single object every report section consumes.

    ``eligible_match_count`` is the whole eligible universe and ``recommendations`` is
    the entitlement slice of it, which is what lets the report say "10 shown from 23
    eligible opportunities" truthfully. Logic Guide §6 forbids limiting the query, so
    the count is always the real one.
    """

    database_version: str = GRANTS_DATABASE_VERSION
    eligible_match_count: int = 0
    display_limit: int = 0
    recommendations: list[MatchResult] = Field(default_factory=list)
    narrative_context: NarrativeContext = Field(default_factory=NarrativeContext)
    all_qualified_match_ids: list[str] = Field(default_factory=list)

    # Additive, outside the wire schema: the audit trail. Kept optional so a strict
    # validator against report_payload_schema.json still passes.
    package: str | None = None
    evaluated_at: str | None = None
    ineligible_count: int = 0
    routed_out_count: int = 0
    admin_flags: list[dict[str, Any]] = Field(default_factory=list)

    def as_payload_dict(self) -> dict[str, Any]:
        """Serialise with the schema's own field spellings.

        Always ``by_alias=True`` — without it ``hard_gate_results[].pass`` silently
        becomes ``passed`` and the payload stops matching the contract it claims to
        implement.
        """
        return self.model_dump(by_alias=True, mode="json")
