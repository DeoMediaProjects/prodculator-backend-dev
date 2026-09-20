"""Stage cycles from verified research claims. Read-only without --apply.

Usage::

    DB_URL=… venv/Scripts/python scripts/stage_verified_cycles.py
    DB_URL=… venv/Scripts/python scripts/stage_verified_cycles.py --apply

WHAT THIS IS FOR
----------------
``stage_curated_cycles`` loads one hand-curated file of six cycles and refuses
anything whose SHA-256 differs. That is right for a reviewed file and useless
for a ledger, which is meant to grow every time a reviewer signs a claim off.

This is the ledger's path. A researcher records a festival's section deadlines,
a reviewer verifies them, and the sections become cycles the engine can rank.
Until this runs, a fact can be researched, independently QA'd and verified and
still leave Section 09 empty, because the row it describes does not exist.

WHAT IT REFUSES TO STAGE
------------------------
A record the engine handoff has not staged. ``opportunity_catalogue`` reads
names from ``engine_handoff_records``, so a cycle pointing at an unstaged record
would be ranked under its own opaque id. ``stage_engine_handoffs --apply`` runs
first.

A line the parser could not type. Those are reported by subject and by reason,
because a dropped rule is a research finding — someone wrote something the
engine cannot act on — and it needs to reach the person who can rewrite it.

WHY IT REPLACES A CYCLE'S RULES RATHER THAN ADDING TO THEM
-----------------------------------------------------------
A reviewer correcting a mistyped rule records a corrected claim, and the old
rule must stop being evaluated. Rules are therefore rewritten to match the
claim, not merged with what was there. Cycles themselves are never deleted: a
deadline that disappears from a claim leaves a staged cycle behind, and that is
reported rather than tidied away, because a cycle vanishing from the universe is
something a person should decide.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]

CYCLES_TABLE = "opportunity_cycles"
RULES_TABLE = "opportunity_rules"
RECORDS_TABLE = "engine_handoff_records"


@dataclass
class CycleStageResult:
    claims_read: int = 0
    cycles_parsed: int = 0
    rules_parsed: int = 0
    new_cycles: int = 0
    updated_cycles: int = 0
    new_rules: int = 0
    removed_rules: int = 0
    #: Cycles already staged that the current claims no longer describe. Never
    #: deleted here; a record leaving the universe is a person's decision.
    orphaned_cycles: list[str] = field(default_factory=list)
    #: Cycles skipped because the handoff record they point at is not staged.
    unstaged_records: list[str] = field(default_factory=list)
    problems: list = field(default_factory=list)
    applied: bool = False


def cycle_id(kind: str, record_id: str, section_name: str, deadline: date) -> str:
    """The same identity ``stage_curated_cycles`` builds, so the two agree.

    A curated cycle and a researched one for the same festival section and
    deadline are the same cycle, and giving them different ids would stage it
    twice and offer a producer one opportunity under two names.
    """
    return str(uuid5(NAMESPACE_URL, f"{kind}:{record_id}:{section_name}:{deadline}"))


def rule_id(cycle: str, index: int, rule) -> str:
    return str(uuid5(NAMESPACE_URL, f"{cycle}:{index}:{rule.project_field}:{rule.condition}"))


def _cycle_row(parsed) -> dict:
    return {
        "id": cycle_id(parsed.kind, parsed.record_id, parsed.section_name, parsed.cycle_deadline),
        "kind": parsed.kind,
        "record_id": parsed.record_id,
        "section_name": parsed.section_name,
        # Never invented. The research asked when a call closes, not when it
        # opened, and ``observed_open_on`` is what the page was seen showing.
        "cycle_open": None,
        "observed_open_on": parsed.observed_open_on,
        "cycle_deadline": parsed.cycle_deadline,
        "cycle_verified": True,
        # Never True. The pack told researchers to leave anything they could not
        # type entirely, so a typed subset is never a complete rule set, and the
        # engine surfaces that as a condition rather than an eligibility.
        "rules_complete": False,
        "source_url": parsed.source_url,
        "verified_on": parsed.verified_on,
        "premiere_requirement": parsed.premiere_requirement,
    }


def _rule_rows(parsed, identity: str) -> list[dict]:
    return [
        {
            "id": rule_id(identity, index, rule),
            "cycle_id": identity,
            "project_field": rule.project_field,
            "operator": rule.operator,
            "expected": rule.expected,
            "condition": rule.condition,
            "source_url": parsed.source_url,
            "verified_on": parsed.verified_on,
        }
        for index, rule in enumerate(parsed.rules)
    ]


def stage_verified_cycles(
    engine: sa.Engine, *, apply: bool = False, today: date | None = None
) -> CycleStageResult:
    from app.modules.reports import verification_store
    from app.modules.reports.verified_cycles import build_cycles

    today = today or date.today()
    result = CycleStageResult(applied=apply)

    try:
        claims = verification_store.load_claims(engine)
    except verification_store.LedgerUnavailable as exc:
        raise RuntimeError(str(exc)) from exc
    result.claims_read = len(claims)

    parsed, problems = build_cycles(claims, today=today)
    result.problems = problems
    result.cycles_parsed = len(parsed)
    result.rules_parsed = sum(len(item.rules) for item in parsed)

    with engine.begin() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        if not {CYCLES_TABLE, RULES_TABLE} <= tables:
            raise RuntimeError("Apply the engine v2 staging migration first")
        cycles = sa.Table(CYCLES_TABLE, sa.MetaData(), autoload_with=conn)
        rules = sa.Table(RULES_TABLE, sa.MetaData(), autoload_with=conn)
        if "premiere_requirement" not in cycles.c:
            raise RuntimeError("Apply migration z5a6b7c8d9e0 first")
        if "observed_open_on" not in cycles.c:
            raise RuntimeError("Apply the observed-open cycle migration first")

        staged_records = set()
        if RECORDS_TABLE in tables:
            records = sa.Table(RECORDS_TABLE, sa.MetaData(), autoload_with=conn)
            staged_records = {
                (row.kind, row.record_id)
                for row in conn.execute(sa.select(records.c.kind, records.c.record_id))
            }
        # An empty handoff table means nothing has been staged at all, which is
        # a setup problem rather than a claim problem, and naming it here beats
        # reporting every cycle as unstaged.
        if not staged_records:
            raise RuntimeError(
                "No engine handoff records are staged; run "
                "scripts/stage_engine_handoffs.py --apply first"
            )

        usable = []
        for item in parsed:
            if (item.kind, item.record_id) not in staged_records:
                result.unstaged_records.append(f"{item.kind} {item.record_id}")
                continue
            usable.append(item)

        existing = {row["id"]: dict(row) for row in conn.execute(sa.select(cycles)).mappings()}
        wanted_rules: dict[str, list[dict]] = {}
        inserts, updates = [], []
        for item in usable:
            row = _cycle_row(item)
            wanted_rules[row["id"]] = _rule_rows(item, row["id"])
            prior = existing.get(row["id"])
            if prior is None:
                inserts.append(row)
            elif any(not _same(prior.get(key), value) for key, value in row.items()):
                updates.append(row)

        result.new_cycles = len(inserts)
        result.updated_cycles = len(updates)
        result.orphaned_cycles = sorted(set(existing) - set(wanted_rules))

        touched = set(wanted_rules)
        current_rules = {
            row["id"]: dict(row)
            for row in conn.execute(
                sa.select(rules).where(rules.c.cycle_id.in_(touched))
            ).mappings()
        } if touched else {}
        wanted_flat = [row for group in wanted_rules.values() for row in group]
        wanted_ids = {row["id"] for row in wanted_flat}
        new_rules = [row for row in wanted_flat if row["id"] not in current_rules]
        stale_rules = [key for key in current_rules if key not in wanted_ids]
        result.new_rules = len(new_rules)
        result.removed_rules = len(stale_rules)

        if apply:
            if inserts:
                conn.execute(cycles.insert(), inserts)
            for row in updates:
                conn.execute(cycles.update().where(cycles.c.id == row["id"]).values(**row))
            # Stale rules go before new ones: a corrected claim can reuse a
            # field with a different expected value, and the row's id is built
            # from its text, so the two would otherwise both be present.
            if stale_rules:
                conn.execute(rules.delete().where(rules.c.id.in_(stale_rules)))
            if new_rules:
                conn.execute(rules.insert(), new_rules)
    return result


def _same(before, after) -> bool:
    if isinstance(before, date) and not isinstance(before, bool):
        return before == after
    if before is None or after is None:
        return before is after or before == after
    return before == after


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the staged cycles")
    parser.add_argument("--show", type=int, default=20, help="How many problems to list")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))

    db_url = os.environ.get("DB_URL")
    if not db_url:
        from app.core.config import get_settings

        db_url = get_settings().DB_URL
    engine = sa.create_engine(db_url)
    try:
        result = stage_verified_cycles(engine, apply=args.apply)
    except RuntimeError as exc:
        parser.exit(2, f"{exc}\n")

    print("Verified claims -> staged cycles")
    print(f"  claims in the ledger : {result.claims_read}")
    print(f"  cycles the verified claims support : {result.cycles_parsed}")
    print(f"  typed rules          : {result.rules_parsed}")
    print()
    print(f"  cycles new           : {result.new_cycles}")
    print(f"  cycles updated       : {result.updated_cycles}")
    print(f"  rules added          : {result.new_rules}")
    print(f"  rules superseded     : {result.removed_rules}")
    print()

    if result.unstaged_records:
        print(
            f"{len(result.unstaged_records)} cycle(s) point at a record the handoff "
            "has not staged, and were skipped:"
        )
        for item in result.unstaged_records[: args.show]:
            print(f"  {item}")
        print()

    if result.orphaned_cycles:
        print(
            f"{len(result.orphaned_cycles)} staged cycle(s) are no longer described by "
            "any verified claim. They are left in place; decide before removing them."
        )
        print()

    if result.problems:
        print(f"Lines that produced nothing ({len(result.problems)}):")
        for reason, count in Counter(p.reason for p in result.problems).most_common():
            print(f"  {count:>4}  {reason}")
        print()
        print("Examples:")
        for problem in result.problems[: args.show]:
            print(f"  {problem.gate}/{problem.subject_id}: {problem.detail[:70]!r}")
            print(f"      {problem.reason}")
        print()

    if not args.apply:
        print("Preflight only. Re-run with --apply to write these cycles.")
        raise SystemExit(0)
    print("Written. Nothing here is a recommendation; the engines decide that.")


if __name__ == "__main__":
    main()
