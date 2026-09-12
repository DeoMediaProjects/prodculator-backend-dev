"""Turning the Grants Master v2 strings into facts the engine can gate on.

Every value in ``01_DATA/prodculator_grants_master_v2.json`` is a STRING. There is
not one integer, float, boolean, null or array in the whole 253-record file:
``paid_match_eligible`` is the string ``"True"`` or ``"False"``, ``max_amount`` is
``"50000"`` or ``"Up to CAD $30,000"``, ``eligible_formats`` is variously
``'["feature", "documentary"]"``, ``"Film; Television"``, ``"[]"`` or ``""``.

That matters more than it sounds. ``"False"`` is a truthy Python string, so a loader
written against the schema's apparent types marks all 49 ineligible records eligible.
This module is the one place those strings become facts, so that mistake can only be
made once and is tested once.

THE THREE SPELLINGS OF UNKNOWN
------------------------------
``""``, ``None`` and the literal ``"[]"`` all mean "not stated". The third is the
one that bites: 60 of the 253 records carry ``"[]"`` in ``eligible_formats``. Parsed
as JSON it becomes an empty list, and a format gate reading an empty list concludes
"this fund declares zero eligible formats" and rejects every project. A fund that
funds no format is not a thing that exists — an empty list is a serialisation
artefact of missing data, so it reads as unknown and the gate goes untested.

DATA_README.md states the rule this module implements: "Do not infer missing values.
Blank/null = not verified or not stated."
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from app.core.formats import CANONICAL_FORMATS, canonical_format

from app.modules.grants.v2_contracts import STATUS_DATE_RE, STATUS_RULES

# ── unknown ──────────────────────────────────────────────────────────────────

#: Strings that encode "not stated" rather than a value. ``"[]"`` is here for the
#: reason set out in the module docstring; ``"none"``/``"n/a"``/``"tbc"`` appear in
#: hand-maintained source rows and mean the same thing.
_UNKNOWN_TOKENS = frozenset({"", "[]", "{}", "none", "null", "n/a", "na", "-", "—", "tbc"})


def is_unknown(value: Any) -> bool:
    """True when *value* says nothing at all.

    Note that ``0`` and ``False`` are NOT unknown — they are answers. Collapsing
    them into unknown is how a verified zero budget floor turns into "no floor".
    """
    if value is None:
        return True
    if isinstance(value, (int, float, bool)):
        return False
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return str(value).strip().lower() in _UNKNOWN_TOKENS


def parse_bool(value: Any) -> bool | None:
    """Tri-state boolean: True, False, or None for "not stated".

    The master encodes all three as strings, and the difference between False and
    unknown is load-bearing: ``current_cycle_verified`` is ``"True"`` on 103 records,
    ``"False"`` on exactly 1, and ``""`` on 149. Those 149 are not "unverified as a
    fact", they are "nobody has checked" — a different report sentence.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "t", "yes", "y", "1"):
        return True
    if text in ("false", "f", "no", "n", "0"):
        return False
    return None


# ── dates ────────────────────────────────────────────────────────────────────

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_ISO_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def parse_date(value: Any) -> date | None:
    """An ISO date from *value*, or None.

    Accepts both spellings the master uses in one column: ``"2026-09-03"`` (212
    records) and ``"2026-07-01T00:00:00"`` (41 records). A parser strict about
    either one fails on the other, which is how a whole verification cohort goes
    missing.
    """
    if value is None:
        return None
    # datetime is a SUBCLASS of date, so the isinstance check below would return it
    # unchanged — and `date - datetime` raises. The column is a TIMESTAMP, so this is
    # what the database actually hands back for every row.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = _ISO_DATE_RE.match(str(value).strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def months_between(start: date, end: date) -> float:
    """Approximate months from *start* to *end*, matching v1's arithmetic."""
    return (end.year - start.year) * 12 + (end.month - start.month) + (end.day - start.day) / 30.0


# ── current status ───────────────────────────────────────────────────────────


