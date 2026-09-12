"""Grants & Funding Engine v2 contracts: vocabulary, gates, weights, entitlements.

Data and rules only. No database access, no scoring, no I/O, so every other layer
— gates, scoring, portfolio, the report builder and the tests — can import this
without a cycle, and there is exactly one place that answers "what is a legal
value for this field".

WHY A CONTRACT MODULE AT ALL
----------------------------
v1 kept its vocabulary as string literals scattered across ``reports/matching.py``
(badge text), ``reports/builder.py`` (display caps) and the PDF template (a second
display cap). The result was three display limits that could disagree, a badge for
``legislative_risk`` that no column could ever populate, and a scoring weight table
that could only be read by reading the function that applied it.

The v2 Developer Guide §6 requires weights to be "versioned configuration rather
than hardcoded UI logic" and §15 requires "reason codes and score components
persisted in result payload". Both mean the vocabulary has to be a value, not a
literal buried in an ``if``.

THE ONE RULE THAT GOVERNS EVERY TABLE BELOW
-------------------------------------------
Null means unknown. It never means zero, "no", or "not eligible". A gate whose
required fact is unknown reports ``tested=False`` and contributes CONDITIONAL — it
never silently excludes. This is the difference between "this fund does not accept
your format" and "this fund has not told us which formats it accepts", and the
253-record master is full of the second kind: 89 records state no format at all,
105 state no stage, 149 carry no official source.
"""
from __future__ import annotations

import re
from typing import Final

# ── database version ─────────────────────────────────────────────────────────

#: Identifies the dataset a stored report was matched against, so a report can be
#: explained months later against the data that actually produced it. Sourced from
#: the master file's own ``version`` + ``frozen_at``.
GRANTS_DATABASE_VERSION: Final = "grants_master_v2_2026-09-04"

#: The scoring weight table's version, persisted on every score component. Bump
#: this whenever a weight in SCORE_WEIGHTS changes, never when code moves.
SCORING_WEIGHTS_VERSION: Final = "grants_v2_weights_1"

# ── eligibility status ───────────────────────────────────────────────────────

#: The four statuses in 04_REPORT_AND_API/matching_result_schema.json, verbatim.
#:
#: CURRENT_CYCLE_UNVERIFIED is the one that carries the most product weight: the
#: Logic Guide §3 says a record may be identity-verified while its current round is
#: unverified, and that such a record "must not be rendered as an actionable current
#: opportunity" — but it is NOT excluded. In the master that distinction covers 124
#: of the 204 paid-match-eligible records, so collapsing it into either ELIGIBLE or
#: INELIGIBLE would either overstate 124 opportunities or delete them.
ELIGIBILITY_STATUSES: Final = (
    "ELIGIBLE",
    "INELIGIBLE",
    "CONDITIONAL",
    "CURRENT_CYCLE_UNVERIFIED",
)

# ── routing and lifecycle ────────────────────────────────────────────────────

#: Which engine owns an opportunity. Logic Guide §11: incentives are calculated only
#: by the Incentive Engine, and markets/labs/WIP "must not return as grants".
ROUTING_VALUES: Final = (
    "GRANTS_FUNDS",
    "INCENTIVE_ENGINE",
    "MARKETS_LABS_WIP",
    "LABS",
    "FELLOWSHIP",
    "BROADCASTER_INVESTMENT",
    "RETIRED",
)

#: Only this routing value may be matched as a grant.
ROUTING_GRANTS: Final = "GRANTS_FUNDS"

#: Record lifecycle, independent of whether the current round happens to be open.
#: ARCHIVED / RECLASSIFIED / SPLIT_PARENT rows are RETAINED for audit (migration
#: instructions steps 4-6) and excluded from matching by this field — never by
#: being deleted, and never by the stale legacy ``status`` column, which the
#: disposition CSVs show still reads "open" on 20 of the 23 archived rows.
LIFECYCLE_STATES: Final = (
    "LIVE",
    "ARCHIVED",
    "RECLASSIFIED",
    "SPLIT_PARENT",
    "SUSPENDED",
    "NEEDS_REVIEW",
)

# ── current-cycle status ─────────────────────────────────────────────────────

#: The normalised cycle vocabulary. The master's own ``current_status`` field has
#: 53 distinct raw spellings; these eleven are what the engine reasons about.
CANONICAL_STATUSES: Final = (
    "OPEN_CURRENT_ROUND",
    "CLOSED_CURRENT_ROUND",
    "ROLLING",
    "UPCOMING",
    "CYCLE_BASED",
    "CURRENT_PROGRAMME",
    "SUSPENDED",
    "CONDITIONAL",
    "INVITE_ONLY",
    "ROUTED_OUT",
    "UNKNOWN",
)

