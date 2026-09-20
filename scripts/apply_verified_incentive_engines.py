"""Write verified statutory engine classifications onto their programmes.

Usage::

    DB_URL=… venv/Scripts/python scripts/apply_verified_incentive_engines.py
    DB_URL=… venv/Scripts/python scripts/apply_verified_incentive_engines.py --apply

WHAT WAS MISSING
----------------
``qs_engine_type`` is read off the ``incentive_programs`` row — by
``statutory_calculation.engine_of``, by ``calculation_status``, by the wizard's
question resolver. Nothing read it from the verification ledger, and nothing
copied it there.

So the thirty-seven programmes the research pack listed could be classified,
reviewed and verified, and every one of them would still calculate nothing. The
pack said to do this gate first for exactly that reason: Section 07 stays empty
until a programme can calculate, and a programme with no engine cannot.

THE RULE THAT SHAPES THIS
-------------------------
It writes only where the column is empty.

An engine is a statutory classification, and the difference between
``QUALIFIED_LABOUR`` and ``ELIGIBLE_LOCAL_SPEND`` is several million pounds on
the same production. A row that already declares one was classified by someone,
somewhere, and silently replacing that from a spreadsheet is the largest single
change this codebase could make without anyone seeing it happen.

A verified claim that disagrees with a populated column is therefore reported as
a conflict and written nowhere. Two people have now classified the same
programme differently, and that is a question for them rather than for a
last-write-wins rule.

WHAT IT REFUSES
---------------
A value outside the twelve frozen engines, even verified. The vocabulary is
``helpers.QS_ENGINE_TYPES`` and every reader compares against it; a thirteenth
value would sit in the column looking classified and match nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]

TABLE = "incentive_programs"
COLUMN = "qs_engine_type"


@dataclass
class ApplyResult:
    verified_claims: int = 0
    written: list[tuple[str, str, str]] = field(default_factory=list)
    unchanged: int = 0
    #: A verified claim disagreeing with a column that already holds an engine.
    #: Never resolved here — two people classified one programme differently.
    conflicts: list[tuple[str, str, str, str]] = field(default_factory=list)
    unknown_values: list[tuple[str, str]] = field(default_factory=list)
    missing_programmes: list[str] = field(default_factory=list)
    applied: bool = False

    @property
    def is_clean(self) -> bool:
        return not (self.conflicts or self.unknown_values or self.missing_programmes)


def apply_verified_engines(
    engine: sa.Engine, *, apply: bool = False, today: date | None = None
) -> ApplyResult:
    from app.modules.reports import verification_store
    from app.modules.reports.helpers import QS_ENGINE_TYPES
    from app.modules.reports.verification_ledger import (
        GATE_INCENTIVE_ENGINE,
        readable_claims,
    )

    today = today or date.today()
    result = ApplyResult(applied=apply)

    try:
        claims = verification_store.load_claims(engine, gate=GATE_INCENTIVE_ENGINE)
    except verification_store.LedgerUnavailable as exc:
        raise RuntimeError(str(exc)) from exc

    readable = [c for c in readable_claims(claims, today=today) if c.field == COLUMN]
    result.verified_claims = len(readable)

    with engine.begin() as conn:
        if TABLE not in set(sa.inspect(conn).get_table_names()):
            raise RuntimeError(f"{TABLE} is not present in this database")
        table = sa.Table(TABLE, sa.MetaData(), autoload_with=conn)
        if COLUMN not in table.c:
            raise RuntimeError(f"{TABLE} has no {COLUMN} column")

        current = {
            str(row.id): (
                str(row._mapping.get("program") or ""),
                str(row._mapping[COLUMN] or "").strip().upper(),
            )
            for row in conn.execute(
                sa.select(table.c.id, table.c.program, table.c[COLUMN])
            )
        }

        writes: list[tuple[str, str]] = []
        for claim in readable:
            value = str(claim.value or "").strip().upper()
            if value not in QS_ENGINE_TYPES:
                result.unknown_values.append((claim.subject_id, value))
                continue
            row = current.get(claim.subject_id)
            if row is None:
                result.missing_programmes.append(claim.subject_id)
                continue
            name, existing = row
            if not existing:
                writes.append((claim.subject_id, value))
                result.written.append((claim.subject_id, name, value))
            elif existing == value:
                result.unchanged += 1
            else:
                result.conflicts.append((claim.subject_id, name, existing, value))

        if apply:
            for subject_id, value in writes:
                conn.execute(
                    table.update()
                    .where(table.c.id == subject_id)
                    .values(**{COLUMN: value})
                )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the classifications")
    parser.add_argument("--show", type=int, default=40)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))

    db_url = os.environ.get("DB_URL")
    if not db_url:
        from app.core.config import get_settings

        db_url = get_settings().DB_URL

    try:
        result = apply_verified_engines(sa.create_engine(db_url), apply=args.apply)
    except RuntimeError as exc:
        parser.exit(2, f"{exc}\n")

    print("Verified engine classifications -> incentive_programs")
    print(f"  verified claims              : {result.verified_claims}")
    print(f"  programmes {'written' if args.apply else 'to write'}  : {len(result.written)}")
    print(f"  already carrying that engine : {result.unchanged}")
    print(f"  conflicts                    : {len(result.conflicts)}")
    print()

    if result.written:
        print("Classifications:")
        for _, name, value in sorted(result.written, key=lambda item: item[1])[: args.show]:
            print(f"  {value:22} {name[:56]}")
        print()
        for value, count in Counter(v for _, _, v in result.written).most_common():
            print(f"  {count:>4}  {value}")
        print()

    if result.conflicts:
        print(
            "CONFLICTS — the column already holds a different engine. Nothing was "
            "written for these; two people have classified one programme two ways:"
        )
        for subject_id, name, existing, value in result.conflicts[: args.show]:
            print(f"  {name[:50]}")
            print(f"      column says {existing}, the verified claim says {value}")
        print()

    if result.unknown_values:
        print("Verified, and not one of the twelve frozen engines:")
        for subject_id, value in result.unknown_values[: args.show]:
            print(f"  {subject_id}: {value!r}")
        print()

    if result.missing_programmes:
        print(f"{len(result.missing_programmes)} claim(s) name a programme that is not in "
              f"{TABLE}:")
        for subject_id in result.missing_programmes[: args.show]:
            print(f"  {subject_id}")
        print()

    if not args.apply:
        print("Preflight only. Re-run with --apply to write these classifications.")
    else:
        print("Written. These programmes can now produce a statutory figure.")
    raise SystemExit(0 if result.is_clean else 1)


if __name__ == "__main__":
    main()
