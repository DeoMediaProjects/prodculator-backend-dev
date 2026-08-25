"""Shared pure functions for the reports pipeline.

Used by both ``ReportBuilder`` (deterministic skeleton assembly) and
``ReportValidator`` (post-processing assertions).  Extracted from
``validator.py`` to avoid circular imports and make dependencies explicit.
"""
from __future__ import annotations

import json as _json
import re as _re
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

#: Statutory qualifying-spend engines, from the Incentive Engine v2 specification.
#: Stored on the programme record as ``qs_engine_type``. NULL means the record has
#: not been migrated to a v2 engine yet and keeps its pre-v2 behaviour, so this
#: field can be populated programme by programme without a flag day.
QS_ENGINE_TYPES = frozenset({
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
})

#: Mechanisms that are not an entitlement, and therefore must never produce a
#: rebate figure.
#:
#: An investor tax shelter returns value to a third-party investor through the tax
#: system; a competitive grant is awarded at a committee's discretion; a
#: no-programme record exists to record the absence of one. None of the three is
#: a percentage a production can claim against its spend, yet each carried a
#: headline rate that the engine was multiplying out: Belgium 42%, Singapore 40%
#: and Mexico EFICINE 10% were being presented with the same authority as a UK
#: statutory credit.
#:
#: A potential award may still be modelled for these mechanisms, but only from
#: their own official rules and never as a rate applied to production spend. That
#: is the v2 result contract's job; until it exists, no figure is the honest
#: output.
NON_ENTITLEMENT_ENGINES = frozenset({
    "INVESTOR_TAX_SHELTER",
    "COMPETITIVE_GRANT",
    "NO_PROGRAMME",
})

#: Why no figure is shown, keyed by engine. Surfaced to the reader so a missing
#: number reads as a statement about the mechanism rather than missing data.
MECHANISM_NO_FIGURE_REASON: dict[str, str] = {
    "INVESTOR_TAX_SHELTER": (
        "This programme is an investor tax shelter, not a production rebate. The "
        "benefit is delivered to an investor through the tax system, so the "
        "headline percentage is not a rate this production can claim against its "
        "spend. Model the investment structure with a tax adviser."
    ),
    "COMPETITIVE_GRANT": (
        "This programme is a competitive award decided by the awarding body, not "
        "an entitlement. No amount can be calculated from production spend, and "
        "any published maximum is a ceiling on what could be granted rather than "
        "a figure to budget against."
    ),
    "NO_PROGRAMME": (
        "No claimable production incentive programme is recorded for this "
        "territory. The record exists to state that absence rather than to imply "
        "an unverified opportunity."
    ),
}


def non_entitlement_mechanism(row: Any) -> bool:
    """True when this programme's mechanism forbids a calculated rebate figure."""
    if not isinstance(row, dict):
        return False
    engine = str(row.get("qs_engine_type") or "").strip().upper()
    return engine in NON_ENTITLEMENT_ENGINES


def mechanism_no_figure_reason(row: Any) -> str | None:
    """The reader-facing explanation for a suppressed figure, or None."""
    if not isinstance(row, dict):
        return None
    engine = str(row.get("qs_engine_type") or "").strip().upper()
    return MECHANISM_NO_FIGURE_REASON.get(engine)


# Data freshness threshold — flag incentives older than this many days.
#
# Was 365, which is longer than the interval between fiscal events that change
# these rates: UK data verified 2026-03-27 would not have flagged until March
# 2027, so a rate could go stale for a full year with the report saying nothing.
# 180 days is roughly two budget cycles and is the threshold the owner chose.
STALE_DAYS = 180

# Default ATL (above-the-line) deduction percentage.  Tax credit programmes
# exclude above-the-line costs from qualifying spend — 15% of total budget
# is a standard conservative assumption used in UK/EU tax credit modelling.
DEFAULT_ATL_PCT = 0.15

# rate_type values that trigger automatic ATL deduction.
# "refundable_tax_credit" is included because refundability describes payment
# mechanics (cash returned vs. tax liability offset) — not qualifying spend
# rules.  California Program 4.0 and New Mexico are refundable credits that
# still categorically exclude ATL costs from qualified expenditures by statute
# (R&TC § 17053.98(b)(21)(B)).  Omitting this type caused California to compute
# rebates on 100% of budget rather than the BTL-only qualifying base.
TAX_CREDIT_RATE_TYPES = {"tax_credit", "enhanced_tax_credit", "refundable_tax_credit"}