#: Statuses that describe a programme a producer could act on now. UPCOMING is
#: deliberately absent: an upcoming round is only actionable when its dates are
#: verified, which is a verification question, not a status question, and is
#: decided by the current-cycle gate rather than by this tuple.
ACTIONABLE_STATUSES: Final = (
    "OPEN_CURRENT_ROUND",
    "ROLLING",
    "CYCLE_BASED",
    "CURRENT_PROGRAMME",
)

#: Statuses that are a hard exclusion. A closed round is not a recommendation
#: (acceptance_tests.md: "A closed current round never appears as a live paid
#: recommendation"), and an invite-only call is one a producer cannot apply to.
NON_ACTIONABLE_STATUSES: Final = (
    "CLOSED_CURRENT_ROUND",
    "SUSPENDED",
    "INVITE_ONLY",
    "ROUTED_OUT",
)

#: Ordered normalisation table for the raw ``current_status`` string.
#: FIRST MATCH WINS AND THE ORDER IS LOAD-BEARING — two orderings are wrong:
#:
#:  * "NO APPLICATIONS CURRENTLY OPEN" contains "open" and does NOT contain
#:    "closed". Tested after the generic open rule it reads as OPEN. It is Hot Docs
#:    — Blue Ice Fund, and its paid_match_eligible is True, so the reference demo's
#:    naive substring order surfaces a fund that is explicitly not accepting
#:    applications. Hence rule 2 precedes rule 10.
#:  * "closed_current_round" contains "round". Tested after the cycle rule, all 44
#:    closed records classify as CYCLE_BASED. Hence rule 3 precedes rule 11.
#:
#: Each entry is (kind, needle, canonical_status) where kind is one of
#: "contains" | "equals" | "startswith" | "in". Input is stripped and lowercased.
STATUS_RULES: Final[tuple[tuple[str, object, str], ...]] = (
    ("contains", "hiatus", "SUSPENDED"),
    ("equals", "no applications currently open", "CLOSED_CURRENT_ROUND"),
    ("contains", "closed", "CLOSED_CURRENT_ROUND"),
    ("in", ("open_until_budget_expended",), "ROLLING"),
    ("contains", "rolling", "ROLLING"),
    ("in", ("opening_soon", "upcoming", "next_round_scheduled", "2027_deadline_tbc"), "UPCOMING"),
    ("startswith", "opens_", "UPCOMING"),
    ("startswith", "next_cycle", "UPCOMING"),
    ("equals", "unknown", "UNKNOWN"),
    ("equals", "incentive_route", "ROUTED_OUT"),
    ("startswith", "invite_only", "INVITE_ONLY"),
    ("in", ("festival_selection_dependent", "conditional_follow_on"), "CONDITIONAL"),
    ("contains", "open", "OPEN_CURRENT_ROUND"),
    ("contains", "round", "CYCLE_BASED"),
    ("contains", "session", "CYCLE_BASED"),
    ("contains", "call", "CYCLE_BASED"),
    ("contains", "cycle", "CYCLE_BASED"),
    (
        "in",
        (
            "current",
            "verified_current",
            "current_programme",
            "current_2026_programme",
            "current programme",
            "active",
            "active_by_commissions",
        ),
        "CURRENT_PROGRAMME",
    ),
)

#: Pulls a date out of a status token such as "open_until_2026-09-11",
#: "opens_2026_09_15" or "CALL 2 DEADLINE 2026-09-04". Used ONLY to populate the
#: descriptive status window — never to invent ``next_deadline``. known_edge_cases.md
#: forbids fabricating a deadline, and a date inside a status string is evidence of
#: a window, not an approved application deadline.
STATUS_DATE_RE: Final = re.compile(r"(\d{4})[-_](\d{2})[-_](\d{2})")

# ── deadlines ────────────────────────────────────────────────────────────────

#: What kind of thing the raw ``next_deadline`` string turned out to be. Only
#: ISO_DATE may produce a real date and a days-until count; every other kind
#: renders as its own words and never as a fabricated "Rolling".
DEADLINE_KINDS: Final = (
    "ISO_DATE",
    "ISO_MONTH_PARTIAL",
    "ROLLING",
    "TBC",
    "FREE_TEXT",
    "UNKNOWN",
)

