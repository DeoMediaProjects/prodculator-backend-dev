"""Let the wizard ask a programme for the statutory figures it calculates from.

Usage::

    DB_URL=… venv/Scripts/python scripts/enable_programme_questions.py
    DB_URL=… venv/Scripts/python scripts/enable_programme_questions.py --apply

WHY A REPORT SHOWS NO REBATE FIGURE
-----------------------------------
``statutory_calculation`` produces an amount only from the statutory inputs a
programme's engine names. Those inputs reach it in a scenario's
``calculation_inputs``, and that list is filled by the producer answering the
wizard. Three things have to be true before the wizard asks:

1. the programme resolves to a jurisdiction, because ``programmes_for`` scopes by
   ``jurisdiction_country`` and ``jurisdiction_subdivision`` and a row with
   neither is in scope for nothing;
2. the programme has a ``programme_id``, because declared inputs are keyed by it;
3. a ``programme_required_inputs`` row exists for each input its engine needs.

Forty-eight of fifty-seven programmes failed the first, so Mexico, New York,
Italy and most of the catalogue were invisible to the wizard: no question, no
answer, no figure, and a report that said "needs a cost breakdown" about a cost
breakdown nobody had been asked for.

This fixes all three together, and only together. Making a programme visible
without giving it a question would put a card in the wizard with nothing on it,
which the scenario service's own docstring calls out as the worse failure.

WHAT IT WILL NOT DO
-------------------
It never overwrites a value that is already set. The nine programmes that work
today carry hand-written labels and help text — "the provincial credit is 36
percent of this figure, and it counts as assistance that reduces the federal
calculation" — and a generated label would be a downgrade.

It resolves a jurisdiction through ``resolve_jurisdiction``, the same function
the wizard uses. Deriving the code any other way would let a programme be stored
under one jurisdiction and looked up under another.

A territory that will not resolve is reported and skipped. A guess there is a
programme filed in the wrong country.

MULTI_BUCKET is skipped. Its buckets are programme-specific by definition —
``ENGINE_REQUIRED_INPUTS`` records none for it — so there is nothing to derive
and someone has to declare which cost buckets that programme sums.

ABOUT THE GENERATED WORDING
---------------------------
``v2_question_resolver`` says the engine default is used "never to invent a
question whose wording nobody reviewed". The labels here are not invented: they
come from ``CANONICAL_INPUTS``, the reviewed registry of what each statutory
input is. Help text is left empty rather than generated, because a sentence
about a specific programme's rules is exactly what nobody has written yet, and a
plausible-sounding one would be worse than none.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]

TABLE = "incentive_programs"
INPUTS_TABLE = "programme_required_inputs"

#: Engines with no spend base to ask for. An investor shelter's value depends on
#: the investor's position and a competitive grant's on a funding decision.
NON_SPEND = frozenset({"INVESTOR_TAX_SHELTER", "COMPETITIVE_GRANT", "NO_PROGRAMME"})

#: What a row records when its input is absent. Matches the nine hand-written
#: rows already in the table.
MISSING_BEHAVIOR = "requires_cost_breakdown"
SCHEMA_VERSION = "1"

#: ``programme_required_inputs.id`` is varchar(64) and holds ``slug:input_key``.
#: The longest canonical key is ``qualified_production_expenditure`` at 32
#: characters, so a slug has 31 to work with. Discovered the hard way: the first
#: run built slugs from whole programme names and Postgres refused the insert.
MAX_SLUG = 64 - 1 - 32

#: Words that appear in most programme names and distinguish none of them.
#: Dropped only when a slug is too long, so short names keep their full wording.
_FILLER: frozenset[str] = frozenset(
    {
        "TAX", "CREDIT", "CREDITS", "FILM", "FILMS", "PRODUCTION", "PRODUCTIONS",
        "INCENTIVE", "INCENTIVES", "PROGRAMME", "PROGRAM", "FUND", "SCHEME",
        "FOR", "AND", "THE", "OF", "A", "TO", "REBATE", "AUDIOVISUAL",
        "TELEVISION", "TV", "INTERNATIONAL", "NATIONAL", "FEDERAL",
    }
)


@dataclass
class EnableResult:
    jurisdictions: list[tuple[str, str, str | None]] = field(default_factory=list)
    slugs: list[tuple[str, str]] = field(default_factory=list)
    questions: list[tuple[str, str, str]] = field(default_factory=list)
    unresolved: list[tuple[str, str]] = field(default_factory=list)
    multi_bucket: list[tuple[str, str]] = field(default_factory=list)
    slug_collisions: list[tuple[str, str]] = field(default_factory=list)
    already_answerable: int = 0
    applied: bool = False

    @property
    def is_clean(self) -> bool:
        return not (self.unresolved or self.slug_collisions)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(text or "").upper()).strip("_")


def programme_slug(row: dict, jurisdiction, taken: set[str]) -> str | None:
    """A stable join key for a programme that has none.

    Built from the jurisdiction code and the programme's own name, in the shape
    the nine existing rows use (``GB_AVEC``, ``CA_BC_PSTC``). It only has to be
    stable and unique — it is a join key, not a label — and a collision is
    returned as None rather than resolved by appending a number, because two
    programmes colliding usually means they are duplicates worth looking at.
    """
    prefix = _slug(jurisdiction.subdivision_id or jurisdiction.territory_id)
    words = [w for w in _slug(row.get("program")).split("_") if w]

    candidate = f"{prefix}_{'_'.join(words)}"
    if len(candidate) > MAX_SLUG:
        # Too long, so drop the words that distinguish nothing. Tried in this
        # order rather than truncating first because "IT_ITALIAN_FOREIGN" reads
        # as a programme and "IT_ITALIAN_TAX_CREDIT_FOR_FORE" reads as a bug.
        kept = [w for w in words if w not in _FILLER] or words
        candidate = f"{prefix}_{'_'.join(kept)}"
    candidate = candidate[:MAX_SLUG].strip("_")
    return None if not candidate or candidate in taken else candidate


def plan(engine: sa.Engine) -> EnableResult:
    from app.modules.incentives.v2_contracts import (
        CANONICAL_INPUTS,
        ENGINE_REQUIRED_INPUTS,
    )
    from app.modules.incentives.v2_jurisdictions import (
        UnknownJurisdiction,
        resolve_jurisdiction,
    )

    result = EnableResult()
    with engine.connect() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        for required in (TABLE, INPUTS_TABLE):
            if required not in tables:
                raise RuntimeError(f"{required} is not present in this database")
        rows = [dict(r) for r in conn.execute(sa.select(sa.table(
            TABLE,
            sa.column("id"), sa.column("programme_id"), sa.column("program"),
            sa.column("territory"), sa.column("status"), sa.column("qs_engine_type"),
            sa.column("jurisdiction_country"), sa.column("jurisdiction_subdivision"),
        ))).mappings()]
        declared: dict[str, set[str]] = {}
        for row in conn.execute(sa.text(
            f"SELECT programme_id, input_key FROM {INPUTS_TABLE}"
        )):
            declared.setdefault(str(row[0]), set()).add(str(row[1]))

    taken = {str(r["programme_id"]).strip() for r in rows if r.get("programme_id")}

    for row in rows:
        status = str(row.get("status") or "active").strip().lower()
        if status not in ("active", ""):
            continue
        engine_type = str(row.get("qs_engine_type") or "").strip().upper()
        name = str(row.get("program") or row["id"])
        territory = str(row.get("territory") or "")

        try:
            jurisdiction = resolve_jurisdiction(territory)
        except UnknownJurisdiction:
            result.unresolved.append((name, territory))
            continue

        if not str(row.get("jurisdiction_country") or "").strip():
            result.jurisdictions.append(
                (row["id"], jurisdiction.territory_id, jurisdiction.subdivision_id)
            )

        slug = str(row.get("programme_id") or "").strip()
        if not slug:
            slug = programme_slug(row, jurisdiction, taken)
            if slug is None:
                result.slug_collisions.append((name, territory))
                continue
            taken.add(slug)
            result.slugs.append((row["id"], slug))

        if engine_type in NON_SPEND or not engine_type:
            continue
        if engine_type == "MULTI_BUCKET":
            if not declared.get(slug):
                result.multi_bucket.append((name, territory))
            continue

        have = declared.get(slug, set())
        needed = [
            key for key in ENGINE_REQUIRED_INPUTS.get(engine_type, ())
            if key not in have
        ]
        if not needed:
            result.already_answerable += 1
            continue
        for key in needed:
            result.questions.append((slug, key, CANONICAL_INPUTS.get(key, key)))

    return result


def apply_plan(engine: sa.Engine, result: EnableResult) -> None:
    programmes = sa.Table(TABLE, sa.MetaData(), autoload_with=engine)
    inputs = sa.Table(INPUTS_TABLE, sa.MetaData(), autoload_with=engine)
    # Batched. Eighty-odd single-row UPDATEs over a remote proxy is eighty-odd
    # round trips inside one transaction, and the connection dropped partway
    # through the first attempt.
    with engine.begin() as conn:
        if result.jurisdictions:
            conn.execute(
                programmes.update()
                .where(programmes.c.id == sa.bindparam("b_id"))
                .values(
                    jurisdiction_country=sa.bindparam("b_country"),
                    jurisdiction_subdivision=sa.bindparam("b_subdivision"),
                ),
                [
                    {"b_id": identity, "b_country": country, "b_subdivision": subdivision}
                    for identity, country, subdivision in result.jurisdictions
                ],
            )
        if result.slugs:
            conn.execute(
                programmes.update()
                .where(programmes.c.id == sa.bindparam("b_id"))
                .values(programme_id=sa.bindparam("b_slug")),
                [{"b_id": identity, "b_slug": slug} for identity, slug in result.slugs],
            )
        if result.questions:
            conn.execute(inputs.insert(), [
                {
                    "id": f"{slug}:{key}",
                    "programme_id": slug,
                    "input_key": key,
                    "label": label,
                    "input_type": "currency",
                    "required_for_exact": True,
                    # Left empty deliberately. A sentence about this programme's
                    # own rules is what nobody has written, and a plausible one
                    # would be worse than none.
                    "help_text": "",
                    "missing_input_behavior": MISSING_BEHAVIOR,
                    "calculation_input_schema_version": SCHEMA_VERSION,
                }
                for slug, key, label in result.questions
            ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the changes")
    parser.add_argument("--show", type=int, default=25)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))

    db_url = os.environ.get("DB_URL")
    if not db_url:
        from app.core.config import get_settings

        db_url = get_settings().DB_URL
    engine = sa.create_engine(db_url)
    try:
        result = plan(engine)
    except RuntimeError as exc:
        parser.exit(2, f"{exc}\n")

    verb = "written" if args.apply else "to write"
    print("Letting the wizard ask for statutory figures")
    print(f"  jurisdictions resolved {verb} : {len(result.jurisdictions)}")
    print(f"  programme slugs {verb}        : {len(result.slugs)}")
    print(f"  questions {verb}              : {len(result.questions)}")
    print(f"  programmes already answerable : {result.already_answerable}")
    print()

    if result.questions:
        print("Questions by statutory input:")
        for key, count in Counter(k for _, k, _ in result.questions).most_common():
            print(f"  {count:>4}  {key}")
        print()

    if result.multi_bucket:
        print(
            f"{len(result.multi_bucket)} MULTI_BUCKET programme(s) still need a human "
            "to declare which cost buckets they sum:"
        )
        for name, territory in result.multi_bucket[: args.show]:
            print(f"  {str(territory)[:18]:18} {name[:56]}")
        print()

    if result.unresolved:
        print(f"{len(result.unresolved)} programme(s) name a territory that will not resolve:")
        for name, territory in result.unresolved[: args.show]:
            print(f"  {str(territory)[:18]:18} {name[:56]}")
        print()

    if result.slug_collisions:
        print(f"{len(result.slug_collisions)} programme(s) would collide on a slug:")
        for name, territory in result.slug_collisions[: args.show]:
            print(f"  {str(territory)[:18]:18} {name[:56]}")
        print()

    if not args.apply:
        print("Preflight only. Re-run with --apply to write these.")
    else:
        apply_plan(engine, result)
        print(
            "Written. The wizard can now ask these programmes for their base; a "
            "figure still needs the producer to answer."
        )
    raise SystemExit(0 if result.is_clean else 1)


if __name__ == "__main__":
    main()