def canonical_status(raw: Any) -> str:
    """One of ``CANONICAL_STATUSES`` for any of the master's 53 raw spellings.

    Applies ``STATUS_RULES`` in order, first match wins. The ordering is the whole
    point and is documented on the table itself — in particular "NO APPLICATIONS
    CURRENTLY OPEN" must be caught before the generic "contains open" rule, and
    "closed_current_round" before the generic "contains round" rule.
    """
    if is_unknown(raw):
        return "UNKNOWN"
    text = str(raw).strip().lower()
    for kind, needle, status in STATUS_RULES:
        if kind == "contains" and str(needle) in text:
            return status
        if kind == "equals" and text == needle:
            return status
        if kind == "startswith" and text.startswith(str(needle)):
            return status
        if kind == "in" and isinstance(needle, tuple) and text in needle:
            return status
    return "UNKNOWN"


def status_window(raw: Any) -> tuple[date | None, date | None]:
    """Dates embedded in a status token, as ``(opens, closes)``.

    ``"open_2026_09_07_to_2026_10_07"`` yields both; ``"opens_2026_09_15"`` yields an
    opening only; ``"CALL 2 DEADLINE 2026-09-04"`` yields a closing only.

    This is descriptive colour, NOT a deadline. known_edge_cases.md forbids inventing
    a deadline, and a date inside a status label is evidence of a window someone
    typed, not an approved application deadline — so nothing here ever writes
    ``next_deadline``.
    """
    if is_unknown(raw):
        return (None, None)
    text = str(raw).strip()
    found: list[date] = []
    for match in STATUS_DATE_RE.finditer(text):
        try:
            found.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    if not found:
        return (None, None)
    lowered = text.lower()
    if len(found) >= 2:
        return (found[0], found[1])
    # A single date is an opening only when the token says so; otherwise it is the
    # closing edge ("open_until_", "open_to_", "DEADLINE").
    if lowered.startswith("opens_") or "opens_" in lowered:
        return (found[0], None)
    return (None, found[0])


# ── deadlines ────────────────────────────────────────────────────────────────

_ROLLING_TOKENS = frozenset({"rolling", "ongoing", "year-round", "year round", "continuous"})


def parse_deadline(raw: Any, *, recurrence: Any = None) -> tuple[str, date | None]:
    """Classify a deadline string as ``(kind, date_or_None)``.

    Only ``ISO_DATE`` produces a date. Everything else — ``"rolling"`` (15 records),
    ``"tbc_2027"`` (11), ``"2027-02"`` (4), and prose like ``"2026 round closed 18
    Mar 2026; next round scheduled spring 2027"`` (8) — returns None, because
    Logic Guide §4 says "Do not invent a date from 'annual' or TBC".

    A partial ``YYYY-MM`` is deliberately NOT widened to the first of the month: a
    producer who books a submission on the 1st because we invented that date is worse
    off than one told the month is all that is published.
    """
    recurrence_text = "" if is_unknown(recurrence) else str(recurrence).strip().lower()
    if recurrence_text in _ROLLING_TOKENS:
        return ("ROLLING", None)
    if is_unknown(raw):
        return ("UNKNOWN", None)

    text = str(raw).strip()
    lowered = text.lower()

    if lowered in _ROLLING_TOKENS or "rolling" in lowered or "year-round" in lowered:
        return ("ROLLING", None)
    if lowered.startswith("tbc") or lowered.endswith("_tbc") or "tbc" in lowered:
        return ("TBC", None)

    parsed = parse_date(text)
    if parsed is not None and _ISO_DATE_RE.match(text):
        return ("ISO_DATE", parsed)
    if _ISO_MONTH_RE.match(text):
        return ("ISO_MONTH_PARTIAL", None)
    return ("FREE_TEXT", None)


# ── formats ──────────────────────────────────────────────────────────────────

#: Prose the grants sources use that the shared FORMAT_MAP does not know. These are
#: source wordings, not new formats — every value maps onto an existing canonical
#: token. Without this table ``canonical_format`` slugifies "Film; Television" into
#: the junk token ``film;_television`` and the format gate rejects the record for
#: failing to match a format it actually accepts.
_GRANT_FORMAT_ALIASES: dict[str, str] = {
    "film": "feature",
    "films": "feature",
    "fiction feature": "feature",
    "feature fiction": "feature",
    "live-action feature": "feature",
    "narrative feature": "feature",
    "long-form scripted": "feature",
    "feature documentary": "documentary",
    "documentaries": "documentary",
    "doc": "documentary",
    "television": "tv_series",
    "tv drama": "tv_series",
    "scripted series": "tv_series",
    "drama series": "tv_series",
    "animated series": "animation",
    "animated short": "animation",
    "shorts": "short",
    "short fiction": "short",
    "children's": "tv_series",
}

