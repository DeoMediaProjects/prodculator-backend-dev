"""Is the shoot month inside the territory's optimal window: computed, not inferred.

The report printed "Best months: March, April, May, June" and, directly beneath it,
an LLM sentence claiming an August shoot "falls within the UK's optimal production
window of March through June". Two defects, and the order matters:

1. ``bestMonths`` was built by scanning January→December for acceptable months and
   then slicing ``[:4]``. That is not "the four best months", it is "the earliest
   four acceptable months". For the UK, months 3-9 all qualify on the seeded
   weather data; August was cut off by the slice despite being a perfectly good
   month (50mm rain, low storm risk, exterior score 85). South Africa is starker:
   qualifying months are 4-9 and August scores 93, the second-best month of its
   year, yet the slice printed "April, May, June, July".

2. Nothing compared the shoot month to that list in Python. The narrative field was
   marked ``# AI fills`` and the model was handed the already-truncated list, so it
   did what a reader would do — read four consecutive month names as a contiguous
   range and described the shoot relative to it.

So the model was not hallucinating a date range; it was faithfully describing a list
that had been silently truncated. Fixing the prose alone would have left the report
understating two territories' shoot windows. Both halves are fixed here: the window
is the full qualifying set, and membership is decided by this module rather than by
whatever the model infers from the rendered list.

Year-wrapping windows (a southern-hemisphere or dry-season window like Nov-Feb) are
handled as a set of month numbers rather than a start/end pair, because a range
comparison is exactly where wrap-around logic goes wrong.
"""
from __future__ import annotations

import calendar
from typing import Any

# ── Verdicts ─────────────────────────────────────────────────────────────────
# The narrative must consume one of these rather than deciding for itself.

INSIDE = "inside_optimal_window"
OUTSIDE = "outside_optimal_window"
ADJACENT = "adjacent_to_optimal_window"
UNKNOWN = "unknown"

VALID_VERDICTS = frozenset({INSIDE, OUTSIDE, ADJACENT, UNKNOWN})

#: Reader-facing phrasing per verdict. Held here so the PDF, the platform and any
#: prompt context all describe the same computed result in the same words, and so
#: no surface has to compose the sentence from the raw verdict token.
VERDICT_LABEL: dict[str, str] = {
    INSIDE: "within the optimal window",
    OUTSIDE: "outside the optimal window",
    ADJACENT: "immediately outside the optimal window",
    UNKNOWN: "against an optimal window that could not be determined",
}


def _month_numbers(months: Any) -> list[int]:
    """Month numbers from a list of ints or month names, sorted, deduplicated.

    ``bestMonths`` is rendered as names and consumed as names in places, so this
    accepts either and never raises on a stray value — an unparseable entry is
    dropped rather than turning the whole window into a failure.
    """
    if not months:
        return []
    by_name = {calendar.month_name[m].lower(): m for m in range(1, 13)}
    by_abbr = {calendar.month_abbr[m].lower(): m for m in range(1, 13)}
    out: set[int] = set()
    for raw in months:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            if 1 <= raw <= 12:
                out.add(raw)
            continue
        text = str(raw).strip().lower()
        if not text or text in ("n/a", "na", "none", "unknown"):
            continue
        if text.isdigit():
            value = int(text)
            if 1 <= value <= 12:
                out.add(value)
            continue
        if text in by_name:
            out.add(by_name[text])
        elif text in by_abbr:
            out.add(by_abbr[text])
    return sorted(out)


def _is_adjacent(month: int, window: set[int]) -> bool:
    """True when *month* sits immediately either side of *window*.

    Modular arithmetic, so December is adjacent to a January window and the
    Nov-Feb case needs no special handling.
    """
    before = 12 if month - 1 == 0 else month - 1
    after = 1 if month + 1 == 13 else month + 1
    return before in window or after in window


