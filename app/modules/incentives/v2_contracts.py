"""Incentive Engine v2 contracts: vocabulary, field map and null semantics.

This module is deliberately data and rules only, with no database access and no
calculation, so every other layer can import it without a cycle.

WHY THERE IS A FIELD MAP
------------------------
The v2 Database Field Specification names 51 fields. Only ``qs_engine_type``
matches an existing column verbatim, but roughly twenty of the v2 concepts are
already stored under a different name: ``base_rate`` is ``rate_gross``,
``qs_absolute_cap`` is ``qualifying_spend_cap_amount``, ``official_source_url``
is ``source_url``, and so on.

Adding all 51 as new columns would leave two columns per concept and no rule
about which wins. That is precisely the failure this codebase has already paid
for: ``cap``, ``cap_amount`` and ``rebate_cap_amount`` were three different
things sharing one vague name, and a programme advertised "No cap" while an 80
percent restriction was silently reducing its qualifying spend.

So the map below is the single answer to "where does this v2 field live". A v2
name resolves either to an existing column or to one this migration adds, never
to both.
"""
from __future__ import annotations

from typing import Any, Final

# ── qualifying spend engines ─────────────────────────────────────────────────

#: The eleven statutory engines plus the no-programme marker, from the
#: Calculation Engine Rules. Kept in step with helpers.QS_ENGINE_TYPES, which is
#: what the report layer imports; the duplication is asserted in the tests rather
#: than resolved by an import, because helpers must not depend on this module.
QS_ENGINES: Final = (
    "CORE_LOWER_OF",
    "ELIGIBLE_LOCAL_SPEND",
    "QUALIFIED_LABOUR",
    "MULTI_BUCKET",
    "QAPE",
    "QNZPE",
    "VFX_ONLY",
    "PDV_ONLY",
    "TIERED_SPEND",
    "INVESTOR_TAX_SHELTER",
    "COMPETITIVE_GRANT",
    "NO_PROGRAMME",
)

# ── calculation statuses ─────────────────────────────────────────────────────

#: Result statuses and whether each may carry a number. The pairing is the point:
#: a status without its financial treatment is the ambiguity the v2 spec exists to
#: remove, since "conditional" meant different things to the engine and the copy.
CALCULATION_STATUSES: Final[dict[str, dict[str, Any]]] = {
    "ESTIMATED": {
        "numeric": True,
        "in_ranking": True,
        "meaning": "Every input the deterministic formula requires is present.",
    },
    "CONDITIONAL": {
        "numeric": True,
        "in_ranking": True,
        "meaning": (
            "A defensible scenario can be shown, but it rests on a planning "
            "assumption or an unresolved approval or eligibility condition."
        ),
    },
    "REQUIRES_COST_BREAKDOWN": {
        "numeric": False,
        "in_ranking": True,
        "meaning": (
            "The programme is identified but the statutory cost base it "
            "calculates from has not been supplied. No number is invented."
        ),
    },
    "NOT_ELIGIBLE": {
        "numeric": False,
        "in_ranking": True,
        "meaning": "Known project facts fail a mandatory programme rule.",
    },
    "PROGRAMME_UNVERIFIED": {
        "numeric": False,
        "in_ranking": False,
        "meaning": "Official rules are incomplete or conflicting.",
    },
    "BLOCKED": {
        "numeric": False,
        "in_ranking": False,
        "meaning": "Calculation verification is blocked by an administrator.",
    },
    "SUSPENDED": {
        "numeric": False,
        "in_ranking": False,
        "meaning": "The programme is not currently running.",
    },
    "NO_PROGRAMME": {
        "numeric": False,
        "in_ranking": False,
        "meaning": "No claimable programme is recorded for this territory.",
    },
}

#: Statuses that may carry a figure.
NUMERIC_STATUSES: Final = frozenset(
    name for name, spec in CALCULATION_STATUSES.items() if spec["numeric"]
)

#: Statuses excluded from financial ranking and totals.
EXCLUDED_FROM_RANKING: Final = frozenset(
    name for name, spec in CALCULATION_STATUSES.items() if not spec["in_ranking"]
)

# ── verification gates ───────────────────────────────────────────────────────

#: Calculation verification is a separate gate from source verification. A green
#: source badge must never imply the formula is ready, which is stated twice in
#: the pack because it is the mistake it expects.
CALCULATION_VERIFICATION = ("ready", "conditional", "blocked")
SOURCE_VERIFICATION = ("government_verified", "official_administrator", "unverified")
BANKABILITY_RESEARCH = (
    "government_direct",
    "partial",
    "industry_only",
    "insufficient_data",
)

#: Only this value permits a deterministic numeric output.
CALCULATION_READY: Final = "ready"

# ── canonical statutory input registry ───────────────────────────────────────

