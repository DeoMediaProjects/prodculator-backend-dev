"""One project-specific incentive status, for every section of the report.

The contradiction this exists to remove: a report told a producer the New York
programme was the strongest rate available, ranked New York first partly on that
strength, showed a waterfall subtracting the rebate, said the producer "qualifies
outright" — and then, in the tax-incentive section, said short-film eligibility for
that same programme was unverified.

Every one of those statements came from a different check. Eligibility was being
answered independently in at least four places:

    helpers.is_domestic_corp_only      producer / nationality structure
    programme_eligibility              minimum spend, budget ceiling, expiry, status
    format_eligibility                 does the programme accept this format
    readiness._confirmed_incentive     bankability, staleness, producer eligibility

Each was correct about its own dimension and silent about the others, so whichever
one a section happened to consult became that section's answer. That is what makes a
report disagree with itself.

This module combines them once, with a fixed precedence, and every consumer reads the
result rather than re-deriving it:

    hard failure                -> INELIGIBLE
    required dimension unknown  -> UNVERIFIED
    unresolved condition        -> CONDITIONAL
    otherwise                   -> ELIGIBLE

A producer being structurally qualified can never outvote an unknown format: a status
is only as strong as its weakest required dimension.
"""
from __future__ import annotations

from typing import Any

from app.modules.reports.format_eligibility import (
    ELIGIBLE as FORMAT_ELIGIBLE,
    INELIGIBLE as FORMAT_INELIGIBLE,
    NEEDS_CONFIRMATION as FORMAT_NEEDS_CONFIRMATION,
    UNVERIFIED as FORMAT_UNVERIFIED,
    evaluate_format_eligibility,
)
from app.modules.reports.helpers import needs_format_eligibility_check
from app.modules.reports.programme_eligibility import (
    AVAILABLE,
    UNAVAILABLE,
    evaluate_programme_eligibility,
)

# The four statuses every section reads.
ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
CONDITIONAL = "conditional"
UNVERIFIED = "unverified"

LABELS = {
    ELIGIBLE: "Eligible",
    INELIGIBLE: "Not available to this project",
    CONDITIONAL: "Conditional eligibility",
    UNVERIFIED: "Eligibility unverified",
}

# Only an outright eligible status may put money into a confirmed total, improve a
# ranking as realisable value, or be recommended as an action to spend against.
_CONFIRMABLE = frozenset({ELIGIBLE})


def resolve_project_incentive(
    row: dict,
    project: dict | None = None,
    *,
    production_format: str | None = None,
) -> dict[str, Any]:
    """Combine every required eligibility dimension into one project-specific status.

    ``row`` is an ``incentive_programs`` record; ``project`` carries the facts the
    gates test against (budget_gbp, runtime_minutes, completion_date, format).

    Returns a result object carrying the status, the reasons behind it, and three
    explicit permissions. The permissions exist so a consumer never has to re-derive
    the rule: a section that asks "may this affect the ranking?" gets an answer rather
    than an opportunity to disagree.
    """
    project = project or {}
    fmt = production_format or project.get("format")

    programme = evaluate_programme_eligibility(row, project)
    format_result = evaluate_format_eligibility(row, fmt, project)

    # Format is only a required dimension for formats whose eligibility genuinely
    # diverges from what these programmes are written for. A feature is not held back
    # by the absence of a record stating that features are accepted, which is true of
    # every programme in the dataset.
    format_is_required = needs_format_eligibility_check(fmt)

    reasons: list[str] = []
    hard_failures: list[str] = []
    unknowns: list[str] = []
    conditions: list[str] = []

    # ── Hard failures first, and they win outright ───────────────────────────
    # A budget below a programme's stated minimum is a known fact about this
    # project, not an open question. Once it fails, nothing downstream should
    # compute or present a rebate from it: a potential figure beside "not available
    # at this budget" is a contradiction the reader has to resolve for us.
    if programme["verdict"] == UNAVAILABLE:
        hard_failures.append(programme["explanation"] or programme["label"])
    if format_is_required and format_result["verdict"] == FORMAT_INELIGIBLE:
        hard_failures.append(format_result["explanation"] or format_result["label"])

    # ── Then anything required but unknown ───────────────────────────────────
    if programme["verdict"] not in (AVAILABLE, UNAVAILABLE):
        unknowns.append(programme["explanation"] or programme["label"])
    if format_is_required and format_result["verdict"] == FORMAT_UNVERIFIED:
        unknowns.append(format_result["explanation"] or format_result["label"])

    # ── Then unresolved conditions ───────────────────────────────────────────
    if format_is_required and format_result["verdict"] == FORMAT_NEEDS_CONFIRMATION:
        conditions.append(format_result["explanation"] or format_result["label"])

    if hard_failures:
        status = INELIGIBLE
        reasons = hard_failures
    elif unknowns:
        status = UNVERIFIED
        reasons = unknowns
    elif conditions:
        status = CONDITIONAL
        reasons = conditions
    else:
        status = ELIGIBLE
        reasons = [format_result["explanation"]] if format_is_required and format_result[
            "verdict"
        ] == FORMAT_ELIGIBLE else []

    confirmable = status in _CONFIRMABLE
    return {
        "status": status,
        "label": LABELS[status],
        "reasons": [r for r in reasons if r],
        # The dimensions, kept so a section can explain WHICH requirement is
        # unresolved rather than saying "unverified" and leaving the producer to
        # guess whether it is their company structure or the programme's rules.
        "formatStatus": format_result["verdict"],
        "formatIsRequired": format_is_required,
        "programmeStatus": programme["verdict"],
        "programmeReasons": programme.get("reasons", []),
        # Explicit permissions, so no consumer re-derives the rule and disagrees.
        "canAffectNetCost": confirmable,
        "canAffectRanking": confirmable,
        "canBeRecommended": confirmable,
        # A hard failure means there is no figure to show at all. An unresolved one
        # means the figure exists but only as an illustration.
        "showPotentialAmount": status in (UNVERIFIED, CONDITIONAL),
    }


def status_of(row: dict, project: dict | None = None, **kwargs) -> str:
    return resolve_project_incentive(row, project, **kwargs)["status"]
