"""Can this production use this programme at all: one gate, every surface.

Eligibility facts were recorded on ``incentive_programs``, formatted into display
strings, and never compared to the production. ``qualifying_spend_min`` is the clearest
case: six places in the codebase read it, five of them only to render a label like
"$1M minimum", and the sixth (readiness) compares it for the single recommended
territory. So California's stated $1,000,000 floor was printed one line above a
$61,780 qualifying spend, on a report that ranked California second and quoted it an
$18,380 rebate. 17 of 49 programmes were unreachable at that budget and every one of
them was still ranked, carded and quoted.

That was not a California problem. The gates that did exist had each been added where
somebody noticed a specific bug, in three different modules with three different
mechanisms: nationality inside ``best_incentive``, budget ceiling inside the
calculator's tier-switching, expiry inside readiness's confirmation criteria. Nothing
asked the general question, so the next missing gate was always going to slip through.

This module asks it once. Three verdicts, and the middle one is the point:

    AVAILABLE      every gate the programme states is satisfied
    UNAVAILABLE    a stated gate is definitively not met
    UNVERIFIABLE   a gate is stated but cannot be evaluated from what we hold

UNVERIFIABLE MUST NOT BE TREATED AS AVAILABLE. Two programmes state a minimum spend
in a currency no GBP rate is held for (Morocco MAD, Serbia RSD). Passing them because
the comparison failed is how a gate becomes decorative.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

AVAILABLE = "available"
UNAVAILABLE = "unavailable"
UNVERIFIABLE = "unverifiable"

LABELS = {
    AVAILABLE: "Available at this budget",
    UNAVAILABLE: "Not available at this budget",
    UNVERIFIABLE: "Availability unverified",
}

# Ranking order, so a programme this production can actually use outranks one it
# cannot, whatever the headline rate says. Same shape as the format-eligibility
# ranking, so the two axes combine by taking the worse of the pair.
_VERDICT_RANK = {UNAVAILABLE: 0, UNVERIFIABLE: 1, AVAILABLE: 2}

BLOCKING_VERDICTS = frozenset({UNAVAILABLE, UNVERIFIABLE})


def verdict_rank(verdict: str | None) -> int:
    return _VERDICT_RANK.get(verdict or UNVERIFIABLE, 1)


# ── Gate outcomes ────────────────────────────────────────────────────────────
PASS, FAIL, UNTESTED = "pass", "fail", "untested"


def _gate(name: str, outcome: str, detail: str) -> dict[str, str]:
    return {"gate": name, "outcome": outcome, "detail": detail}


def _money(currency: str, amount: float) -> str:
    return f"{currency} {amount:,.0f}"


# ── Minimum qualifying spend ─────────────────────────────────────────────────

def _check_minimum_spend(row: dict, project: dict) -> dict[str, str] | None:
    """The floor a production must clear to qualify at all."""
    raw = row.get("qualifying_spend_min")
    try:
        minimum = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        minimum = None
    if not minimum or minimum <= 0:
        return None  # states no floor, nothing to test

    currency = (
        row.get("qualifying_spend_currency") or row.get("currency") or "GBP"
    ).upper()

    # Compared against the whole budget, not the territory's qualifying spend.
    # Qualifying spend is always a subset of budget, so using the budget is the
    # generous direction: it can only ever fail a programme that the stricter
    # comparison would also fail. Understating exclusions beats inventing them.
    budget_gbp = _to_float(project.get("budget_gbp"))

    from app.modules.reports.readiness import _to_gbp

    minimum_gbp, basis = _to_gbp(minimum, currency)

    if minimum_gbp is None:
        return _gate(
            "minimum qualifying spend", UNTESTED,
            f"This programme states a {_money(currency, minimum)} minimum qualifying "
            f"spend, but no GBP rate is held for {currency}, so it could not be "
            f"compared to this budget. Confirm the threshold with the programme directly.",
        )
    if budget_gbp is None:
        return _gate(
            "minimum qualifying spend", UNTESTED,
            f"This programme states a minimum qualifying spend of "
            f"{_money(currency, minimum)}, which could not be tested because no "
            f"normalised budget is held for this production.",
        )
    if budget_gbp < minimum_gbp:
        shortfall = minimum_gbp - budget_gbp
        return _gate(
            "minimum qualifying spend", FAIL,
            f"This programme requires a minimum qualifying spend of "
            f"{_money(currency, minimum)} (£{minimum_gbp:,.0f}; {basis}). This "
            f"production's budget of £{budget_gbp:,.0f} is £{shortfall:,.0f} below "
            f"that floor, so the programme is not available to it at this budget.",
        )
    return _gate(
        "minimum qualifying spend", PASS,
        f"Budget of £{budget_gbp:,.0f} clears this programme's "
        f"{_money(currency, minimum)} minimum qualifying spend.",
    )


# ── Budget eligibility ceiling ───────────────────────────────────────────────
# Free text on the row, e.g. "GBP 23,500,000 total core expenditure - ABOVE THIS,
# IFTC IS NOT AVAILABLE AT ALL, must use AVEC instead". Parsed rather than
# hand-mapped, because a hand-mapped ceiling is another gate that only covers the
# rows somebody remembered.
_CEILING_RE = re.compile(
    r"\b(GBP|USD|EUR|CAD|AUD|ZAR|NZD|JPY|KRW|INR|MAD|RSD|RON|CZK|HUF|PLN|SGD|ILS|MXN|BRL)?"
    r"\s*[£$€]?\s*([\d][\d,\.]{3,})",
    re.I,
)


def _stated_ceiling(text: str) -> tuple[float, str] | None:
    match = _CEILING_RE.search(text)
    if not match:
        return None
    currency = (match.group(1) or "GBP").upper()
    try:
        return float(match.group(2).replace(",", "")), currency
    except ValueError:
        return None


def _check_budget_ceiling(row: dict, project: dict) -> dict[str, str] | None:
    """The ceiling above which a programme is not available at all.

    This existed only on the calculator path, as tier-switching with a refusal
    reason. The report path never applied it.
    """
    text = (row.get("budget_eligibility_ceiling") or "").strip()
    if not text:
        return None

    parsed = _stated_ceiling(text)
    if parsed is None:
        return _gate(
            "budget eligibility ceiling", UNTESTED,
            f"This programme states a budget eligibility ceiling that could not be "
            f"read as an amount: “{text}”. Confirm it with the programme directly.",
        )
    ceiling, currency = parsed
    budget_gbp = _to_float(project.get("budget_gbp"))

    from app.modules.reports.readiness import _to_gbp

    ceiling_gbp, basis = _to_gbp(ceiling, currency)
    if ceiling_gbp is None or budget_gbp is None:
        return _gate(
            "budget eligibility ceiling", UNTESTED,
            f"This programme's stated ceiling of {_money(currency, ceiling)} could "
            f"not be compared to this production's budget.",
        )
    if budget_gbp > ceiling_gbp:
        return _gate(
            "budget eligibility ceiling", FAIL,
            f"This production's budget of £{budget_gbp:,.0f} is above the programme's "
            f"eligibility ceiling of {_money(currency, ceiling)} "
            f"(£{ceiling_gbp:,.0f}; {basis}), above which it is not available. "
            f"Recorded terms: “{text}”.",
        )
    return _gate(
        "budget eligibility ceiling", PASS,
        f"Budget of £{budget_gbp:,.0f} is within the programme's "
        f"{_money(currency, ceiling)} eligibility ceiling.",
    )


# ── Expiry ───────────────────────────────────────────────────────────────────

def _check_expiry(row: dict, project: dict) -> dict[str, str] | None:
    """A programme that closes before this production can claim against it.

    Latent rather than live: no row carries an expiry date today. The column exists
    and the scraper populates it, so the gate goes in now rather than after the
    first programme sunsets.
    """
    from app.modules.reports.readiness import _parse_date

    expiry = _parse_date(row.get("expiry_date"))
    if expiry is None:
        return None

    horizon = _parse_date(project.get("completion_date")) or _parse_date(
        project.get("filming_start_date")
    )
    if horizon is None:
        return _gate(
            "programme expiry", UNTESTED,
            f"This programme expires on {expiry.isoformat()}, which could not be "
            f"tested because no completion date is held for this production.",
        )
    if expiry < horizon:
        return _gate(
            "programme expiry", FAIL,
            f"This programme expires on {expiry.isoformat()}, before this "
            f"production's {horizon.isoformat()} completion date.",
        )
    return _gate(
        "programme expiry", PASS,
        f"This programme runs to {expiry.isoformat()}, past this production's "
        f"{horizon.isoformat()} completion date.",
    )


# ── Status ───────────────────────────────────────────────────────────────────

def _check_status(row: dict, project: dict) -> dict[str, str] | None:
    """A suspended or withdrawn programme is not available, whatever its rate.

    'active' and blank both mean active, matching the rest of the codebase. Anything
    else is a programme whose availability we cannot stand behind today.
    """
    status = (row.get("status") or "").strip().lower()
    if status in ("", "active"):
        return None
    if status == "no_programme":
        return _gate(
            "programme status", FAIL,
            "There is no incentive programme on record for this territory.",
        )
    return _gate(
        "programme status", FAIL,
        f"This programme's status is recorded as “{status}”, so it cannot be "
        f"treated as available to a production planning now.",
    )


_GATES = (_check_status, _check_minimum_spend, _check_budget_ceiling, _check_expiry)


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def evaluate_programme_eligibility(
    row: dict,
    project: dict | None = None,
) -> dict[str, Any]:
    """Whether *row* is a programme this production can actually use.

    Returns one object that every surface renders, so the report, the PDF and the
    ranking cannot disagree. ``reasons`` carries each gate that had something to say,
    in the order tested, with the arithmetic spelled out: a producer has to be able
    to check the exclusion, not just be told about it.
    """
    project = project or {}
    reasons = [g for check in _GATES if (g := check(row, project)) is not None]

    failed = [r for r in reasons if r["outcome"] == FAIL]
    untested = [r for r in reasons if r["outcome"] == UNTESTED]

    if failed:
        verdict = UNAVAILABLE
    elif untested:
        verdict = UNVERIFIABLE
    else:
        # Includes the common case of a programme that states no gates at all. It
        # has nothing left to fail, so it is available on these grounds. Format
        # eligibility is a separate axis and is evaluated separately.
        verdict = AVAILABLE

    # The producer reads the blocking reason first. A passed gate is reassurance,
    # not news, so it comes after.
    primary = failed[0] if failed else (untested[0] if untested else None)

    return {
        "verdict": verdict,
        "label": LABELS[verdict],
        "available": verdict == AVAILABLE,
        # A figure may only be presented as an amount this production can count on
        # when nothing is blocking and nothing is untested.
        "rebateIsClaimable": verdict == AVAILABLE,
        "explanation": primary["detail"] if primary else None,
        "reasons": reasons,
        "failedGates": [r["gate"] for r in failed],
        "untestedGates": [r["gate"] for r in untested],
    }


def any_unavailable(rows: list[dict], project: dict | None = None) -> bool:
    """True when at least one programme cannot be confirmed as usable.

    Drives the blanket caveat, so it retires itself once every programme in a report
    clears its own gates rather than needing a code change.
    """
    for row in rows or []:
        verdict = evaluate_programme_eligibility(row, project)["verdict"]
        if verdict in BLOCKING_VERDICTS:
            return True
    return False