def classify_shoot_window(
    shoot_months: Any,
    best_months: Any,
) -> dict[str, Any]:
    """Where the shoot sits relative to the territory's optimal window.

    Returns a dict carrying the verdict, the month sets it was decided from, and a
    ready-made phrase, so every surface renders one computed answer instead of
    re-deriving it. ``partialOverlap`` is set when a multi-month shoot straddles the
    window boundary: such a shoot is reported as ADJACENT rather than INSIDE,
    because "some of your shoot is in the good window" must not be rendered as
    "your shoot is in the good window".
    """
    shoot = _month_numbers(shoot_months)
    window = _month_numbers(best_months)

    result: dict[str, Any] = {
        "verdict": UNKNOWN,
        "label": VERDICT_LABEL[UNKNOWN],
        "shootMonths": shoot,
        "optimalMonths": window,
        "monthsInside": [],
        "monthsOutside": [],
        "partialOverlap": False,
        "optimalWindowDisplay": format_month_ranges(window) if window else None,
        "shootMonthDisplay": format_month_ranges(shoot) if shoot else None,
    }

    # No window, or no shoot date: there is nothing to compare. Saying so beats
    # defaulting to either answer, both of which read as a finding.
    if not shoot or not window:
        return result

    window_set = set(window)
    inside = [m for m in shoot if m in window_set]
    outside = [m for m in shoot if m not in window_set]
    result["monthsInside"] = inside
    result["monthsOutside"] = outside

    if not outside:
        result["verdict"] = INSIDE
    elif inside:
        result["verdict"] = ADJACENT
        result["partialOverlap"] = True
    elif any(_is_adjacent(m, window_set) for m in outside):
        result["verdict"] = ADJACENT
    else:
        result["verdict"] = OUTSIDE

    result["label"] = VERDICT_LABEL[result["verdict"]]
    return result


def format_month_ranges(months: Any) -> str:
    """Month numbers as reader-facing text, contiguous runs collapsed to ranges.

    ``[3,4,5,6,7,8,9]`` renders "March to September" rather than seven names, which
    is what made truncating the list tempting in the first place. Wrap-around is
    deliberately NOT collapsed across the year boundary — "November to February"
    reads as four months to a producer, but collapsing it would require ordering
    the list from November, and a list that does not start in January is a second
    thing every consumer has to know about.
    """
    values = _month_numbers(months)
    if not values:
        return ""
    runs: list[list[int]] = [[values[0]]]
    for month in values[1:]:
        if month == runs[-1][-1] + 1:
            runs[-1].append(month)
        else:
            runs.append([month])

    parts: list[str] = []
    for run in runs:
        if len(run) == 1:
            parts.append(calendar.month_name[run[0]])
        elif len(run) == 2:
            parts.append(
                f"{calendar.month_name[run[0]]} and {calendar.month_name[run[1]]}"
            )
        else:
            parts.append(
                f"{calendar.month_name[run[0]]} to {calendar.month_name[run[-1]]}"
            )
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f", plus {parts[-1]}"


# ── Contradiction detection ──────────────────────────────────────────────────
# Used by the cross-section validator. Kept beside the classifier so the phrases
# that count as an inside-claim live next to the rule that decides the truth.

_INSIDE_CLAIMS = (
    "falls within",
    "falls inside",
    "is within",
    "sits within",
    "lies within",
    "within the optimal",
    "within the recommended",
    "inside the optimal",
    "in the optimal window",
    "within the ideal",
    "coincides with the optimal",
    "aligns with the optimal",
)

_OUTSIDE_CLAIMS = (
    "falls outside",
    "is outside",
    "sits outside",
    "lies outside",
    "outside the optimal",
    "outside the recommended",
    "outside the ideal",
)


def narrative_contradicts_window(text: str | None, verdict: str | None) -> str | None:
    """The contradiction, described, or None when the prose agrees with the verdict.

    Deliberately one-directional per verdict: an INSIDE shoot described as outside is
    just as wrong as the reverse, and both are caught, but a shoot the classifier
    called ADJACENT may legitimately be described either as outside the window or as
    bordering it, so only an inside-claim is rejected there.
    """
    if not text or verdict not in VALID_VERDICTS or verdict == UNKNOWN:
        return None
    lowered = text.lower()

    claims_inside = any(phrase in lowered for phrase in _INSIDE_CLAIMS)
    claims_outside = any(phrase in lowered for phrase in _OUTSIDE_CLAIMS)

    if verdict == INSIDE and claims_outside and not claims_inside:
        return (
            "narrative places the shoot outside the optimal window, but every shoot "
            "month is inside it"
        )
    if verdict in (OUTSIDE, ADJACENT) and claims_inside and not claims_outside:
        return (
            f"narrative places the shoot inside the optimal window, but the computed "
            f"verdict is {verdict}"
        )
    return None