#: Days before a dated deadline at which a CLOSING SOON badge appears. Carried over
#: from v1 (matching.CLOSING_SOON_DAYS) and kept equal to
#: builder._DEADLINE_URGENT_DAYS so the funding section and the executive-summary
#: deadline flag cannot disagree about what "soon" means.
CLOSING_SOON_DAYS: Final = 90

#: Verification ageing. Unlike v1 these no longer stand alone: v1 excluded any
#: record with no verification date at all, which under the v2 master would be a
#: dataset-wide time bomb (every record's last_verified_at is mid-2026, so a fixed
#: six-month exclusion empties the entire funding section in early 2027). Here they
#: feed the composite verification state and a caveat; only STALE_EXCLUDE_MONTHS
#: still excludes, and the engine reports the excluded count so it can be alarmed on.
STALE_FLAG_MONTHS: Final = 4
STALE_EXCLUDE_MONTHS: Final = 6

# ── verification ─────────────────────────────────────────────────────────────

#: Composite verification states from Developer Guide §4. The guide is explicit that
#: the flat legacy ``verified`` boolean is the migration floor and "not a reason to
#: collapse these states": identity, official source and current cycle are
#: independent facts, and in the master 104 records carry a source while only 103
#: carry a verified cycle.
VERIFICATION_STATES: Final = (
    "VERIFIED_CURRENT",
    "PARTIALLY_VERIFIED",
    "IDENTITY_ONLY",
    "NEEDS_REVIEW",
    "ARCHIVED",
)

# ── hard gates ───────────────────────────────────────────────────────────────

#: Gate execution order. Every gate runs BEFORE any scoring — Logic Guide §5:
#: "a programme that fails a hard gate has no meaningful score for presentation.
#: Do not assign 0 and keep it in the same ranking list."
#:
#: v1 violated this twice: it initialised the score inside the nationality gate and
#: applied the budget bounds after five signals had already accumulated. Order here
#: is cheapest-and-most-decisive first, so an ineligible record is rejected on the
#: fact that most plainly disqualifies it rather than on whichever gate ran first.
HARD_GATES: Final = (
    "routing",
    "record_state",
    "current_cycle",
    "deadline",
    "format",
    "stage",
    "nationality",
    "applicant_structure",
    "territory_region",
    "budget",
    "co_production",
)

#: Gate outcomes that keep a record but qualify it. Named so a reader of a stored
#: payload can tell "we tested this and it passed" from "we could not test this".
GATE_PASS_CODES: Final = (
    "GATE_ROUTING_GRANTS_FUNDS",
    "GATE_RECORD_LIVE",
    "GATE_CYCLE_OPEN",
    "GATE_CYCLE_ROLLING",
    "GATE_DEADLINE_FUTURE",
    "GATE_DEADLINE_ROLLING",
    "GATE_FORMAT_MATCH",
    "GATE_FORMAT_UNSTATED",
    "GATE_STAGE_MATCH",
    "GATE_STAGE_UNSTATED",
    "GATE_NATIONALITY_MET",
    "GATE_NATIONALITY_NOT_REQUIRED",
    "GATE_APPLICANT_STRUCTURE_OK",
    "GATE_REGION_MATCH",
    "GATE_REGION_UNRESTRICTED",
    "GATE_BUDGET_WITHIN_VERIFIED_BOUNDS",
    "GATE_BUDGET_UNVERIFIED_SKIPPED",
    "GATE_COPRODUCTION_ROUTE_PRESENT",
    "GATE_COPRODUCTION_NOT_REQUIRED",
)

#: Gate outcomes that exclude the record from the ranked universe entirely.
GATE_FAIL_CODES: Final = (
    "GATE_ROUTING_NOT_GRANTS",
    "GATE_RECORD_ARCHIVED",
    "GATE_RECORD_RECLASSIFIED",
    "GATE_SPLIT_PARENT_RETIRED",
    "GATE_NOT_PAID_MATCH_ELIGIBLE",
    "GATE_RECORD_NEEDS_REVIEW",
    "GATE_CYCLE_CLOSED",
    "GATE_CYCLE_SUSPENDED",
    "GATE_CYCLE_INVITE_ONLY",
    "GATE_DEADLINE_PASSED",
    "GATE_FORMAT_MISMATCH",
    "GATE_STAGE_MISMATCH",
    "GATE_NATIONALITY_NO_ROUTE",
    "GATE_APPLICANT_STRUCTURE_MISMATCH",
    "GATE_REGION_MISMATCH",
    "GATE_BUDGET_ABOVE_VERIFIED_MAX",
    "GATE_BUDGET_BELOW_VERIFIED_MIN",
    "GATE_COPRODUCTION_REQUIRED_NO_ROUTE",
    "GATE_VERIFICATION_STALE",
)