#: The statutory cost bases a programme may require, keyed by canonical name.
#:
#: Deliberately generic. The specification forbids territory-specific columns
#: such as ``uk_core_spend`` or ``bc_labour``, so a new programme reuses an
#: existing key and a genuinely new statutory base is added here once rather than
#: as another column on another table.
CANONICAL_INPUTS: Final[dict[str, str]] = {
    "eligible_local_spend": "Defined local eligible expenditure",
    "qualified_labour": "Qualified labour base",
    "resident_labour": "Resident labour bucket",
    "nonresident_labour": "Permitted non-resident labour bucket",
    "vendor_spend": "Qualified vendor or non-labour spend",
    "local_core_expenditure": "Local core costs",
    "global_core_expenditure": "Relevant global core costs",
    "vfx_expenditure": "Qualifying visual effects base",
    "pdv_expenditure": "Qualifying post, digital and visual effects base",
    "qape": "Qualifying Australian Production Expenditure",
    "pdv_qape": "Post, digital and visual effects specific QAPE",
    "qnzpe": "Qualifying New Zealand Production Expenditure",
    "qualified_production_expenditure": "Generic statutory qualified production spend",
    # Uplift bases. A programme paying a higher rate on a subset of spend needs
    # that subset as its own figure, because an uplift is not a rate on the whole
    # base. California, Louisiana and New Mexico all work this way.
    "out_of_zone_expenditure": (
        "Qualified expenditure outside a programme's designated metropolitan "
        "zone, where the programme pays an uplift on it"
    ),
    "local_hire_wages": (
        "Qualified wages to workers meeting a programme's local hire test, which "
        "is usually narrower than simple residency"
    ),
    "other_programme_bucket": "Structured extension for a base with no common key",
}

#: Which engines require which inputs for an exact figure. Used to validate a
#: programme's declared inputs against its engine, so a labour credit cannot be
#: configured to ask only for total spend.
ENGINE_REQUIRED_INPUTS: Final[dict[str, tuple[str, ...]]] = {
    "CORE_LOWER_OF": ("local_core_expenditure", "global_core_expenditure"),
    "ELIGIBLE_LOCAL_SPEND": ("eligible_local_spend",),
    "QUALIFIED_LABOUR": ("qualified_labour",),
    "MULTI_BUCKET": (),  # buckets are programme specific; declared per record
    "QAPE": ("qape",),
    "QNZPE": ("qnzpe",),
    "VFX_ONLY": ("vfx_expenditure",),
    "PDV_ONLY": ("pdv_expenditure",),
    "TIERED_SPEND": ("eligible_local_spend",),
    "INVESTOR_TAX_SHELTER": (),  # not calculated from production spend
    "COMPETITIVE_GRANT": (),
    "NO_PROGRAMME": (),
}

# ── provenance ───────────────────────────────────────────────────────────────

#: How well known a supplied statutory amount is.
INPUT_STATUSES: Final = ("known", "planning_assumption", "unknown")

#: Where a supplied amount came from. There is no AI-generated member, by design:
#: the narrative layer may explain an input but may never populate one.
INPUT_SOURCES: Final = ("user_entered", "imported_budget", "verified_cost_report")

#: Where a scenario spend came from.
SCENARIO_SPEND_SOURCES: Final = ("user_entered", "imported_budget", "unknown")

#: An input carrying this status downgrades an otherwise exact result to
#: CONDITIONAL, because the number rests on the producer's own estimate.
PLANNING_ASSUMPTION: Final = "planning_assumption"

# ── production structure mode ────────────────────────────────────────────────

#: How the selected territories relate to one another. This is not presentation:
#: it changes what the spend figures mean, whether they reconcile, and whether the
#: report may rank the territories against each other.
#:
#:   comparison    Alternatives. UK or Germany or Malta. Spends are separate
#:                 scenarios, are never summed, and need not total the budget.
#:                 Territories rank against each other.
#:   coproduction  Complementary parts of one production. France and Germany and
#:                 Ireland. Allocations belong to a single structure, reconcile to
#:                 the structure budget, and must NOT be ranked as if the producer
#:                 has to choose one.
#:   undecided     Comparison logic, with co-production opportunities surfaced
#:                 separately. Behaves as comparison for every calculation.
STRUCTURE_MODES: Final = ("comparison", "coproduction", "undecided")

#: Modes in which spends are alternatives rather than allocations.
COMPARISON_MODES: Final = frozenset({"comparison", "undecided"})

#: Most territories a producer may compare as alternatives, by plan. This bounds
#: how much of the market a tier explores, which is a product lever.
COMPARISON_TERRITORY_LIMITS: Final[dict[str, int | None]] = {
    "free": 3,
    "professional": 5,
    "producer": None,
    "studio": None,
}

