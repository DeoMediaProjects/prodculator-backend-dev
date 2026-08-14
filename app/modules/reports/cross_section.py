"""Sections that are each correct and together contradictory.

Every defect in the last review round had the same shape. No section was wrong on its
own terms; each pair disagreed:

    eligibility unverified, confirmed incentive £0    ...and Incentive Value 88, ranked first
    bankability badge BANKABLE                       ...and "should not be treated as investor-bankable"
    "Best months: March, April, May, June"           ...and an August shoot "falls within" it
    "VFX credit stacks ON TOP of AVEC (Enhanced)"    ...and "Cannot be combined with the VFX uplift"

Per-section assertions cannot catch any of those, because each section passed its own.
And prompt wording cannot fix them: the contradiction is between a computed field and
a sentence, so the only reliable check is to compare the two after both exist.

This module is that comparison. It runs on the assembled report immediately before
rendering, reads the canonical fields as the source of truth, and flags any narrative
that disagrees with them. Findings are returned as warnings rather than raised: a
producer is better served by a report that renders with a flagged inconsistency than
by no report at all, and a raise here would make one bad sentence in one territory
fail an otherwise sound analysis. Critical findings are logged at ERROR so they
surface in monitoring rather than only in the payload.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.modules.reports.shoot_window import (
    UNKNOWN as WINDOW_UNKNOWN,
    narrative_contradicts_window,
)
from app.modules.reports.stacking import statements_contradict

logger = logging.getLogger(__name__)

#: Prefix on every finding, so they are greppable in logs and distinguishable from
#: the validator's other warning families.
PREFIX = "[cross-section]"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _texts(*values: Any) -> list[str]:
    """Flatten narrative values of mixed shape into a list of strings."""
    out: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                out.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                out.extend(_texts(item))
        elif isinstance(value, dict):
            for item in value.values():
                out.extend(_texts(item))
    return out


def _joined(*values: Any) -> str:
    return " \n ".join(_texts(*values)).lower()


def _is_zero_money(text: Any) -> bool:
    """True only when a formatted money string is definitely zero: '£0', '$0', 'R0'.

    A string carrying no digits at all ("N/A", "See programme terms") is NOT zero — it
    is an absent figure, and reading it as zero would have this module reporting a
    contradiction about a number the report never claimed.
    """
    if text is None:
        return False
    digits = re.sub(r"[^\d]", "", str(text))
    if not digits:
        return False
    return int(digits) == 0


# ── Checks ───────────────────────────────────────────────────────────────────

_ELIGIBLE_CLAIMS = (
    "is eligible", "qualifies for", "the production qualifies",
    "confirmed eligible", "eligibility is confirmed", "has been confirmed eligible",
)

_SECURED_CLAIMS = (
    "secured", "guaranteed", "locked in", "confirmed rebate of",
    "will receive", "will be received", "is receivable",
)

_NOT_BANKABLE_CLAIMS = (
    "not investor-bankable", "not be treated as investor-bankable",
    "is not bankable", "not bankable", "cannot be banked",
    "should not be banked", "not financeable against",
)

_BANKABLE_CLAIMS = (
    "is bankable", "fully bankable", "lenders will advance",
    "can be banked", "bankable against",
)


def _check_eligibility_vs_narrative(report: dict, findings: list[str]) -> None:
    """An unverified or ineligible programme described as eligible or secured."""
    for est in report.get("incentiveEstimates") or []:
        if not isinstance(est, dict):
            continue
        territory = est.get("territory") or "?"
        fmt = est.get("formatEligibility") or {}
        verdict = (fmt.get("verdict") or "").lower()
        prose = _joined(
            est.get("eligibilityNote"), est.get("programmeNote"),
            est.get("stackingNote"), est.get("requirements"),
        )

        if verdict in ("unverified", "needs_confirmation", "ineligible"):
            for claim in _ELIGIBLE_CLAIMS:
                if claim in prose:
                    findings.append(
                        f"{PREFIX} {territory}: format eligibility is '{verdict}' but "
                        f"narrative asserts eligibility ('{claim}')"
                    )
                    break

        # A rebate that is not confirmed must not be described as money in hand.
        if est.get("rebateIsConfirmed") is False or est.get("rebateIsClaimable") is False:
            for claim in _SECURED_CLAIMS:
                if claim in prose:
                    findings.append(
                        f"{PREFIX} {territory}: rebate is not confirmed but narrative "
                        f"describes it as secured ('{claim}')"
                    )
                    break

        # Format ineligible, yet a claimable figure is still presented.
        if verdict == "ineligible" and est.get("rebateIsConfirmed") is True:
            findings.append(
                f"{PREFIX} {territory}: format eligibility is 'ineligible' but the "
                f"rebate is marked confirmed"
            )


def _check_confirmed_zero_vs_narrative(report: dict, findings: list[str]) -> None:
    """Confirmed incentive of zero, described as a benefit the production has."""
    financial = report.get("financialAnalysis") or {}
    scenarios = financial.get("budgetScenarios") or []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        territory = scenario.get("territory") or "?"
        confirmed = scenario.get("netRebate") or scenario.get("confirmedIncentive")
        if confirmed is None or not _is_zero_money(confirmed):
            continue
        prose = _joined(scenario.get("note"), scenario.get("caption"))
        for claim in _SECURED_CLAIMS:
            if claim in prose:
                findings.append(
                    f"{PREFIX} {territory}: confirmed incentive is {confirmed} but "
                    f"narrative describes it as secured ('{claim}')"
                )
                break


def _check_bankability_vs_narrative(report: dict, findings: list[str]) -> None:
    """The badge and the prose disagreeing about whether a lender will advance."""
    for loc in report.get("locationRankings") or []:
        if not isinstance(loc, dict):
            continue
        label = (loc.get("bankabilityLabel") or "").upper()
        if label not in ("BANKABLE", "VERIFY FIRST", "NOT BANKABLE"):
            continue
        name = loc.get("name") or loc.get("territory") or "?"
        prose = _joined(loc.get("keyRisks"), loc.get("keyAdvantages"), loc.get("reasoning"))

        if label == "BANKABLE":
            for claim in _NOT_BANKABLE_CLAIMS:
                if claim in prose:
                    findings.append(
                        f"{PREFIX} {name}: bankability is BANKABLE but narrative says "
                        f"'{claim}'"
                    )
                    break
        if label == "NOT BANKABLE":
            for claim in _BANKABLE_CLAIMS:
                if claim in prose and "not" not in prose[
                    max(0, prose.find(claim) - 12):prose.find(claim)
                ]:
                    findings.append(
                        f"{PREFIX} {name}: bankability is NOT BANKABLE but narrative "
                        f"says '{claim}'"
                    )
                    break


#: Weather fields written by the narrative model, and therefore the only ones worth
#: checking against the computed verdict. ``shootWindowRisk`` is deliberately excluded:
#: it is composed FROM the verdict by the builder, so testing it against the verdict is
#: circular. It also legitimately contains an inside-claim for a straddling shoot
#: ("September falls inside it, August does not"), which the phrase matcher would read
#: as a contradiction of an ADJACENT verdict when it is an accurate description of one.
_MODEL_WEATHER_FIELDS = ("seasonalConsiderations", "infrastructure")


def _check_shoot_window(report: dict, findings: list[str]) -> None:
    """A shoot month placed inside a window the classifier put it outside of."""
    for entry in report.get("weatherLogistics") or []:
        if not isinstance(entry, dict):
            continue
        verdict = entry.get("shootWindowVerdict")
        if not verdict or verdict == WINDOW_UNKNOWN:
            continue
        territory = entry.get("territory") or "?"
        for field in _MODEL_WEATHER_FIELDS:
            problem = narrative_contradicts_window(entry.get(field), verdict)
            if problem:
                findings.append(f"{PREFIX} {territory} ({field}): {problem}")


def _check_stacking(report: dict, findings: list[str]) -> None:
    """Prose contradicting the resolved stacking relationship for the same pair.

    Checked per pair, not per territory. The UK carries two true statements at once —
    the VFX uplift stacks with standard AVEC and does not stack with AVEC
    (Enhanced/IFTC) — so a check that merely noticed both claim shapes in one
    territory would fail correct data. What must never happen is the report asserting
    both directions about ONE pair, which is exactly what it did.
    """
    for est in report.get("incentiveEstimates") or []:
        if not isinstance(est, dict):
            continue
        relationship = est.get("stackingRelationship")
        if not relationship or relationship == "unknown":
            continue
        territory = (est.get("territory") or "?").strip()
        programme = est.get("program") or "?"
        partner = est.get("stacksWith") or "the primary incentive"

        # Only this estimate's own text describes this pair. Sibling estimates in the
        # territory describe their own pairings and are not evidence about this one.
        own_texts = _texts(
            est.get("stackingNote"), est.get("eligibilityNote"), est.get("requirements"),
        )
        if statements_contradict(own_texts):
            findings.append(
                f"{PREFIX} {territory}: text for {programme} with {partner} asserts "
                f"both that they stack and that they cannot be combined "
                f"(resolved relationship: {relationship})"
            )


def _check_must_film_in(report: dict, datasets: dict, findings: list[str]) -> None:
    """A stated hard location constraint absent from the recommendation."""
    must = (datasets.get("_must_film_in") or "").strip()
    if not must:
        return
    summary = report.get("executiveSummary") or {}
    prose = _joined(
        summary.get("keyInsights"), summary.get("keyFlags"),
        summary.get("recommendation"), report.get("alternativeStrategy"),
        report.get("nextSteps"),
    )
    if must.lower() not in prose:
        findings.append(
            f"{PREFIX} must-film-in territory '{must}' is not mentioned anywhere in "
            f"the executive summary, alternative strategy or next steps"
        )


def _check_metric_agreement(report: dict, findings: list[str]) -> None:
    """One metric with two values across sections.

    Compares the per-territory figures the Financial Analysis and the Tax Incentive
    Analysis each render for the same programme. They are built from one precomputed
    source, so a mismatch here means a renderer recomputed something.
    """
    scenarios = {
        (s.get("territory") or "").strip(): s
        for s in (report.get("financialAnalysis") or {}).get("budgetScenarios") or []
        if isinstance(s, dict)
    }
    for est in report.get("incentiveEstimates") or []:
        if not isinstance(est, dict):
            continue
        territory = (est.get("territory") or "").strip()
        scenario = scenarios.get(territory)
        if not scenario:
            continue
        est_total = est.get("totalBudget")
        scen_total = scenario.get("totalBudget")
        if est_total and scen_total and str(est_total).strip() != str(scen_total).strip():
            findings.append(
                f"{PREFIX} {territory}: total budget differs between Tax Incentive "
                f"Analysis ({est_total}) and Financial Analysis ({scen_total})"
            )


_CHECKS = (
    _check_eligibility_vs_narrative,
    _check_confirmed_zero_vs_narrative,
    _check_bankability_vs_narrative,
    _check_shoot_window,
    _check_stacking,
    _check_metric_agreement,
)


def validate_cross_section(report: dict, datasets: dict | None = None) -> list[str]:
    """Every contradiction between sections of *report*.

    Returns findings as strings. An empty list means every canonical field and the
    prose describing it agreed.
    """
    findings: list[str] = []
    if not isinstance(report, dict):
        return findings

    for check in _CHECKS:
        try:
            check(report, findings)
        except Exception as exc:  # pragma: no cover - a check must never fail a report
            logger.warning("Cross-section check %s failed: %s", check.__name__, exc)

    try:
        _check_must_film_in(report, datasets or {}, findings)
    except Exception as exc:  # pragma: no cover
        logger.warning("Cross-section must-film-in check failed: %s", exc)

    if findings:
        logger.error(
            "Cross-section contradictions found before render: count=%s findings=%s",
            len(findings), findings[:12],
        )
    return findings