# Fallback FX rates (GBP→X) when live rates are unavailable
STATIC_FX_TO_GBP: dict[str, float] = {
    "ZAR": 23.8,
    "AUD": 1.95,
    "USD": 1.27,
    "EUR": 1.17,
    "CAD": 1.75,
    "NZD": 2.10,
    "HUF": 480.0,
    "CZK": 29.5,
    "INR": 106.0,
    "KRW": 1680.0,
    "JPY": 192.0,
    "SGD": 1.72,
    "RON": 5.85,
}

# Terminal bankability labels that should not be overwritten
TERMINAL_LABELS = frozenset({"NOT APPLICABLE", "INFORMATIONAL"})


# ── Indexing helpers ─────────────────────────────────────────────────────────

def prog_name(row: dict) -> str:
    """Return the programme name from a row, checking both DB and test keys."""
    return row.get("program_name") or row.get("program") or ""


def index_incentives(incentives: list[dict]) -> dict[str, dict]:
    """Index incentive rows by programme name (case-insensitive).

    Checks both ``program_name`` (used in tests / AI output) and ``program``
    (the actual DB column name) so the index works in all environments.
    """
    result: dict[str, dict] = {}
    for row in incentives:
        if not isinstance(row, dict):
            continue
        name = row.get("program_name") or row.get("program")
        if name:
            result[name] = row
            result[name.lower()] = row
    return result