#: Splits a prose format list. The master uses semicolons ("Film; Television"),
#: slashes ("Interactive/Digital Games") and commas interchangeably.
_FORMAT_SPLIT_RE = re.compile(r"[;,/]| and ")

#: Trailing qualifiers that describe the same format ("Feature Film 70+ min").
_FORMAT_NOISE_RE = re.compile(r"\s*\b\d+\+?\s*(min|minutes|mins)\b.*$", re.I)

#: Last-resort keyword scan, in priority order, for source wording that names a real
#: format inside a longer phrase: "Finnish Film", "Documentary Feature", "High-end
#: Series", "Children's Film", "Low-budget Feature Film".
#:
#: Priority is why this is a tuple and not a dict. "Documentary Feature" contains
#: both "documentary" and "feature", and it is a documentary — the more specific word
#: has to win. Exact matches are tried before this, so "short film" is already
#: resolved to ``short`` by FORMAT_MAP and never reaches the "film" -> feature rule.
#:
#: This reads the wording a source used; it does not infer a rule the source did not
#: state. A phrase naming no format at all ("Screen projects", "Interactive/Digital
#: Games") still resolves to nothing and leaves the gate untested.
_FORMAT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("documentary", "documentary"),
    ("animation", "animation"),
    ("animated", "animation"),
    ("series", "tv_series"),
    ("television", "tv_series"),
    ("short", "short"),
    ("feature", "feature"),
    ("film", "feature"),
)


def _format_from_keywords(text: str) -> str | None:
    """The format named inside a longer phrase, by priority, or None."""
    for keyword, canonical in _FORMAT_KEYWORDS:
        if keyword in text:
            return canonical
    return None


def parse_format_list(raw: Any) -> list[str] | None:
    """Canonical formats a record accepts, or None when it states none.

    Returns None — meaning "not stated", gate untested — for ``""``, ``"[]"`` and for
    prose that canonicalises to nothing recognisable ("Screen projects",
    "Interactive/Digital Games"). Returning an empty list instead would read as
    "accepts no formats" and hard-reject the record, which for the 89 records that
    state no format at all would delete a third of the database.
    """
    if is_unknown(raw):
        return None

    tokens: list[str] = []
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(item) for item in raw]
    else:
        text = str(raw).strip()
        if text.startswith("["):
            try:
                decoded = json.loads(text)
                if isinstance(decoded, list):
                    tokens = [str(item) for item in decoded]
            except (ValueError, TypeError):
                tokens = []
        if not tokens:
            tokens = _FORMAT_SPLIT_RE.split(text)

    canonical: list[str] = []
    for token in tokens:
        cleaned = _FORMAT_NOISE_RE.sub("", str(token)).strip().lower()
        if not cleaned:
            continue
        if cleaned == "all":
            return ["all"]
        mapped = _GRANT_FORMAT_ALIASES.get(cleaned) or canonical_format(cleaned)
        if mapped not in CANONICAL_FORMATS:
            mapped = _format_from_keywords(cleaned)
        # Drop anything that is not a format we actually reason about. Keeping the
        # slug would make an unrecognised word behave as a format restriction.
        if mapped in CANONICAL_FORMATS and mapped not in canonical:
            canonical.append(mapped)

    return canonical or None


# ── production stage ─────────────────────────────────────────────────────────

#: Source stage wording mapped onto the canonical lifecycle. Two legacy oddities are
#: handled explicitly because both would otherwise misgate:
#:  * "short" is a FORMAT that leaked into the stage column on 9 live rows. It is not
#:    a stage, so it maps to nothing and leaves the record stage-unstated.
#:  * "multi" means "any stage" on 5 live rows, so it must widen the set rather than
#:    narrow it.
_STAGE_ALIASES: dict[str, str] = {
    "development": "development",
    "early development": "development",
    "writing": "development",
    "script development": "development",
    "slate development": "development",
    "research": "development",
    "production": "production",
    "co_production": "co_production",
    "co-production": "co_production",
    "coproduction": "co_production",
    "post-production": "completion",
    "post_production": "completion",
    "post production": "completion",
    "post": "completion",
    "completion": "completion",
    "archive": "completion",
    "distribution": "distribution",
    "sales": "distribution",
    "marketing": "distribution",
    "audience_development": "distribution",
    "festival_launch": "distribution",
    "promotion": "promotion",
    "festival": "promotion",
    "exhibition": "promotion",
}