#: Most partners one co-production structure may hold.
#:
#: Deliberately NOT the comparison limit. A multilateral co-production under the
#: Council of Europe Convention requires at least three co-producers established
#: in three different Parties, so a cap of three would permit only the bare legal
#: minimum and make any four-partner structure unmodellable. Blocking a real
#: production structure is a different thing from bounding how many alternatives
#: a tier may explore, so the two limits are separate numbers.
#:
#: Six sits comfortably above the two to four partners a typical structure carries
#: while still bounding the form.
MAX_COPRODUCTION_PARTNERS: Final = 6

#: The legal minimum for a multilateral co-production. Held for messaging, not
#: enforcement: a bilateral of two is legitimate, and so is a single territory
#: while the producer is still deciding.
MULTILATERAL_MINIMUM_PARTNERS: Final = 3

#: Where a co-production partner stands. Neither value implies authority approval;
#: that is the treaty layer's separate concern.
PARTNER_STATUSES: Final = ("candidate", "confirmed")

#: Whether the structure's allocations account for the budget.
RECONCILIATION_STATUSES: Final = (
    "reconciled",
    "under_allocated",
    "over_allocated",
    "not_assessable",
)

#: Whether combined public support has cleared the aid-intensity review. Combined
#: support is never presented as available finance before this passes.
CUMULATION_STATUSES: Final = (
    "not_checked",
    "requires_review",
    "requires_fx",
    "passed",
    "blocked",
)


def is_comparison_mode(mode: str | None) -> bool:
    """Whether spends are alternatives. Absent mode means comparison."""
    return (mode or "comparison").strip().lower() in COMPARISON_MODES


def reconcile_allocations(
    total_budget: float | None,
    allocations: list[float | None],
    unallocated: float | None = None,
    *,
    tolerance: float = 0.01,
) -> tuple[str, float | None]:
    """Compare co-production allocations against the structure budget.

    Returns the reconciliation status and the remaining amount, positive when the
    structure is under-allocated.

    Deliberately reports rather than rejects. A producer mid-way through entering
    a structure is legitimately under-allocated, and an over-allocation may be a
    currency difference rather than an error. Blocking either would stop them
    working; stating it lets the report say so.

    An allocation of ``None`` is unknown and makes the total unassessable, because
    treating it as zero would report a false shortfall.
    """
    if total_budget is None or total_budget <= 0:
        return "not_assessable", None
    if any(a is None for a in allocations):
        return "not_assessable", None

    allocated = sum(a for a in allocations if a is not None) + (unallocated or 0.0)
    remaining = total_budget - allocated
    if abs(remaining) <= tolerance:
        return "reconciled", 0.0
    if remaining > 0:
        return "under_allocated", remaining
    return "over_allocated", remaining


# ── missing input behaviour ──────────────────────────────────────────────────

MISSING_INPUT_BEHAVIOURS: Final = (
    "requires_cost_breakdown",
    "conditional_allowed",
    "not_applicable",
)

# ── the field map ────────────────────────────────────────────────────────────

#: v2 specification field name -> the column that holds it.
#:
#: An entry mapping to a differently named column means the concept already
#: exists and must not be duplicated. ``None`` means the field is genuinely new
#: and is added by migration x4y5z6a7b8c9.
V2_FIELD_MAP: Final[dict[str, str | None]] = {
    # Identity
    "programme_id": "programme_id",
    "programme_name": "program",
    "jurisdiction_country": "jurisdiction_country",
    "jurisdiction_subdivision": "jurisdiction_subdivision",
    "jurisdiction_level": "programme_level",
    "parent_programme_id": "parent_programme_id",
    # Versioning
    "rule_version": "rule_version",
    "effective_from": "effective_from",
    "effective_to": "effective_to",
    "last_rule_verified": "last_verified_at",
    "programme_status": "status",
    # Scenario
    "scenario_spend_supported": "scenario_spend_supported",
    "scenario_spend_currency_policy": "scenario_spend_currency_policy",
    # Qualifying spend
    "qs_engine_type": "qs_engine_type",
    "qs_statutory_definition": "qs_basis",
    "qs_formula_version": "qs_formula_version",
    "eligible_cost_definition": "eligible_cost_definition",
    "excluded_cost_definition": "excluded_cost_definition",
    "minimum_spend_amount": "qualifying_spend_min",
    "minimum_spend_basis": "minimum_spend_basis",
    "qs_percentage_cap": "qualifying_spend_cap_pct",
    "qs_absolute_cap": "qualifying_spend_cap_amount",
    # Rate
    "base_rate": "rate_gross",
    "rate_tiers_json": "rate_tier_json",
    "uplift_rules_json": "uplift_rules_json",
    "effective_rate_method": "effective_rate_method",
    # Caps
    "credit_output_cap": "rebate_cap_amount",
    "per_person_cap_json": "per_person_cap_json",
    "annual_pool": "annual_pool",
    "annual_pool_type": "annual_pool_type",
    # Access
    "foreign_producer_access": "foreign_producer_access",
    "foreign_producer_route": "foreign_producer_route",
    "local_entity_requirement": "local_entity_requirement",
    "cultural_or_points_test": "cultural_test_required",
    "application_timing_requirement": "application_timing_requirement",
    "preapproval_required": "preapproval_required",
    # Interaction
    "stacking_allowed": "stacking_allowed",
    "stackable_with_json": "stackable_with",
    "assistance_treatment_json": "assistance_treatment_json",
    "calculation_order": "calculation_order",
    "mutual_exclusion_group": "mutual_exclusion_group",
    # Control
    "source_verification_status": "verification_status",
    "calculation_verification_status": "calculation_verification_status",
    "bankability_research_status": "bankability_research_status",
    "official_authority": "authority",
    "official_source_url": "source_url",
    "legal_reference": "legal_reference",
    # Reporting
    "report_qualification_text": "report_qualification_text",
    "report_warning_text": "report_warning_text",
    "missing_input_message": "missing_input_message",
    "confidence_display": "confidence_display",
}