#: Gate outcomes where the fact needed was unknown. These NEVER exclude. They mark
#: the record CONDITIONAL and name the missing fact, because Developer Guide §5 says
#: "use explicit producer/project facts only. Never infer", and §14 says an unknown
#: nationality is "do not infer; reject or mark needs fact" — the second half of
#: which is the only safe branch when no structured field carries the answer.
GATE_CONDITIONAL_CODES: Final = (
    "GATE_CYCLE_UNVERIFIED",
    "GATE_DEADLINE_TBC_UNVERIFIED",
    "GATE_DEADLINE_UNPARSEABLE",
    "GATE_NATIONALITY_UNKNOWN",
    "GATE_APPLICANT_STRUCTURE_UNKNOWN",
    "GATE_REGION_ORIGIN_UNKNOWN",
    "GATE_COPRODUCTION_ROUTE_UNCONFIRMED",
)

# ── scoring ──────────────────────────────────────────────────────────────────

#: Reference signals and weights, Developer Guide §6, preserved from v1 verbatim so
#: v2 scores stay comparable to reports already issued. The guide's list and the v1
#: implementation agree on all ten.
#:
#: Two properties are deliberate rather than accidental and must survive any tuning:
#:  * RANKED_TERRITORY and SCRIPT_ORIGIN_TERRITORY STACK (+6 when a fund's territory
#:    is both), which is what makes a home-territory fund outrank a generic one.
#:  * GLOBAL_OPEN and CONTINENT_AFFINITY are mutually exclusive with those two and
#:    with each other — they are the consolation signals for a fund that is not
#:    territorially yours, so they may not compound on top of a territory hit.
SCORE_WEIGHTS: Final[dict[str, float]] = {
    "TERRITORY_CONSIDERED": 3.0,
    "SCRIPT_ORIGIN_TERRITORY": 3.0,
    "GLOBAL_OPEN": 0.5,
    "CONTINENT_AFFINITY": 1.0,
    "GENRE_OVERLAP": 2.0,
    "SPECIALISED_FORMAT": 1.0,
    "BUDGET_FIT_VERIFIED": 2.0,
    "NATIONALITY_HOME": 2.0,
    "NATIONALITY_ROUTE_ONLY": -1.0,
    "REQUIRED_REGION_FIT": 1.0,
}

#: Human wording for each signal, shown to the producer as "why it matched".
#: Kept beside the weight so a new signal cannot be added without a sentence
#: explaining it, which is how v1's unreachable LEGISLATIVE RISK badge survived.
SCORE_REASON_LABELS: Final[dict[str, str]] = {
    "TERRITORY_CONSIDERED": "{territory} is a production territory you are considering",
    "SCRIPT_ORIGIN_TERRITORY": "{territory} is where your script is predominantly set",
    "GLOBAL_OPEN": "open to productions worldwide",
    "CONTINENT_AFFINITY": "open across {continent}, which includes your production territories",
    "GENRE_OVERLAP": "accepts {detail}",
    "SPECIALISED_FORMAT": "specifically funds {detail} projects",
    "BUDGET_FIT_VERIFIED": "your budget sits inside this fund's verified range",
    "NATIONALITY_HOME": "nationality requirement met — you are based in {territory}",
    "NATIONALITY_ROUTE_ONLY": (
        "restricted to {territory} entities — a local co-production structure "
        "would be required"
    ),
    "REQUIRED_REGION_FIT": "within this fund's {detail} remit",
}

# ── badges and caveats ───────────────────────────────────────────────────────

#: Prominence badges. LEGISLATIVE_RISK from v1 is deliberately NOT here: no column
#: has ever populated it, so it was a badge that could not fire.
BADGES: Final = (
    "NATIONALITY RESTRICTION",
    "REGIONAL RESTRICTION",
    "CO-PRODUCTION REQUIRED",
    "CLOSING SOON",
    "ROLLING",
    "CURRENT CYCLE UNVERIFIED",
)

