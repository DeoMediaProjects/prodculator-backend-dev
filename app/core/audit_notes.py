"""Detection and separation of internal data-audit annotations.

PROD-FIX-006. The admin/data team annotates incentive records inline with
bracketed audit notes, e.g.::

    [FLAGGED 2026-07: found historical evidence of first-come-first-served
     annual budget allocation ... needs direct confirmation from Film Center
     Serbia before treating 'No cap' as reliable.]

These are written for the data team. They must never reach a client-facing
report — a producer reading their own report's internal QA trail is a trust
problem, not a data problem.

The primary defence is schema separation: audit text lives in
``incentive_programs.internal_audit_notes``, which no code under
``app/modules/reports`` reads. This module provides the detector used to
establish and hold that separation:

  * ``split_audit_text``   — used by data migrations to move annotations out of
                             narrative-facing columns
  * ``contains_audit_text``— used as a fail-closed guard at the report boundary,
                             so a future record that reintroduces an annotation
                             fails the report rather than shipping it

Keep the two in sync: anything ``split_audit_text`` removes,
``contains_audit_text`` must detect.
"""
from __future__ import annotations

import re

# Columns that feed the AI narrative, the PDF template, or the Excel export,
# and must therefore never carry internal audit text.
NARRATIVE_COLUMNS: tuple[str, ...] = (
    "notes",
    "eligibility_notes",
    "qs_basis",
    "calc_formula",
    "annual_programme_cap",
)

# Known annotation labels. The QA brief listed four; the live dataset carries
# ten, because the brief only saw the ones that surfaced in the three reports
# reviewed. AUDIT_SPAN below matches the *shape* of the convention rather than
# this enumeration, so a new variant is caught without a code change. The list
# is retained for error messages and for documentation of the convention.
AUDIT_MARKERS: tuple[str, ...] = (
    "FLAGGED",
    "UPDATED",
    "STRUCTURAL FLAG",
    "RENAMED",
    "CONFIRMED",
    "CORRECTED",
    "MAJOR CORRECTION",
    "MAJOR STRUCTURAL CHANGE",
    "UNRESOLVED",
    "DISCREPANCY FLAG",
    "DISCREPANCY",
    "ADMIN VERIFY",
    "VERIFY",
    "NOTE TO ADMIN",
)

# Any bracketed span whose opening token is an ALL-CAPS label of two or more
# characters — the data team's annotation convention — optionally followed by a
# YYYY-MM stamp, then a colon. Bracketed spans do not nest in this dataset, so a
# simple [^\]]* body is sufficient and avoids the catastrophic backtracking a
# nesting-aware pattern would risk.
AUDIT_SPAN = re.compile(r"\[\s*[A-Z][A-Z][A-Z /-]*(?:\s+\d{4}-\d{2}(?:-\d{2})?)?\s*:[^\]]*\]")

# Parenthetical asides carrying an instruction to the data team rather than
# information for the client, e.g. New York's annual programme cap:
#   "(sources conflict on exact figure post-2026 increase — ADMIN VERIFY before
#    relying on a specific number)"
# Removing the aside does not hide the uncertainty from the client: the value
# itself still states a range, and warnings_json still carries VERIFY FIRST.
AUDIT_PAREN = re.compile(
    r"\s*\([^()]*(?:ADMIN VERIFY|sources? (?:conflict|disagree)|needs? confirmation)"
    r"[^()]*\)",
    re.IGNORECASE,
)

# Sentence-level internal language. A sentence that talks about the dataset
# itself ("this record", "this row", "this session") or instructs someone to go
# and verify something is addressed to the data team, not to the producer
# reading the report. Genuine client-facing caution is carried separately by
# warnings_json and by the bankability label — this does not suppress it.
INTERNAL_SENTENCE = re.compile(
    r"""(
        this\s+record | this\s+row | this\s+session | admin\s+verify
      | needs?\s+(?:a\s+)?(?:direct\s+)?(?:confirmation|reconciliation|check|confirming)
      | could\s+not\s+(?:reconcile|confirm|find)
      | flagged\s+for\s+(?:a\s+)?(?:direct\s+)?check
      | not\s+a\s+conflation
      | worth\s+adding
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Sentence boundary: period or semicolon followed by whitespace and a capital or
# digit. Deliberately conservative — an over-merged sentence only means slightly
# coarser extraction, whereas an over-eager split could strand a fragment.
_SENTENCE_SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-Z0-9])")


def _tidy(text: str) -> str:
    """Repair the seams left by removing spans from the middle of prose."""
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"(?:[.,;:]\s*){2,}", ". ", text)
    # Unbalanced parentheses mean a removal cut through a pair.
    if text.count("(") != text.count(")"):
        text = text.replace("(", "").replace(")", "")
    text = re.sub(r"[\s.,;:—-]+$", "", text)
    return text.strip()


def contains_audit_text(value: object) -> bool:
    """True if ``value`` carries internal audit annotation of any known form.

    Used as a fail-closed guard at the report boundary. Non-string input is
    never audit text and returns False.
    """
    if not isinstance(value, str) or not value:
        return False
    return bool(AUDIT_SPAN.search(value) or INTERNAL_SENTENCE.search(value))


def split_audit_text(value: str | None) -> tuple[str | None, list[str]]:
    """Split one field value into (client-safe prose, audit fragments).

    Returns the value unchanged with an empty fragment list when there is
    nothing to extract. Returns ``None`` as the prose when the value was
    entirely audit annotation (three records in the v4 dataset are: Portugal's
    notes, British Columbia's notes, Singapore's qsBasis).
    """
    if not value or not isinstance(value, str):
        return value, []

    # Nothing to do — return the value byte-identical. _tidy is a seam repair
    # for text that has had spans cut out of it; running it over untouched
    # prose would silently reword client-facing copy (it strips terminal
    # punctuation, among other things).
    if not contains_audit_text(value) and not AUDIT_PAREN.search(value):
        return value, []

    extracted: list[str] = []

    # 1 — bracketed audit annotations
    extracted += [m.group(0) for m in AUDIT_SPAN.finditer(value)]
    clean = AUDIT_SPAN.sub("", value)

    # 2 — parenthetical admin directives
    extracted += [m.group(0).strip() for m in AUDIT_PAREN.finditer(clean)]
    clean = AUDIT_PAREN.sub("", clean)

    # 3 — whole sentences written in internal language
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(clean):
        if sentence.strip() and INTERNAL_SENTENCE.search(sentence):
            extracted.append(sentence.strip())
        else:
            kept.append(sentence)
    clean = " ".join(kept)

    return (_tidy(clean) or None), extracted