#: v2 fields served by a column that already existed under another name. Listed
#: so a reader can see at a glance what was reused rather than duplicated.
REUSED_COLUMNS: Final[dict[str, str]] = {
    v2: column
    for v2, column in V2_FIELD_MAP.items()
    if column is not None and column != v2
}


def v2_column(field: str) -> str:
    """The column holding a v2 specification field.

    Raises rather than returning the input unchanged: a typo that silently became
    a new column name is how a concept ends up stored in two places.
    """
    try:
        column = V2_FIELD_MAP[field]
    except KeyError:
        raise KeyError(
            f"{field!r} is not an Incentive Engine v2 field. Add it to "
            f"V2_FIELD_MAP with the column that holds it."
        ) from None
    assert column is not None, f"{field!r} has no column assigned"
    return column


# ── null semantics ───────────────────────────────────────────────────────────


def is_unknown(amount: Any) -> bool:
    """True when a statutory amount is unknown, as opposed to a known zero.

    The specification calls this out as a critical database rule, and it is the
    rule every fallback bug violates. NULL means the producer has not told us.
    Zero means the producer has told us there is none. They lead to different
    statuses: unknown gives REQUIRES_COST_BREAKDOWN, zero gives a calculated
    result that happens to be nil.
    """
    return amount is None


def resolve_statutory_amount(amount: Any) -> float | None:
    """Coerce a supplied statutory amount, preserving the unknown or zero split.

    Never substitutes a budget, a scenario spend or a percentage of either. That
    substitution is the single behaviour the v2 rebuild exists to remove.
    """
    if amount is None:
        return None
    if isinstance(amount, bool):  # a bool is an int in Python; never an amount
        return None
    if isinstance(amount, (int, float)):
        return float(amount)
    text = str(amount).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def missing_required_inputs(
    engine: str,
    supplied: dict[str, Any],
    declared_required: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    """Which required statutory inputs are unknown for this engine.

    ``supplied`` maps canonical input key to amount. An amount of zero counts as
    supplied; ``None`` or an absent key counts as missing. ``declared_required``
    overrides the engine default, which is what a MULTI_BUCKET programme uses to
    name its own buckets.
    """
    required = (
        tuple(declared_required)
        if declared_required is not None
        else ENGINE_REQUIRED_INPUTS.get(engine.upper(), ())
    )
    missing: list[str] = []
    for key in required:
        if resolve_statutory_amount(supplied.get(key)) is None:
            missing.append(key)
    return missing


def status_for_inputs(
    engine: str,
    supplied: dict[str, Any],
    provenance: dict[str, str] | None = None,
    declared_required: tuple[str, ...] | list[str] | None = None,
) -> str:
    """The calculation status implied by the inputs held, before eligibility.

    Deliberately does not calculate anything. It answers only whether the engine
    could calculate, and how much confidence the provenance supports.
    """
    engine_upper = engine.upper()
    if engine_upper == "NO_PROGRAMME":
        return "NO_PROGRAMME"
    if engine_upper in {"INVESTOR_TAX_SHELTER", "COMPETITIVE_GRANT"}:
        # Neither is calculated from production spend, so no quantity of supplied
        # cost detail promotes them to an exact figure.
        return "CONDITIONAL"
    if missing_required_inputs(engine_upper, supplied, declared_required):
        return "REQUIRES_COST_BREAKDOWN"
    provenance = provenance or {}
    if any(v == PLANNING_ASSUMPTION for v in provenance.values()):
        return "CONDITIONAL"
    return "ESTIMATED"
