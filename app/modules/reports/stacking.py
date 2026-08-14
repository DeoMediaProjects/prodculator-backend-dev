"""Whether two programmes combine: decided once, from every field that says so.

The Tax Incentive Analysis printed both of these about the same pair, on the same
page:

    "SUPPLEMENTARY: UK VFX Expenditure Credit (Uplift) stacks ON TOP of
     AVEC (Enhanced/IFTC)."

    "NARROW ELIGIBILITY -- Independent Film Tax Credit (Enhanced AVEC): ...
     Cannot be combined with the VFX uplift or animation uplift."

Two unconnected channels produced them. The first is composed in the builder from
``is_supplementary``. The second is ``incentive_programs.eligibility_notes`` prose
rendered verbatim into the requirements list. Neither consulted the other, and the
exclusion detector that was supposed to prevent exactly this missed for two
independent reasons:

* it read the exclusion off the SUPPLEMENTARY row, but the UK exclusion is recorded
  on the PRIMARY row (the IFTC record), which it never looked at; and
* it matched only the literal phrase "cannot be combined with", while the VFX row's
  own ``qs_basis`` says "Cannot combine with the IFTC enhanced rate" — a phrasing
  the regex does not cover.

The correct answer for this pair is that they do NOT stack: the enhanced/IFTC rate
and the VFX uplift are alternatives. So the DB prose was right and the generated
note was wrong, which is worth stating plainly — the fix is not to soften the prose
but to make the computed note agree with it.

This module resolves the relationship once, from every field on BOTH rows that could
carry it, and returns the single note every surface renders. Adding a phrasing here
fixes it everywhere rather than in whichever renderer someone noticed it in.
"""
from __future__ import annotations

import json as _json
import re
from typing import Any

# ── Relationship values ──────────────────────────────────────────────────────

STACKS = "stacks"
MUTUALLY_EXCLUSIVE = "mutually_exclusive"
UNKNOWN = "unknown"

VALID_RELATIONSHIPS = frozenset({STACKS, MUTUALLY_EXCLUSIVE, UNKNOWN})

#: Fields on an incentive row that may state a combination constraint. Order is not
#: significant — any hit is a hit — but the list is exhaustive on purpose: a
#: constraint recorded in a field absent from here is a constraint the report will
#: contradict itself about, which is the whole failure this module exists to stop.
_CONSTRAINT_FIELDS = (
    "eligibility_notes",
    "notes",
    "qs_basis",
    "calc_formula",
    "ai_rule",
    "stacking_note",
)

#: Ways the datasets actually phrase an exclusion. Captured group is the excluded
#: programme description.
#
#: The capture class excludes newlines as well as sentence punctuation. ``[^.;]+``
#: looked equivalent and is not: a negated character class matches newlines, and
#: _constraint_text joins several fields with newlines, so a clause with no trailing
#: full stop — "Cannot combine with the IFTC enhanced rate or animation uplift", which
#: is exactly how the live UK VFX row's qs_basis is written — swallowed every
#: subsequent field. That pulled "AVEC" out of a later warnings entry and reported the
#: VFX uplift as mutually exclusive with STANDARD AVEC, which it is not. One clause per
#: match, and a field boundary ends a clause.
_EXCLUSION_PATTERNS = (
    r"cannot be combined with ([^.;\n]+)",
    r"cannot combine with ([^.;\n]+)",
    r"can(?:'|no)?t be combined with ([^.;\n]+)",
    r"not combinable with ([^.;\n]+)",
    r"mutually exclusive with ([^.;\n]+)",
    r"cannot be claimed (?:together |alongside |with )?(?:as well as )?([^.;\n]+)",
    r"may not be combined with ([^.;\n]+)",
    r"instead of,? not (?:in addition to|alongside) ([^.;\n]+)",
)

#: Ways they phrase an explicit permission, so a curator can override a false
#: positive without editing code.
_STACKING_PATTERNS = (
    r"stacks? (?:on top of|with) ([^.;\n]+)",
    r"combinable with ([^.;\n]+)",
    r"can be combined with ([^.;\n]+)",
    r"claimed alongside ([^.;\n]+)",
    r"in addition to ([^.;\n]+)",
)

#: Words too generic to identify a programme. Without this, "Credit" alone matches
#: nearly every record in the table and every pair looks mutually exclusive.
_GENERIC_TOKENS = frozenset({
    "the", "and", "or", "with", "for", "any", "all", "other", "this", "that",
    "programme", "program", "programmes", "programs",
    "credit", "credits", "tax", "relief", "reliefs", "expenditure", "incentive",
    "incentives", "rebate", "rebates", "scheme", "schemes", "fund", "funds",
    "funding", "film", "films", "television", "audiovisual", "audio", "visual",
    "rate", "rates", "enhanced", "standard", "uplift", "uplifts", "national",
    "regional", "state", "federal", "production", "productions", "qualifying",
    "spend", "cash", "grant", "grants", "offset", "offsets",
})


def _tokens(text: str | None) -> set[str]:
    """Distinctive words in *text*, lowercased, generic incentive vocabulary removed."""
    if not text:
        return set()
    words = re.findall(r"[A-Za-z]{2,}", str(text))
    return {w.lower() for w in words} - _GENERIC_TOKENS


def _constraint_text(row: dict | None) -> str:
    """Every constraint-bearing field on *row*, concatenated for scanning.

    ``warnings_json`` is included because the UK VFX row records
    "Must be claimed alongside IFTC or AVEC — cannot be claimed alone" there and
    nowhere else, and a constraint the scanner cannot see is a contradiction waiting
    to be rendered.
    """
    if not row:
        return ""
    parts: list[str] = []
    for field in _CONSTRAINT_FIELDS:
        value = row.get(field)
        if value and isinstance(value, str):
            parts.append(value)
    raw_warnings = row.get("warnings_json")
    if raw_warnings:
        if isinstance(raw_warnings, str):
            try:
                raw_warnings = _json.loads(raw_warnings)
            except (ValueError, TypeError):
                raw_warnings = [raw_warnings]
        if isinstance(raw_warnings, list):
            parts.extend(str(w) for w in raw_warnings if w)
    return " \n ".join(parts)