def index_incentives_by_territory(incentives: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for row in incentives:
        if not isinstance(row, dict):
            continue
        t = row.get("territory") or ""
        result.setdefault(t, []).append(row)
    return result


def is_domestic_corp_only(r: dict) -> bool:
    """True only when nationality_requirements is non-empty AND spv_eligible is False.

    Used to exclude programmes restricted to domestic corporations (e.g. BC FIBC,
    Canada CPTC) from being recommended to or stacked with foreign productions.
    The ``spv_eligible is False`` guard ensures SPV-friendly programmes like UK AVEC
    (which have no nationality restriction) are never incorrectly excluded.
    """
    nr = r.get("nationality_requirements")
    if nr is None:
        return False
    if isinstance(nr, str):
        try:
            nr = _json.loads(nr)
        except (ValueError, TypeError):
            return False
    return isinstance(nr, list) and bool(nr) and r.get("spv_eligible") is False


# Formats whose incentive eligibility is materially different from a feature and
# is NOT recorded anywhere in the programme data. ``applicable_formats`` exists on
# incentive_programs and is read by ``best_incentive``, but it is NULL on every
# row, and NULL means "applies to all formats". So a short film is currently
# modelled against every programme as though each one accepted shorts.
#
# Short-form work is commonly excluded from production tax credits altogether and
# supported instead by separate, smaller grant schemes with their own criteria.
# Quoting a feature-scale rebate against a short without saying so overstates what
# the production can claim, so the report carries the caveat until the eligibility
# data exists to replace it.
FORMATS_NEEDING_ELIGIBILITY_CHECK: frozenset[str] = frozenset({
    "short",
    "short film",
})


def needs_format_eligibility_check(production_format: str | None) -> bool:
    """True when *production_format* is one the programme data cannot vouch for."""
    if not production_format:
        return False
    return production_format.strip().lower() in FORMATS_NEEDING_ELIGIBILITY_CHECK


def format_eligibility_is_recorded(rows: list[dict]) -> bool:
    """True when any row actually states which formats it applies to.

    Guards the caveat against becoming permanent furniture: once the eligibility
    data is populated, this returns True and the report can rely on the filtering
    in ``best_incentive`` instead of a blanket warning.
    """
    for r in rows or []:
        af = r.get("applicable_formats")
        if af is None:
            continue
        if isinstance(af, str):
            try:
                af = _json.loads(af)
            except (ValueError, TypeError):
                continue
        if isinstance(af, list) and af:
            return True
    return False


def best_incentive(
    rows: list[dict],
    production_format: str | None = None,
    project: dict | None = None,
) -> dict:
    """Pick the best incentive row, preferring dependable eligibility over rate.

    When *production_format* is given, each row is evaluated by
    ``evaluate_format_eligibility``. Rows a verified whitelist excludes are dropped.
    Among what remains, the most dependable verdict wins first and rate decides only
    within that group, so a programme whose format eligibility is unverified can
    never beat a verified one on the strength of a number nobody checked.

    *project* carries fields a ``conditional`` programme's rule may reference (for
    example ``runtime_minutes``). Without it, a condition that cannot be settled
    reports as needing confirmation rather than being assumed either way.

    Rows that require a domestic corporation (``nationality_requirements`` is a
    non-empty array AND ``spv_eligible`` is explicitly ``False``) are treated as
    lower-priority than universally-accessible rows.  When at least one
    universally-accessible row exists, domestic-corp-only rows are excluded
    before ranking.  This prevents programmes like Canada CPTC (restricted to
    Canadian-controlled corporations) from being selected over PSTC (accessible
    to any foreign production via a Canadian entity).  Rows that allow SPV
    structures (``spv_eligible=True``) such as UK AVEC are NOT excluded.
    """

    def _key(r: dict) -> float:
        rate = r.get("rate_gross") or r.get("rate_net") or 0
        try:
            return float(rate)
        except (TypeError, ValueError):
            return 0.0

    eligible = rows
    if production_format:
        # Format eligibility is decided per programme by evaluate_format_eligibility,
        # which knows the difference between "this whitelist excludes shorts" and
        # "nobody has checked". The old rule here treated NULL as "all formats", so
        # an unchecked programme was indistinguishable from a verified one and could
        # win on rate alone.
        from app.modules.reports.format_eligibility import (
            INELIGIBLE,
            evaluate_format_eligibility,
            verdict_rank,
        )

        verdicts = {
            id(r): evaluate_format_eligibility(r, production_format, project)
            for r in rows
        }

        # A verified exclusion is a real answer: drop those outright. Anything else
        # stays a candidate, because a programme whose eligibility is merely
        # unverified may well qualify and hiding it would understate the options.
        permitted = [r for r in rows if verdicts[id(r)]["verdict"] != INELIGIBLE]
        if permitted:
            eligible = permitted
        # else: every programme is verified-ineligible for this format. The caller
        # still gets a row so nothing crashes, but the verdict on it says ineligible,
        # so no surface can present it as an available incentive.

        # Prefer dependability over headline rate: a confirmed programme outranks an
        # unverified one even when the unverified one computes a larger rebate. That
        # inversion is the whole point — the larger number was never checked.
        best_rank = max(verdict_rank(verdicts[id(r)]["verdict"]) for r in eligible)
        eligible = [
            r for r in eligible
            if verdict_rank(verdicts[id(r)]["verdict"]) == best_rank
        ]

    # Prefer a programme this production can actually use over one it cannot.
    #
    # This band sits ABOVE format eligibility and rate, because it answers a blunter
    # question: whether the production clears the programme's own stated thresholds
    # at all. Before this, `qualifying_spend_min` was formatted into a display string
    # in three separate modules and compared in none of them, so a programme whose
    # floor was 17x the entire budget could be selected on the strength of its rate.
    #
    # Nothing is dropped, only demoted. The row still reaches the caller carrying its
    # verdict, so a territory with one unusable programme keeps its place in the
    # report and states why rather than vanishing from it.
    from app.modules.reports.programme_eligibility import (
        evaluate_programme_eligibility,
        verdict_rank as programme_rank,
    )

    availability = {id(r): evaluate_programme_eligibility(r, project) for r in eligible}
    best_availability = max(
        programme_rank(availability[id(r)]["verdict"]) for r in eligible
    )
    usable = [
        r for r in eligible
        if programme_rank(availability[id(r)]["verdict"]) == best_availability
    ]
    if usable:
        eligible = usable

    # Prefer universally-accessible rows over domestic-corp-only rows.
    foreign_accessible = [r for r in eligible if not is_domestic_corp_only(r)]
    if foreign_accessible:
        eligible = foreign_accessible
    # else: all rows require domestic corp → fall back to full set (graceful degradation)

    # Prefer primary incentives over supplementary credits (e.g. UK VFX Expenditure
    # Credit is supplementary to AVEC — it applies only to VFX spend and should never
    # be selected as the main production incentive).
    primary = [r for r in eligible if not r.get("is_supplementary")]
    if primary:
        eligible = primary
    # else: all rows are supplementary → fall back to full set (graceful degradation)

    return max(eligible, key=_key)


# ── Formatting helpers ───────────────────────────────────────────────────────

def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_zero_rate(rate_gross: Any, rate_net: Any) -> bool:
    gross = to_float(rate_gross)
    net = to_float(rate_net)
    g_zero = gross is None or gross == 0
    n_zero = net is None or net == 0
    return g_zero and n_zero


def format_rate(rate_gross: Any, rate_net: Any) -> str | None:
    gross = to_float(rate_gross)
    net = to_float(rate_net)
    if net is not None and net > 0 and gross is not None and gross > 0:
        if abs(gross - net) < 0.1:
            return f"{net:g}%"
        return f"{net:g}% net ({gross:g}% gross)"
    if net is not None and net > 0:
        return f"{net:g}% net"
    if gross is not None and gross > 0:
        return f"{gross:g}% gross"
    return None


#: Cap labels that assert an absence rather than describing a ceiling. These come
#: straight from the v4 source rows ("No cap", "No cap identified", "No flat cap
#: identified — interim payments available once ..."), and a display chain that
#: passes them through states something the engine cannot stand behind: UK AVEC
#: showed "No cap" while the 80% core-expenditure restriction was being applied
#: to its qualifying spend. Only "no formal cap" was filtered before.
#:
#: Matched as a prefix on the normalised string so trailing qualifications are
#: caught too. A row whose only cap information is one of these yields no cap
#: label at all, which lets the qualifying-spend cap below be stated instead.
_VACUOUS_CAP_PREFIXES = (
    "no cap",
    "no formal cap",
    "no flat cap",
    "no per-project cap",
    "none",
    "n/a",
    "uncapped",
    "not stated",
    "no ceiling",
)


def is_vacuous_cap_label(text: Any) -> bool:
    """True when a DB cap label asserts no cap rather than describing one."""
    if not text:
        return True
    normalised = str(text).strip().lower()
    if not normalised:
        return True
    return normalised.startswith(_VACUOUS_CAP_PREFIXES)


def format_qualifying_spend_cap(
    cap_pct: Any,
    cap_amount: Any = None,
    cap_currency: str = "GBP",
) -> str | None:
    """Describe a qualifying-spend restriction for display.

    ``qualifying_spend_cap_pct`` was applied to the money in exactly one place and
    rendered in none, so an 80% reduction reached the producer's waterfall with no
    statement anywhere that it had happened. This is the canonical label for it.
    """
    pct = to_float(cap_pct)
    parts: list[str] = []
    if pct is not None and 0 < pct < 100:
        parts.append(f"{pct:g}% of core expenditure qualifies")
    amount = to_float(cap_amount)
    if amount is not None and amount > 0:
        formatted = format_cap(amount, cap_currency)
        if formatted:
            parts.append(f"qualifying spend capped at {formatted}")
    if not parts:
        return None
    return ", ".join(parts)


def format_cap(cap_amount: Any, cap_currency: str) -> str | None:
    """Format cap as human-readable string. Returns None if no cap."""
    if cap_amount is None:
        return None
    amount = to_float(cap_amount)
    if amount is None:
        return None
    if amount == 0:
        return "No cap"
    symbol = currency_symbol(cap_currency)
    if amount >= 1_000_000:
        return f"{symbol}{amount / 1_000_000:g}M"
    if amount >= 1_000:
        return f"{symbol}{amount / 1_000:g}K"
    return f"{symbol}{amount:g}"


def format_money(amount: Any, currency: str) -> str:
    val = to_float(amount)
    if val is None:
        return "See programme terms"
    symbol = currency_symbol(currency)
    if val >= 1_000_000:
        return f"{symbol}{val / 1_000_000:g}M"
    if val >= 1_000:
        return f"{symbol}{val / 1_000:g}K"
    return f"{symbol}{val:g}"


def format_millions(amount: float, symbol: str = "£") -> str | None:
    """Format a monetary amount as '[symbol]XXM' or '[symbol]XX.XM' for prose matching."""
    if amount < 100_000:
        return None
    m = amount / 1_000_000
    if m == int(m):
        return f"{symbol}{int(m)}M"
    return f"{symbol}{m:.1f}M"


def currency_symbol(currency: str) -> str:
    return {
        "GBP": "£",
        "USD": "$",
        "EUR": "€",
        "ZAR": "R",
        "HUF": "Ft ",
        "NGN": "₦",
        "AUD": "A$",
        "CAD": "C$",
    }.get((currency or "").upper(), f"{currency} ")


def budget_to_display(
    gbp_amount: float,
    territory_currency: str,
    budget_currency: str,
    budget_original_amount: float | None,
    budget_gbp: float | None,
    fx_rates_from_budget: dict[str, dict] | None,
) -> tuple[float, str, str | None]:
    """Convert a GBP-computed amount to the territory's display currency.

    When the territory's incentive currency matches the budget currency,
    scales from the original budget amount directly (avoiding GBP round-trip).
    Otherwise uses the budget→territory FX rate.

    Returns (display_amount, currency_symbol, fx_note_or_None).
    """
    symbol = currency_symbol(territory_currency)

    if territory_currency == budget_currency:
        # Same currency — scale from original amount to avoid round-trip
        if budget_original_amount and budget_gbp and budget_gbp > 0:
            ratio = gbp_amount / budget_gbp
            return round(budget_original_amount * ratio, 0), symbol, None
        # Fallback if original amount not available
        return round(gbp_amount, 0), symbol, None

    # Different currency — convert via FX rate
    fx_rates = fx_rates_from_budget or {}
    fx_info = fx_rates.get(territory_currency)
    if fx_info and fx_info.get("rate"):
        rate = fx_info["rate"]
        # Convert from budget currency to territory currency
        if budget_original_amount and budget_gbp and budget_gbp > 0:
            ratio = gbp_amount / budget_gbp
            display = round(budget_original_amount * ratio * rate, 0)
        else:
            display = round(gbp_amount * rate, 0)
        fx_date = fx_info.get("rate_date", "")
        note = (
            f"Converted from {budget_currency} at rate {rate:.4f}"
            f"{' (' + fx_date + ')' if fx_date else ''}."
        )
        return display, symbol, note

    # No FX rate available — show in budget currency as fallback
    symbol = currency_symbol(budget_currency)
    if budget_original_amount and budget_gbp and budget_gbp > 0:
        ratio = gbp_amount / budget_gbp
        return round(budget_original_amount * ratio, 0), symbol, None
    return round(gbp_amount, 0), symbol, None


def parse_money_string(text: Any) -> float | None:
    """Best-effort parse of a monetary string like '£22.5M', '$6,500,000',
    '£18M net', '£7,950,000 - £10,500,000' (takes the first figure).

    Returns a float in base units (e.g. 22_500_000 for £22.5M), or None.
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None

    # Strip leading approximation markers and currency symbols
    raw = _re.sub(r'^[~≈]\s*', '', raw)
    raw = _re.sub(r'^[£$€R₦]\s*', '', raw)
    # Also strip "A$", "C$", "NZ$", "Ft " and ISO currency code prefixes
    raw = _re.sub(r'^(?:A\$|C\$|NZ\$|Ft\s*|Kč\s*)', '', raw)
    raw = _re.sub(r'^(?:HUF|USD|CAD|EUR|GBP|ZAR|NGN|AUD|NZD|CZK|MAD|RON|RSD)\s*', '', raw)

    # Match patterns like "22.5M", "6,500,000", "18M", "7.95M"
    match = _re.match(r'([\d,]+(?:\.\d+)?)\s*([MmKkBb])?', raw)
    if not match:
        return None

    number_str = match.group(1).replace(',', '')
    try:
        value = float(number_str)
    except ValueError:
        return None

    multiplier_char = (match.group(2) or '').upper()
    multipliers = {'M': 1_000_000, 'K': 1_000, 'B': 1_000_000_000}
    value *= multipliers.get(multiplier_char, 1)

    return value if value > 0 else None


# Third-party data providers we cannot surface in user-facing output for legal
# reasons. Matched case-insensitively against each "/"-separated attribution part.
_SUPPRESSED_SOURCE_TOKENS = ("tmdb", "the movie database")


# ── Production schedule (canonical) ──────────────────────────────────────────
#
# Two figures used to reach the reader with no stated relationship: the
# executive summary printed a shoot length in weeks, and script intelligence
# printed estimated shooting days. They are genuinely different facts, the
# producer's planned duration versus what the script implies, but presented
# side by side and unlabelled they read as a contradiction (14 days against an
# 8 week shoot). Worse, the two places that converted days into weeks disagreed
# on the divisor: the report service used 5 days per week, completion-date
# estimation used 5.5, so the same script produced two different shoot lengths
# depending on which code path asked.
#
# One divisor lives here now, and ``resolve_schedule`` returns both figures
# together with which one the producer supplied, so a caller cannot present a
# derived number as a declared one.

#: Shooting days per production week. A six-day week is not assumed: a five-day
#: week is the standard scheduling basis, and overtime is a cost question rather
#: than a calendar one.
DEFAULT_SHOOT_DAYS_PER_WEEK = 5.0


def resolve_schedule(
    declared_weeks: Any = None,
    script_shoot_days: Any = None,
) -> dict[str, Any]:
    """Canonical production schedule.

    ``declared_weeks`` is what the producer told us; ``script_shoot_days`` is
    what the parser estimated from the screenplay. Returns:

      ``shootWeeks``      weeks to plan against
      ``shootDays``       the script's estimated shooting days, or None
      ``weeksSource``     ``"declared"`` | ``"derived_from_script"`` | None
      ``impliedWeeks``    what the script's days convert to, for comparison
      ``divergent``       True when the two disagree by more than a week

    The declared duration always wins: it is a decision, not an estimate, and
    overwriting it with a derived figure would tell a producer their own
    schedule is something other than what they entered.
    """
    weeks = to_float(declared_weeks)
    days = to_float(script_shoot_days)

    implied = days / DEFAULT_SHOOT_DAYS_PER_WEEK if days and days > 0 else None

    if weeks and weeks > 0:
        source = "declared"
    elif implied is not None:
        weeks = max(1.0, round(implied))
        source = "derived_from_script"
    else:
        weeks = None
        source = None

    divergent = (
        weeks is not None
        and implied is not None
        and source == "declared"
        and abs(implied - weeks) > 1.0
    )

    return {
        "shootWeeks": int(round(weeks)) if weeks else None,
        "shootDays": int(round(days)) if days and days > 0 else None,
        "weeksSource": source,
        "impliedWeeks": round(implied, 1) if implied is not None else None,
        "divergent": divergent,
        "daysPerWeek": DEFAULT_SHOOT_DAYS_PER_WEEK,
    }


# ── Payment timing (canonical) ───────────────────────────────────────────────
#
# One representation of "when does the money arrive", resolved once and consumed
# by every section. Before this existed, three sections read three different
# sources and disagreed inside the same report:
#
#   * territory card / incentive table / executive summary read only
#     ``payment_timeline_notes``, a free-text column. When it was NULL they
#     printed "Data not available" even though the programme recorded
#     ``payment_timeline_days_min/max`` (Italy: 180-365 days, i.e. 6 to 12
#     months, was reported as unavailable);
#   * the payment-timing chart read a different table entirely,
#     ``territory_profiles`` certification + payment weeks, so it could show a
#     window the rest of the report contradicted;
#   * neither collapsed a degenerate range, so equal bounds rendered "12-12 MO".
#
# The programme's own numbers win when present, because they describe the
# programme this report is quoting. The territory bankability research is the
# documented fallback, and which one was used is recorded on the object so a
# reader is never told research-derived timing is programme-stated.

#: Days per month used for every days-to-months conversion in the pipeline, so
#: chart, card and narrative round identically.
DAYS_PER_MONTH = 30.0

#: Weeks per month, for the territory-profile fallback (weeks are what the
#: bankability research records).
WEEKS_PER_MONTH = 4.345


def _months_from_days(days: Any) -> float | None:
    value = to_float(days)
    return None if value is None else value / DAYS_PER_MONTH


def _months_from_weeks(weeks: Any) -> float | None:
    value = to_float(weeks)
    return None if value is None else value / WEEKS_PER_MONTH


#: A month window stated inside a free-text note, e.g. "6-12 months
#: post-completion" or "about 4 months". Used only to cross-check the numeric
#: columns, never as the displayed window: prose is not a data type.
_NOTE_MONTHS_RE = _re.compile(
    r"(\d{1,2})\s*(?:-|to|\u2013|\u2014)\s*(\d{1,2})\s*month|(\d{1,2})\s*month",
    _re.I,
)


def months_stated_in_note(notes: Any) -> tuple[int, int] | None:
    """The month window a note claims, when it states one, else None."""
    if not notes:
        return None
    match = _NOTE_MONTHS_RE.search(str(notes))
    if not match:
        return None
    if match.group(1) and match.group(2):
        lo, hi = int(match.group(1)), int(match.group(2))
        return (min(lo, hi), max(lo, hi))
    single = int(match.group(3))
    return (single, single)


def resolve_payment_timing(
    incentive_row: dict | None = None,
    territory_profile: dict | None = None,
) -> dict[str, Any]:
    """Canonical payment timing for one programme in one territory.

    Returns a dict with:
      ``minMonths`` / ``maxMonths``  rounded whole months, or None
      ``source``   ``"programme"`` | ``"territory_research"`` | None
      ``notes``    the programme's free-text note, verbatim, when present
      ``label``    the display string every section renders
      ``conflict`` True when the note states a window the numbers contradict

    Both bounds are always either populated together or both None: a window with
    only one end is not a window, and rendering it as one invites a reader to
    treat an unknown bound as certainty.

    The structured columns win the display, because they are what the chart
    scales and what bankability is computed from, and a number is comparable
    across programmes in a way prose is not. A note that disagrees with them is
    not silently discarded: ``conflict`` is set so the validator can surface it
    and the source record can be corrected, rather than the report quietly
    picking one of two contradictory claims.
    """
    row = incentive_row or {}
    notes = row.get("payment_timeline_notes") or None

    min_months = _months_from_days(row.get("payment_timeline_days_min"))
    max_months = _months_from_days(row.get("payment_timeline_days_max"))
    source: str | None = "programme" if (min_months is not None or max_months is not None) else None

    if source is None and territory_profile:
        # Certification window plus payment window is completion-to-cash, which
        # is the same question the programme columns answer.
        cert_min = _months_from_weeks(territory_profile.get("cert_weeks_min"))
        cert_max = _months_from_weeks(territory_profile.get("cert_weeks_max"))
        pay_min = _months_from_weeks(territory_profile.get("payment_weeks_min"))
        pay_max = _months_from_weeks(territory_profile.get("payment_weeks_max"))
        # Only a fully verified pair produces a total: adding a verified window
        # to a missing one reads as a complete figure when it is not.
        if cert_max is not None and pay_max is not None:
            min_months = (cert_min if cert_min is not None else cert_max) + (
                pay_min if pay_min is not None else pay_max
            )
            max_months = cert_max + pay_max
            source = "territory_research"

    if min_months is None and max_months is None:
        # No structured window. A note that states one is the only thing we have,
        # so it is shown verbatim rather than replaced with "Data not available".
        return {
            "minMonths": None, "maxMonths": None,
            "source": "note" if notes else None,
            "notes": notes,
            "conflict": False,
            "label": notes or "Data not available",
        }

    # A single recorded bound describes both ends of a one-point window.
    lo = int(round(min_months if min_months is not None else max_months))
    hi = int(round(max_months if max_months is not None else min_months))
    if lo > hi:
        lo, hi = hi, lo

    stated = months_stated_in_note(notes)
    # More than a month apart is a real disagreement rather than rounding.
    conflict = bool(
        stated and (abs(stated[0] - lo) > 1 or abs(stated[1] - hi) > 1)
    )

    return {
        "minMonths": lo,
        "maxMonths": hi,
        "source": source,
        "notes": notes,
        "conflict": conflict,
        "label": format_payment_timing(lo, hi),
    }


def format_payment_timing(min_months: int | None, max_months: int | None) -> str:
    """Display string for a month window.

    Equal bounds collapse to a single figure: "12-12 months" is a range whose
    ends are the same number, which reads as a data error rather than as a
    twelve-month wait.
    """
    if min_months is None and max_months is None:
        return "Data not available"
    lo = min_months if min_months is not None else max_months
    hi = max_months if max_months is not None else min_months
    if lo == hi:
        return f"{lo} month" if lo == 1 else f"{lo} months"
    return f"{lo} to {hi} months"


def clean_source(source: Any) -> str:
    """Strip legally-suppressed provider attributions (e.g. TMDB) from a source
    string, keeping any remaining provenance. Returns "Industry sources" when
    nothing usable remains. Mirrors the frontend ``cleanSource`` helper."""
    if not source:
        return "Industry sources"
    parts = [p.strip() for p in str(source).split("/")]
    kept = [
        p for p in parts
        if p and not any(tok in p.lower() for tok in _SUPPRESSED_SOURCE_TOKENS)
    ]
    return " / ".join(kept) if kept else "Industry sources"
