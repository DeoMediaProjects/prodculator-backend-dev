"""Record researched source verifications from CSV. Read-only without --apply.

Usage::

    python scripts/record_verifications.py claims.csv              # preflight
    python scripts/record_verifications.py claims.csv --apply
    python scripts/record_verifications.py --review GATE/SUBJECT/FIELD \\
        --reviewer someone [--qa-by someone-else]

The CSV columns match what ``verification_worklist.py --csv`` emits, plus the
four a researcher fills in::

    gate, subject_id, field, value, source_url, source_basis,
    verified_on, verified_by, notes

Claims arrive PENDING however confident their author is. Reaching VERIFIED takes
a separate ``--review`` by a named reviewer, and for market rules and festival
sections that reviewer cannot be the author. Nothing recorded here reaches an
engine until it has been reviewed.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_COLUMNS = (
    "gate",
    "subject_id",
    "field",
    "value",
    "source_url",
    "source_basis",
    "verified_on",
    "verified_by",
)


def read_claims(path: Path):
    from app.modules.reports.verification_ledger import SourceClaim

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")
        claims = []
        for line, row in enumerate(reader, start=2):
            raw_date = (row.get("verified_on") or "").strip()
            try:
                verified_on = date.fromisoformat(raw_date[:10]) if raw_date else None
            except ValueError:
                raise ValueError(
                    f"{path.name} line {line}: {raw_date!r} is not an ISO date"
                ) from None
            if verified_on is None:
                raise ValueError(f"{path.name} line {line}: verified_on is required")
            claims.append(
                SourceClaim(
                    gate=(row.get("gate") or "").strip(),
                    subject_id=(row.get("subject_id") or "").strip(),
                    field=(row.get("field") or "").strip(),
                    value=(row.get("value") or "").strip() or None,
                    source_url=(row.get("source_url") or "").strip(),
                    source_basis=(row.get("source_basis") or "").strip(),
                    verified_on=verified_on,
                    verified_by=(row.get("verified_by") or "").strip(),
                    notes=(row.get("notes") or "").strip() or None,
                )
            )
        return claims


def _render(result) -> str:
    lines = [
        "Recorded as PENDING — nothing here reaches an engine until it is reviewed",
        "",
        f"  New claims:        {result.inserted}",
        f"  Already recorded:  {result.unchanged}",
        f"  Rejected:          {len(result.rejected)}",
        f"  Conflicts:         {len(result.conflicts)}",
        "",
    ]
    if result.rejected:
        lines.append("Rejected claims:")
        for claim, problems in result.rejected:
            lines.append(f"  {claim.gate}/{claim.subject_id}/{claim.field}")
            for problem in problems:
                lines.append(f"      - {problem}")
        lines.append("")
    if result.conflicts:
        lines.append(
            "Conflicts — a claim already exists for this field with a different "
            "value. Resolve it deliberately rather than re-importing:"
        )
        for claim, existing in result.conflicts:
            lines.append(
                f"  {claim.gate}/{claim.subject_id}/{claim.field}: "
                f"recorded {existing!r}, CSV says {claim.value!r}"
            )
        lines.append("")
    lines.append(
        "Applied." if result.applied else "Preflight only — re-run with --apply."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", nargs="?", type=Path, help="Claims to record")
    parser.add_argument("--apply", action="store_true", help="Insert the new claims")
    parser.add_argument(
        "--review", help="Mark one claim reviewed, as GATE/SUBJECT_ID/FIELD"
    )
    parser.add_argument("--reviewer", help="Who performed the review")
    parser.add_argument("--qa-by", help="Independent QA signatory, where required")
    parser.add_argument(
        "--reject", action="store_true", help="Reject rather than verify"
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from app.core.config import get_settings
    from app.modules.reports import verification_store as store
    from app.modules.reports.verification_ledger import REJECTED, VERIFIED

    engine = sa.create_engine(get_settings().DB_URL)
    today = date.today()

    if args.review:
        if not args.reviewer:
            parser.exit(2, "--review needs --reviewer\n")
        try:
            gate, subject_id, field_name = args.review.split("/", 2)
        except ValueError:
            parser.exit(2, "--review takes GATE/SUBJECT_ID/FIELD\n")
        try:
            claim = store.review(
                engine,
                gate=gate,
                subject_id=subject_id,
                field_name=field_name,
                reviewer=args.reviewer,
                state=REJECTED if args.reject else VERIFIED,
                qa_by=args.qa_by,
                today=today,
            )
        except (LookupError, ValueError, store.LedgerUnavailable) as exc:
            parser.exit(2, f"{exc}\n")
        print(f"{args.review} is now {claim.review_state}, reviewed by {args.reviewer}")
        return

    if not args.csv_path:
        parser.exit(2, "Give a CSV to record, or --review a recorded claim\n")

    try:
        claims = read_claims(args.csv_path)
        result = store.record_claims(engine, claims, apply=args.apply, today=today)
    except (ValueError, OSError, store.LedgerUnavailable) as exc:
        parser.exit(2, f"{exc}\n")

    print(_render(result))
    raise SystemExit(0 if result.is_clean else 1)


if __name__ == "__main__":
    main()
