"""Validate a filled research workbook, and optionally record it. Read-only by default.

Usage::

    venv/Scripts/python scripts/ingest_research_workbook.py PATH.xlsx
    DB_URL=… venv/Scripts/python scripts/ingest_research_workbook.py PATH.xlsx --apply

Reads the workbook directly rather than asking for seven CSV exports. An export
step between the sheet a researcher filled and the ledger is a step where a
subject_id gets mistyped, and the subject_id is the one column nobody can
sanity-check by eye.

WHAT THIS IS NOT
----------------
It is not approval. Every claim lands PENDING however thorough the workbook
looks, and reaching an engine still takes a named reviewer — and for market hard
gates and festival section rules, a second person who is not the author.

A workbook can be filled carefully and still be wrong, and a workbook that says
it was filled carefully is not evidence that it was. This validates shape and
provenance: that a source exists, is official, is not our own data, and that the
claim records what the source says. Whether the source says what the researcher
reports is what the review step is for.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RESEARCH_COLUMNS = ("value", "source_url", "source_basis", "verified_on", "verified_by")

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HAS_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")

_INCENTIVE_ENGINES = {
    "CORE_LOWER_OF", "ELIGIBLE_LOCAL_SPEND", "QUALIFIED_LABOUR", "QAPE", "QNZPE",
    "VFX_ONLY", "PDV_ONLY", "TIERED_SPEND", "MULTI_BUCKET", "INVESTOR_TAX_SHELTER",
    "COMPETITIVE_GRANT", "NO_PROGRAMME",
}
_OPERATORS = {
    "equals", "one_of", "at_least", "at_most", "greater_than", "less_than",
    "overlaps", "manual_confirmation",
}
_PREMIERE = {"WORLD", "INTERNATIONAL", "NATIONAL", "NONE"}
#: The canonical production formats. A comparable's format is compared token to
#: token against the project's, so a phrase never matches and reads afterwards
#: as a title nobody researched.
_FORMATS = {"feature", "short", "documentary", "tv_series", "animation"}
#: Gate 8's list-valued fields. Semicolon-separated because a genre or a country
#: can contain a comma and splitting on one would invent two values from one.
_COMPARABLE_LISTS = {"genres", "production_countries", "primary_languages"}


def _reads_as_rule(line: str) -> bool:
    """Whether a typed-rule line names a field and a known operator.

    Either order is accepted for ``manual_confirmation``, and that is a
    correction to the instruction rather than a leniency. The pack said
    "field operator expected", but manual_confirmation has no expected value —
    there is nothing to compare against, only something for a human to check —
    so "manual_confirmation attached_director" is as natural a reading as
    "attached_director manual_confirmation". Rejecting one of them would be
    marking a researcher wrong for an ambiguity in the instruction they were
    given.
    """
    parts = line.split()
    if len(parts) < 2:
        return False
    return parts[0] in _OPERATORS or parts[1] in _OPERATORS
_NOT_YET = {"NOT_ANNOUNCED", "ROLLING", "UNKNOWN"}


def value_problems(claim) -> list[str]:
    """Whether the value is in the shape the engine will have to read.

    Separate from ``validate_claim``, which checks provenance: that a source
    exists, is official, and is not our own data. A claim can pass all of that
    and still carry a date no parser will read, and the engine discovers it
    months later as an absence rather than an error.

    Dates are the case this exists for. "15 October 2026" is a real deadline,
    correctly researched from an official page, and it is not a date any of this
    system's code can compare against today.
    """
    value = str(claim.value or "").strip()
    if not value:
        return ["Value is blank"]
    upper = value.upper()
    problems: list[str] = []

    if claim.gate == "INCENTIVE_ENGINE_CLASSIFICATION":
        if upper not in _INCENTIVE_ENGINES:
            problems.append(f"{value!r} is not one of the twelve statutory engines")

    elif claim.gate == "MARKET_CYCLE":
        if upper not in _NOT_YET and not _ISO.match(value):
            problems.append(
                f"{value!r} is not YYYY-MM-DD, NOT_ANNOUNCED or ROLLING"
            )

    elif claim.gate == "FESTIVAL_SECTION" and claim.field == "section_deadlines":
        if upper not in _NOT_YET:
            for line in [x.strip() for x in value.splitlines() if x.strip()]:
                if "|" not in line:
                    problems.append(f"{line[:52]!r} is not 'Section name | YYYY-MM-DD'")
                elif not _HAS_ISO.search(line.split("|", 1)[1]):
                    problems.append(
                        f"{line[:52]!r} carries no YYYY-MM-DD date the engine can read"
                    )

    elif claim.gate == "FESTIVAL_SECTION" and claim.field == "section_rules":
        for line in [x.strip() for x in value.splitlines() if x.strip()]:
            body = line.split("|", 1)[1].strip() if "|" in line else line
            parts = body.split()
            if not parts:
                continue
            if parts[0] == "premiere_requirement":
                if len(parts) < 2 or parts[1].upper() not in _PREMIERE:
                    problems.append(
                        f"{line[:52]!r}: premiere must be WORLD, INTERNATIONAL, "
                        "NATIONAL or NONE"
                    )
            elif not _reads_as_rule(body):
                problems.append(f"{line[:52]!r} names no known operator")

    elif claim.gate == "MARKET_HARD_GATE":
        for line in [x.strip() for x in value.splitlines() if x.strip()]:
            if not _reads_as_rule(line):
                problems.append(f"{line[:52]!r} names no known operator")

    elif claim.gate == "COMPARABLE_TITLE_PROFILE":
        if claim.field == "format":
            if value.lower() not in _FORMATS:
                problems.append(
                    f"{value!r} is not one of {', '.join(sorted(_FORMATS))}"
                )
        elif claim.field in _COMPARABLE_LISTS:
            if not [part for part in value.split(";") if part.strip()]:
                problems.append("Expected one or more semicolon-separated values")
        else:
            problems.append(f"{claim.field!r} is not a comparable title field")

    elif claim.gate == "GRANTS_SPLIT_PARENT":
        if upper != "NONE" and not [t for t in value.split(";") if t.strip()]:
            problems.append("Expected NONE or semicolon-separated successor titles")

    return problems


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def read_workbook(path: Path, *, today: date):
    """Every filled row as a SourceClaim, plus rows that could not be read."""
    from app.modules.reports.verification_ledger import SourceClaim

    wb = load_workbook(path, data_only=True)
    claims: list[SourceClaim] = []
    unreadable: list[tuple[str, int, str]] = []
    blank = 0

    for name in wb.sheetnames:
        ws = wb[name]
        header = [c.value for c in ws[2]] if ws.max_row >= 2 else []
        if "gate" not in (header or []) or "value" not in (header or []):
            continue  # instructions or coverage sheet
        index = {h: n for n, h in enumerate(header)}

        for number, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            answers = [row[index[c]] for c in RESEARCH_COLUMNS]
            if all(a in (None, "") for a in answers):
                blank += 1
                continue

            verified_on = _as_date(row[index["verified_on"]])
            if verified_on is None:
                unreadable.append((name, number, "verified_on is missing or not a date"))
                continue

            claims.append(
                SourceClaim(
                    gate=str(row[index["gate"]] or "").strip(),
                    subject_id=str(row[index["subject_id"]] or "").strip(),
                    field=str(row[index["field"]] or "").strip(),
                    value=(str(row[index["value"]]).strip() if row[index["value"]] is not None else None),
                    source_url=str(row[index["source_url"]] or "").strip(),
                    source_basis=str(row[index["source_basis"]] or "").strip(),
                    verified_on=verified_on,
                    verified_by=str(row[index["verified_by"]] or "").strip(),
                )
            )
    return claims, unreadable, blank


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--apply", action="store_true", help="Record the valid claims")
    parser.add_argument("--show", type=int, default=12, help="How many problems to list")
    args = parser.parse_args()

    from app.modules.reports.verification_ledger import validate_claim

    today = date.today()
    claims, unreadable, blank = read_workbook(args.workbook, today=today)

    valid, rejected, unusable = [], [], []
    for claim in claims:
        problems = validate_claim(claim, today=today)
        if problems:
            rejected.append((claim, problems))
            continue
        shape = value_problems(claim)
        if shape:
            unusable.append((claim, shape))
        else:
            valid.append((claim, ()))

    print(f"Workbook: {args.workbook.name}")
    print(f"  rows with answers : {len(claims)}")
    print(f"  rows left blank   : {blank}")
    print(f"  unreadable rows   : {len(unreadable)}")
    print(f"  valid claims      : {len(valid)}")
    print(f"  rejected claims   : {len(rejected)}")
    print(f"  unusable values   : {len(unusable)}")
    print()

    by_gate = Counter(c.gate for c, _ in valid)
    if by_gate:
        print("Valid by gate:")
        for gate, count in sorted(by_gate.items()):
            print(f"  {gate:36} {count:>4}")
        print()

    if unreadable:
        print("Rows that could not be read:")
        for sheet, number, why in unreadable[: args.show]:
            print(f"  {sheet} row {number}: {why}")
        print()

    if rejected:
        reasons = Counter(p for _, ps in rejected for p in ps)
        print("Why claims were rejected:")
        for reason, count in reasons.most_common():
            print(f"  {count:>4}  {reason}")
        print()
        print("Examples:")
        for claim, problems in rejected[: args.show]:
            print(f"  {claim.gate}/{claim.subject_id}/{claim.field}")
            for problem in problems:
                print(f"      - {problem}")
        print()

    if unusable:
        reasons = Counter(p for _, ps in unusable for p in ps[:1])
        print(
            "Sourced correctly, but the value is in a shape no engine can read. "
            "These are NOT recorded:"
        )
        for reason, count in reasons.most_common(args.show):
            print(f"  {count:>4}  {reason}")
        print()

    needs_qa = [c for c, _ in valid if c.needs_independent_qa]
    if needs_qa:
        print(
            f"{len(needs_qa)} valid claim(s) are on gates requiring independent QA. "
            "They will be recorded, and stay unreadable to any engine until a "
            "reviewer who is NOT their author signs them off."
        )
        print()

    if not args.apply:
        print("Preflight only. Re-run with --apply to record the valid claims as PENDING.")
        raise SystemExit(0 if not (rejected or unreadable or unusable) else 1)

    db_url = os.environ.get("DB_URL")
    if not db_url:
        parser.exit(2, "DB_URL is not set; nothing recorded.\n")

    import sqlalchemy as sa

    from app.modules.reports import verification_store as store

    engine = sa.create_engine(db_url)
    try:
        result = store.record_claims(
            engine, [c for c, _ in valid], apply=True, today=today
        )
    except store.LedgerUnavailable as exc:
        parser.exit(2, f"{exc}\n")

    print(f"Recorded as PENDING : {result.inserted}")
    print(f"Already present     : {result.unchanged}")
    print(f"Conflicts           : {len(result.conflicts)}")
    for claim, existing in result.conflicts[: args.show]:
        print(f"  {claim.gate}/{claim.subject_id}/{claim.field}: "
              f"recorded {existing!r}, workbook says {claim.value!r}")
    print()
    print("Nothing here is approved. Review each claim before any engine reads it.")
    raise SystemExit(0 if result.is_clean else 1)


if __name__ == "__main__":
    main()
