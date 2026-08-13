"""Deterministic block on incentive figures for territories that have none.

FIX-06. The prompt now forbids the model from quoting a rate or a rebate amount
for a territory whose programme is suspended, absent, or verified as excluding
this production's format. A prompt rule is a request, not a guarantee, and the
failure mode here is the expensive kind: a producer reads "roughly 25% back" next
to a territory that will pay nothing, and builds a finance plan on it.

So the figures are removed after generation, deterministically, by reading the
same canonical report object every surface renders. Two rules:

  1. A territory the report itself marks as having no claimable incentive may not
     appear in narrative prose next to a percentage or a money amount.
  2. Everything else is left exactly alone. This guard has no opinion about
     unverified programmes: FORMAT_UNVERIFIED figures are legitimate, labelled
     illustrative elsewhere, and stripping them would be its own kind of lie.

Sentence-level rather than whole-field: a paragraph about South Africa's crew
depth and its suspended rebate loses the rebate sentence and keeps the crew one.
Anything stripped is recorded as a warning, because silent repair hides a
regression in the prompt.
"""
from __future__ import annotations

import re
from typing import Any

#: A number that reads as an incentive claim: "25%", "£9,329", "USD 13,124",
#: "$1M". Bare integers are deliberately NOT matched — "45 scenes", "4 weeks" and
#: "2026" are ordinary prose, and a guard that eats them would be turned off.
_PERCENT = r"\d[\d,.]*\s?%"
_CURRENCY = (
    r"(?:[£$€¥₦₹]|\b(?:GBP|USD|EUR|ZAR|CAD|AUD|NZD|NGN|INR|JPY|KRW|SGD|BRL|MXN|"
    r"MAD|RSD|RON|CZK|HUF|PLN|ILS)\b)\s?\d[\d,.]*\s?[KkMmBb]?"
)
FIGURE_RE = re.compile(f"(?:{_PERCENT}|{_CURRENCY})")

#: Split on sentence ends, keeping the terminator so rejoined prose reads right.
_SENTENCE_RE = re.compile(r"[^.!?]*[.!?]+|\s*[^.!?]+$")


def blocked_territories(report: dict) -> dict[str, str]:
    """Territories that may not carry an incentive figure, mapped to the reason.

    Read from the report rather than re-derived, so this cannot disagree with the
    ranking, the badge or the waterfall about which territories are blocked.
    """
    blocked: dict[str, str] = {}

    for loc in report.get("locationRankings") or []:
        if not isinstance(loc, dict):
            continue
        name = loc.get("name")
        if not name:
            continue
        if loc.get("hasNoBankableIncentive") is True:
            blocked[name] = loc.get("incentiveAvailability") or "no bankable incentive"

    for est in report.get("incentiveEstimates") or []:
        if not isinstance(est, dict):
            continue
        name = est.get("territory")
        if not name or name in blocked:
            continue
        fmt = est.get("formatEligibility") or {}
        if fmt.get("gateState") == "FORMAT_INELIGIBLE" or fmt.get("verdict") == "ineligible":
            blocked[name] = fmt.get("explanation") or "format not eligible"

    return blocked


def _strip_figures(text: str, territory: str) -> tuple[str, list[str]]:
    """Drop sentences that name *territory* and quote a figure.

    A sentence mentioning neither is untouched; a sentence mentioning the
    territory but no figure is untouched; a figure with no nearby territory
    mention is untouched, because it is almost certainly about a different
    territory in the same paragraph and removing it would corrupt a true claim.
    """
    removed: list[str] = []
    kept: list[str] = []
    for sentence in _SENTENCE_RE.findall(text):
        if not sentence.strip():
            continue
        if territory.lower() in sentence.lower() and FIGURE_RE.search(sentence):
            removed.append(sentence.strip())
            continue
        kept.append(sentence)
    return ("".join(kept).strip(), removed)


def _walk(value: Any, territory: str, removed: list[str]) -> Any:
    if isinstance(value, str):
        cleaned, dropped = _strip_figures(value, territory)
        removed.extend(dropped)
        return cleaned
    if isinstance(value, list):
        out = [_walk(v, territory, removed) for v in value]
        # A bullet reduced to nothing is dropped rather than left blank.
        return [v for v in out if not (isinstance(v, str) and not v.strip())]
    if isinstance(value, dict):
        return {k: _walk(v, territory, removed) for k, v in value.items()}
    return value


#: Narrative fields the model writes. Deterministic fields are never touched:
#: the report's own figures are correct and are what the guard trusts.
_NARRATIVE_PATHS: tuple[tuple[str, ...], ...] = (
    ("executiveSummary", "keyInsights"),
    ("executiveSummary", "keyFlags"),
    ("alternativeStrategy",),
    ("scriptIntelligence",),
    ("nextSteps",),
)


def scrub_report(report: dict) -> list[str]:
    """Remove fabricated incentive figures in place. Returns what was removed.

    A non-empty return is a prompt regression worth investigating, not routine
    housekeeping — under normal operation the model obeys the rule and this does
    nothing at all.
    """
    blocked = blocked_territories(report)
    if not blocked:
        return []

    warnings: list[str] = []

    for territory, reason in blocked.items():
        removed: list[str] = []

        for path in _NARRATIVE_PATHS:
            node: Any = report
            for key in path[:-1]:
                node = node.get(key) if isinstance(node, dict) else None
                if node is None:
                    break
            if not isinstance(node, dict):
                continue
            leaf = path[-1]
            if leaf not in node:
                continue
            node[leaf] = _walk(node[leaf], territory, removed)

        # Per-territory narrative lives on the ranking rows themselves.
        for loc in report.get("locationRankings") or []:
            if not isinstance(loc, dict) or loc.get("name") != territory:
                continue
            for field in ("reasoning", "keyAdvantages", "keyRisks"):
                if field in loc:
                    loc[field] = _walk(loc[field], territory, removed)

        for item in removed:
            warnings.append(
                f"[fabrication] removed an incentive figure stated for {territory}, "
                f"which has no claimable incentive ({reason.split('.')[0]}): "
                f"\"{item[:120]}\""
            )

    return warnings