def _matches(patterns: tuple[str, ...], text: str, target_tokens: set[str]) -> str | None:
    """The first clause in *text* naming something that looks like *target_tokens*."""
    if not text or not target_tokens:
        return None
    for pattern in patterns:
        for clause in re.findall(pattern, text, flags=re.IGNORECASE):
            if _tokens(clause) & target_tokens:
                return clause.strip()
    return None


def resolve_stacking(
    primary_row: dict | None,
    supplementary_row: dict | None,
    *,
    primary_name: str | None = None,
    supplementary_name: str | None = None,
) -> dict[str, Any]:
    """The one stacking relationship between these two programmes.

    Both rows are scanned for constraints naming the other. An explicit exclusion
    beats an explicit permission: a dataset that says both is a dataset with a
    curation problem, and in that state refusing to combine is the direction that
    cannot overstate what a production can claim.

    Returns the relationship, the evidence it rests on, and the note every surface
    should render, so no renderer composes its own wording.
    """
    primary = (primary_name or (primary_row or {}).get("program") or "").strip()
    supplementary = (
        supplementary_name or (supplementary_row or {}).get("program") or ""
    ).strip()

    primary_tokens = _tokens(primary)
    supplementary_tokens = _tokens(supplementary)

    primary_text = _constraint_text(primary_row)
    supplementary_text = _constraint_text(supplementary_row)

    # Look in both directions. The UK case is recorded only on the primary row, which
    # is why reading the supplementary row alone missed it.
    exclusion = (
        _matches(_EXCLUSION_PATTERNS, primary_text, supplementary_tokens)
        or _matches(_EXCLUSION_PATTERNS, supplementary_text, primary_tokens)
    )
    permission = (
        _matches(_STACKING_PATTERNS, primary_text, supplementary_tokens)
        or _matches(_STACKING_PATTERNS, supplementary_text, primary_tokens)
    )

    if exclusion:
        relationship = MUTUALLY_EXCLUSIVE
        evidence = exclusion
    elif permission:
        relationship = STACKS
        evidence = permission
    elif supplementary_row is not None and supplementary_row.get("is_supplementary"):
        # No stated constraint either way. is_supplementary means the programme is
        # not claimable as a primary, which is what it is FOR — a supplementary
        # credit exists to be added to something. Treated as stacking, and the note
        # says the pairing is unconfirmed so a reader knows which part was inferred.
        relationship = STACKS
        evidence = None
    else:
        relationship = UNKNOWN
        evidence = None

    return {
        "relationship": relationship,
        "primary": primary or None,
        "supplementary": supplementary or None,
        "evidence": evidence,
        "note": _compose_note(relationship, primary, supplementary, evidence),
        "stacks": relationship == STACKS,
    }


def _compose_note(
    relationship: str,
    primary: str,
    supplementary: str,
    evidence: str | None,
) -> str | None:
    """The single rendered sentence for this relationship."""
    if not supplementary:
        return None
    target = primary or "the primary incentive"

    # Emphasis casing matches the wording these notes have always used, so the
    # relationship is as scannable in the rendered PDF as it was before.
    if relationship == MUTUALLY_EXCLUSIVE:
        note = (
            f"MUTUAL EXCLUSIVITY: {supplementary} CANNOT be combined with {target}. "
            f"These are alternatives — a production claims one or the other, not both. "
            f"Model both paths before committing."
        )
        if evidence:
            note += f" Recorded constraint names: {evidence.strip()}."
        return note

    if relationship == STACKS:
        note = (
            f"SUPPLEMENTARY: {supplementary} stacks ON TOP of {target}. "
            f"Applies only to qualifying specialist expenditure, not total budget. "
            f"Calculate on your estimated specialist-spend proportion for the "
            f"combined territory benefit."
        )
        if not evidence:
            note += (
                " The programme is recorded as supplementary but the pairing with "
                "this specific primary is not stated in its terms — confirm with the "
                "programme administrator."
            )
        return note

    return (
        f"{supplementary} is recorded for this territory, but whether it combines "
        f"with {target} is not stated in either programme's terms. Confirm before "
        f"modelling the two together."
    )


# ── Contradiction detection ──────────────────────────────────────────────────

_STACK_CLAIM_RE = re.compile(
    r"stacks?\s+(?:on\s+top\s+of|with)|combinable with|can be combined with",
    re.IGNORECASE,
)
_EXCLUSION_CLAIM_RE = re.compile(
    r"cannot\s+(?:be\s+)?combin|mutual(?:ly)?\s+exclusiv|not\s+combinable|"
    r"may not be combined",
    re.IGNORECASE,
)


def statements_contradict(texts: list[str]) -> bool:
    """True when this collection asserts both that a pair stacks and that it cannot.

    Used by the cross-section validator against all stacking-bearing text for one
    territory. Deliberately coarse: any co-occurrence of the two claim shapes in one
    territory's incentive text is worth failing on, because the reader has no way to
    tell which of the two to act on.
    """
    claims_stack = False
    claims_exclusive = False
    for text in texts:
        if not text:
            continue
        if _STACK_CLAIM_RE.search(text):
            claims_stack = True
        if _EXCLUSION_CLAIM_RE.search(text):
            claims_exclusive = True
    return claims_stack and claims_exclusive
