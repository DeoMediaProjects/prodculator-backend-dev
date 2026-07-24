"""Pure incentive / bankability scoring helpers for report building.

Extracted from ``builder.py`` (which had grown past 2,300 lines). These are
stateless, side-effect-free functions over an incentive ``db_row`` dict and
numeric inputs, so they live cleanly on their own and are unit-testable in
isolation. ``validator.py`` carries equivalent logic and can adopt these in a
follow-up to remove the duplication.
"""
from __future__ import annotations

import json

from app.modules.reports.helpers import to_float


# Source-quality tiers curated in territory_profiles that are trusted enough
# to drive the client-facing verdict directly. "unverified" is deliberately
# excluded — an unverified curated row is no more trustworthy than having no
# curated row at all, so it falls through to the legacy proxy below.
_TRUSTED_BANKABILITY_SOURCE_QUALITY = frozenset({
    "government_direct", "government_plus_industry", "industry_secondary",
})


def _compute_bankability_label(
    reliability: float | None,
    timeline_max: float | None,
    *,
    profile: dict | None = None,
) -> str:
    """Return BANKABLE / VERIFY FIRST / NOT BANKABLE per v3 spec.

    Prefers curated, human-verified territory_profiles bankability research
    (real government-sourced cert+payment timing) over the older
    incentive-row proxy signal (payment_reliability / payment_timeline_days)
    whenever a trusted profile exists for this territory. Falls back to the
    proxy for the many territories that have no curated research yet, or
    whose research is marked 'unverified' — so existing behaviour is
    unchanged wherever the curated data doesn't exist.
    """
    if profile:
        if profile.get("bankability_suspended"):
            return "NOT BANKABLE"
        if profile.get("bankability_real_world_confirms") is False:
            # Real-world evidence contradicts the stated policy (e.g. a
            # programme that is nominally active but factually not paying).
            return "NOT BANKABLE"

        source_quality = (profile.get("bankability_source_quality") or "").strip()
        if source_quality in _TRUSTED_BANKABILITY_SOURCE_QUALITY:
            cert_max = to_float(profile.get("cert_weeks_max"))
            pay_max = to_float(profile.get("payment_weeks_max"))
            if cert_max is not None or pay_max is not None:
                total_weeks = (cert_max or 0) + (pay_max or 0)
                if total_weeks > 52:
                    return "NOT BANKABLE"
                if total_weeks <= 26:
                    return "BANKABLE"
                return "VERIFY FIRST"

    # Legacy proxy signal — used when no trusted curated profile exists.
    if reliability is not None and reliability < 0.50:
        return "NOT BANKABLE"
    if timeline_max is not None and timeline_max > 365 and reliability is None:
        return "NOT BANKABLE"
    if reliability is not None and reliability >= 0.80:
        if timeline_max is None or timeline_max <= 180:
            return "BANKABLE"
    return "VERIFY FIRST"


def _incentive_rate_score(rate_gross: float) -> float:
    """Interpolate rateScore from system-prompt breakpoints."""
    _BP = [(0, 0), (20, 40), (30, 65), (40, 82), (53, 90), (100, 100)]
    if rate_gross <= 0:
        return 0.0
    for i in range(len(_BP) - 1):
        r0, s0 = _BP[i]
        r1, s1 = _BP[i + 1]
        if r0 <= rate_gross <= r1:
            t = (rate_gross - r0) / (r1 - r0)
            return s0 + t * (s1 - s0)
    return 100.0


def _incentive_qualification_score(db_row: dict) -> int:
    """Estimate qualification ease (0-100; higher = easier)."""
    nat_req = db_row.get("nationality_requirements")
    has_nat_req = False
    if nat_req:
        try:
            parsed = json.loads(nat_req) if isinstance(nat_req, str) else nat_req
            has_nat_req = bool(parsed)
        except (ValueError, TypeError):
            has_nat_req = True

    base = 40 if has_nat_req else 80

    rules_raw = db_row.get("eligibility_rules_json")
    n_mandatory = 0
    if rules_raw:
        try:
            rules = json.loads(rules_raw) if isinstance(rules_raw, str) else rules_raw
            if isinstance(rules, list):
                n_mandatory = sum(
                    1 for r in rules
                    if isinstance(r, dict) and r.get("required", True)
                )
        except (ValueError, TypeError):
            pass

    if n_mandatory >= 5:
        base -= 15
    elif n_mandatory >= 3:
        base -= 8

    if db_row.get("spv_eligible") is False:
        base -= 10

    return max(20, min(85, base))


def _incentive_stability_score(db_row: dict) -> int:
    """Estimate programme stability (0-100; higher = more stable)."""
    if (db_row.get("status") or "").lower() != "active":
        return 20

    warnings_raw = db_row.get("warnings_json")
    w_text = ""
    if warnings_raw:
        try:
            w = json.loads(warnings_raw) if isinstance(warnings_raw, str) else warnings_raw
            if isinstance(w, list):
                w_text = " ".join(str(x) for x in w).lower()
        except (ValueError, TypeError):
            pass

    _FROZEN_KW = (
        "frozen", "suspended", "no new", "halted", "operational",
        "dtic payment", "payment delays",
    )
    _CAUTION_KW = (
        "conditionally bankable", "registration queue", "backlog",
        "cap not yet set", "delayed into", "verify",
    )

    if any(kw in w_text for kw in _FROZEN_KW):
        return 20
    if any(kw in w_text for kw in _CAUTION_KW):
        return 45

    reliability = to_float(db_row.get("payment_reliability"))
    if reliability is not None:
        if reliability >= 0.85:
            return 90
        if reliability >= 0.70:
            return 70
    return 70