#: Every canonical stage, used when a record says "multi".
CANONICAL_STAGES = ("development", "production", "completion", "distribution", "co_production",
                    "promotion")

_STAGE_SPLIT_RE = re.compile(r"[;,/]| and ")


def parse_stage_list(raw: Any) -> list[str] | None:
    """Canonical stages a record funds, or None when it states none.

    Handles all three shapes the master uses: bare strings ("production"),
    slash-delimited prose ("production/post-production") and JSON arrays
    ('["completion", "post_production"]').

    None for unstated, for the same reason as ``parse_format_list``: 105 of 253
    records state no stage, and a gate that fails closed on unstated would delete
    them.
    """
    if is_unknown(raw):
        return None

    tokens: list[str] = []
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(item) for item in raw]
    else:
        text = str(raw).strip()
        if text.startswith("["):
            try:
                decoded = json.loads(text)
                if isinstance(decoded, list):
                    tokens = [str(item) for item in decoded]
            except (ValueError, TypeError):
                tokens = []
        if not tokens:
            tokens = _STAGE_SPLIT_RE.split(text)

    stages: list[str] = []
    for token in tokens:
        cleaned = str(token).strip().lower().replace(" ", "_")
        if not cleaned:
            continue
        if cleaned in ("multi", "all"):
            return list(CANONICAL_STAGES)
        mapped = _STAGE_ALIASES.get(cleaned) or _STAGE_ALIASES.get(cleaned.replace("_", "-"))
        if mapped and mapped not in stages:
            stages.append(mapped)

    return stages or None


# ── amounts ──────────────────────────────────────────────────────────────────

#: A value that is nothing but digits (optionally with separators). ONLY these may
#: become a number.
_PURE_NUMERIC_RE = re.compile(r"^\s*\d[\d\s,]*(?:\.\d+)?\s*$")

#: Wording that marks a figure as a whole-programme pool rather than a per-project
#: maximum. Developer Guide §1: "Total programme/call pools are not per-project
#: maximums", and acceptance_tests.md tests exactly this.
_POOL_MARKERS = (
    "total call",
    "total pool",
    "total fund",
    "annual budget",
    "programme budget",
    "overall budget",
    "total programme",
    "fund size",
)


def parse_amount(raw: Any) -> tuple[str | None, float | None, bool]:
    """``(source_text, numeric_value, is_pool)`` for a ``max_amount`` string.

    Only a purely numeric string yields a number. 49 of the 89 populated values in
    the master are prose, and several of them are explicit ANTI-instructions that a
    regex would invert — "Selective amount determined per project; do not hard-code
    €1.2m without current official cap evidence" and "Varies by stream/company
    eligibility; do not use CAD 125,000 as universal cap". Extracting the first
    number from those produces precisely the figure the source says not to use.

    ``is_pool`` marks a figure that describes a whole call, e.g. "Brazil FSA
    approximately BRL1.5m total call", so the report can refuse to present it as a
    per-project maximum.
    """
    if is_unknown(raw):
        return (None, None, False)

    text = str(raw).strip()
    lowered = text.lower()
    is_pool = any(marker in lowered for marker in _POOL_MARKERS)

    if _PURE_NUMERIC_RE.match(text):
        try:
            return (text, float(text.replace(",", "").replace(" ", "")), is_pool)
        except ValueError:
            return (text, None, is_pool)

    return (text, None, is_pool)


# ── eligibility prose ────────────────────────────────────────────────────────


def parse_text_list(raw: Any) -> list[str]:
    """Prose lines from a field that is sometimes a JSON array of strings.

    ``eligibility_summary`` is a plain paragraph on 133 records, a JSON array of
    legacy lines on 110, and the literal ``"[]"`` on 10.
    """
    if is_unknown(raw):
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if text.startswith("["):
        try:
            decoded = json.loads(text)
            if isinstance(decoded, list):
                return [str(item).strip() for item in decoded if str(item).strip()]
        except (ValueError, TypeError):
            pass
    return [text] if text else []
