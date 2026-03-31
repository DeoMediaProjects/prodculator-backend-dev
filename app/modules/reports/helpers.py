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

# Data freshness threshold — flag incentives older than this many days
STALE_DAYS = 365

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


def best_incentive(rows: list[dict], production_format: str | None = None) -> dict:
    """Pick the row with the highest rate_gross (fallback to rate_net).

    When *production_format* is given, rows whose ``applicable_formats`` JSON
    array does NOT include that format are excluded before ranking.  A NULL /
    absent ``applicable_formats`` means the programme applies to all formats
    (backward-compatible default).  If filtering would leave no rows we fall
    back to the full set so the caller never gets an error.

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
        def _format_ok(r: dict) -> bool:
            af = r.get("applicable_formats")
            if af is None:
                return True  # NULL → applies to all formats
            if isinstance(af, str):
                try:
                    af = _json.loads(af)
                except (ValueError, TypeError):
                    return True  # unparseable → don't exclude
            if isinstance(af, list) and af:
                return any(f.lower() == production_format.lower() for f in af)
            return True  # empty list → applies to all formats

        filtered = [r for r in rows if _format_ok(r)]
        if filtered:
            eligible = filtered
        # else: no matching rows → fall back to full set (graceful degradation)

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
    if gross is not None and gross > 0:
        return f"{gross:g}%"
    if net is not None and net > 0:
        return f"{net:g}%"
    return None


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