#: Caveat text keyed by code. NOT_COMMITTED_FINANCE is attached to every single
#: recommendation without exception — Logic Guide §9 is unambiguous that matched
#: grants "must not be added numerically to committed finance merely because the
#: matcher found them", and the caveat is how the report says so in words.
CAVEATS: Final[dict[str, str]] = {
    "CAVEAT_NOT_COMMITTED_FINANCE": (
        "Matched opportunity, not committed finance. Treat as finance only once "
        "awarded and contracted."
    ),
    "CAVEAT_SELECTIVE_SUPPORT": "Selective support — a match is not an award.",
    "CAVEAT_AMOUNT_NOT_VERIFIED": (
        "No verified per-project amount published for this programme."
    ),
    "CAVEAT_POOL_NOT_PER_PROJECT": (
        "The figure stated is a total call pool, not a per-project maximum."
    ),
    "CAVEAT_CYCLE_UNVERIFIED": (
        "Current round and next dates are not verified — confirm on the official "
        "source before relying on this."
    ),
    "CAVEAT_ROLLING_NO_DEADLINE": "Rolling programme — no fixed deadline.",
    "CAVEAT_NEXT_CYCLE_TBC": "Next cycle dates are not yet published.",
    "CAVEAT_VERIFICATION_AGEING": (
        "Record last verified more than four months ago — re-verify before use."
    ),
    "CAVEAT_APPLICANT_STRUCTURE_REQUIRED": (
        "Requires a qualifying applicant entity — confirm your company structure."
    ),
    "CAVEAT_TREATY_ROUTE_NOT_CERTIFICATION": (
        "A treaty route identified is not co-production certification and not an "
        "award."
    ),
    "CAVEAT_BROADCASTER_RECOUPABLE": (
        "Broadcaster investment is a finance route, not a non-recoupable grant."
    ),
    "CAVEAT_CURRENCY_PRESENTATION_ONLY": (
        "Currency conversion is a presentation aid — programme rules apply in the "
        "native currency."
    ),
}

# ── package entitlements ─────────────────────────────────────────────────────

#: How many grants each package DISPLAYS. From 04_REPORT_AND_API/package_entitlements.json.
#:
#: This is a display depth, never a search scope. Logic Guide §6: "Do not implement
#: LIMIT 5/10 in the database query. Match and rank everything first; entitlement is
#: the final display layer." Every package sees the same eligible universe and the
#: same raw scores; only how far down the ranked list they can read differs.
#:
#: Two keys the contract file does not carry, both deliberate:
#:  * "single" AND "professional" are both present because app.models.enums
#:    .normalize_plan rewrites single -> professional, so a map keyed on only one of
#:    them is half unreachable depending on which side of that call you are on.
#:  * "free" is a PRODUCT DECISION, not a contract value — package_entitlements.json
#:    covers paid packages only, and fundingOpportunities is an Explorer section.
#:    Set to the cheapest paid depth so the free tier can never see more than a
#:    paying Single customer. Change it here, not at a call site.
GRANT_DISPLAY_LIMITS: Final[dict[str, int]] = {
    "free": 5,
    "single": 5,
    "professional": 5,
    "producer": 10,
    "studio": 10,
}

#: Applied when a package is absent or unrecognised. The cheapest paid depth, so an
#: unknown plan can never over-deliver.
DEFAULT_GRANT_DISPLAY_LIMIT: Final = 5


def display_limit(package: str | None) -> int:
    """How many grants this package may see.

    Tolerates None and unknown values by returning the most conservative limit
    rather than raising: a report that renders five funds is recoverable, a report
    that raises mid-build is not.
    """
    if not package:
        return DEFAULT_GRANT_DISPLAY_LIMIT
    return GRANT_DISPLAY_LIMITS.get(str(package).strip().lower(), DEFAULT_GRANT_DISPLAY_LIMIT)


# ── narrative vocabulary ─────────────────────────────────────────────────────

#: Logic Guide §8. The engine emits the left-hand term; nothing in the report — and
#: nothing the AI narrative layer writes — may promote a result rightwards.
#: MATCHED != ELIGIBLE != APPLIED != AWARDED != COMMITTED FINANCE.
NARRATIVE_LADDER: Final = (
    "MATCHED",
    "ELIGIBLE",
    "CONDITIONAL",
    "APPLIED",
    "AWARDED",
    "COMMITTED_FINANCE",
)

#: The engine may only ever assert these. APPLIED, AWARDED and COMMITTED_FINANCE are
#: external project-status evidence and cannot be inferred from a match.
ENGINE_ASSERTABLE: Final = ("MATCHED", "ELIGIBLE", "CONDITIONAL")

#: Cross-engine caveats attached to the payload's narrative context, from
#: sample_report_payload.json.
CROSS_ENGINE_CAVEATS: Final = (
    "Do not add an opportunity to confirmed finance until awarded/contracted.",
    "Check stacking/cumulation where public support and incentives interact.",
)
