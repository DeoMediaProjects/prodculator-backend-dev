"""One workbook for reviewing recorded claims, and for correcting the broken ones.

Usage::

    DB_URL=… venv/Scripts/python scripts/build_review_workbook.py --out review.xlsx
    venv/Scripts/python scripts/build_review_workbook.py --from-workbook PACK.xlsx \\
        --out review.xlsx
    DB_URL=… venv/Scripts/python scripts/build_review_workbook.py --apply review.xlsx

WHY THESE ARE ONE DOCUMENT
--------------------------
``record_verifications.py --review`` moves one claim, named on the command line.
Three hundred and sixty-eight claims is three hundred and sixty-eight
invocations, and a reviewer who cannot see the claim beside the decision is
reviewing a subject id.

The second reason is the one that matters more. A claim can be sourced
perfectly, verified by two people, and still produce nothing — because its value
is in a shape no engine reads, or because its section name does not match the
one the deadline was filed under. Reviewing such a claim to VERIFIED is work
that buys nothing, and the reviewer has no way to know that from the claim
alone.

So each row carries what the shape checker says and what the cycle bridge would
do with it, in two columns beside the decision. A row whose ``engine_outcome``
needs rewriting is a row to send back rather than sign off.

"Stages nothing" and "needs rewriting" are kept apart, because most rows that
stage nothing are right. A hundred and thirty-two market tracks answered
NOT_ANNOUNCED, which is the finding the research asked for and produces no cycle
because there is no call to rank. Flagging those beside a malformed line would
tell a reviewer to send back correct work.

WHAT THIS IS NOT
----------------
It is not a way to approve in bulk. Every row needs a named reviewer, and for
market hard gates and festival section rules a second name that is not the
author's — the ledger checks that by identity when the decision is applied, so a
spreadsheet cannot route around it. A row left blank is left alone.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: What the workbook shows about the claim. Frozen context, never edited.
CONTEXT_COLUMNS = (
    "gate",
    "subject_id",
    "field",
    "value",
    "source_url",
    "source_basis",
    "verified_on",
    "verified_by",
    "needs_second_reviewer",
    "shape_check",
    "engine_outcome",
)
#: What a reviewer fills in.
DECISION_COLUMNS = (
    "decision", "reviewed_by", "qa_by", "sole_owner_attestation", "review_note"
)

_HEADER = PatternFill("solid", fgColor="1F3B57")
_DECISION = PatternFill("solid", fgColor="FFF3CD")
_BLOCKED = PatternFill("solid", fgColor="F8D7DA")

INSTRUCTIONS = [
    ("Reviewing recorded claims", True),
    ("", False),
    ("Every claim here is PENDING. Nothing in this file has reached an engine.", False),
    ("", False),
    ("Fill four columns, or leave the row alone:", False),
    ("  decision       VERIFIED or REJECTED. Blank means undecided; the row is skipped.", False),
    ("  reviewed_by    You.", False),
    ("  qa_by          A second person, for the gates marked needs_second_reviewer.", False),
    ("                 It cannot be the person in verified_by. The ledger checks", False),
    ("                 that by identity when the decision is applied.", False),
    ("  sole_owner_attestation", False),
    ("                 Only when there is no second person. Put qa_by as", False),
    ("                 yourself and write here why: the ledger then accepts the", False),
    ("                 self-signed QA and keeps your reason on the row.", False),
    ("                 Leave it blank and the refusal stands, so the exception", False),
    ("                 is always taken in writing rather than by omission.", False),
    ("                 An audit can then separate a two-person claim from a", False),
    ("                 one-person one, and read why each of the latter was made.", False),
    ("  review_note    Optional. Why, when you reject.", False),
    ("", False),
    ("Two columns are worth reading before you decide:", False),
    ("  shape_check    Whether the value is in a shape any engine can read.", False),
    ("  engine_outcome What the cycle bridge would do with this claim if it were", False),
    ("                 verified today. 'needs rewriting' means verifying it buys", False),
    ("                 nothing — send it back instead. 'no cycle, and none is", False),
    ("                 expected' is a correct answer: verify it.", False),
    ("", False),
    ("Rows shaded red already fail one of those two checks. Verifying them is", False),
    ("work that produces no recommendation.", False),
    ("", False),
    ("Apply your decisions with:", False),
    ("    DB_URL=... venv/Scripts/python scripts/build_review_workbook.py --apply THIS.xlsx", False),
]


def _claims_from_database(engine, *, today: date):
    from app.modules.reports import verification_store as store
    from app.modules.reports.verification_ledger import PENDING

    return [c for c in store.load_claims(engine) if c.review_state == PENDING]


def _claims_from_workbook(path: Path, *, today: date):
    from scripts.ingest_research_workbook import read_workbook

    claims, _, _ = read_workbook(path, today=today)
    return claims


def _shape_check(claim) -> str:
    from scripts.ingest_research_workbook import value_problems

    problems = value_problems(claim)
    return "; ".join(problems) if problems else "ok"


def _engine_outcomes(claims, *, today: date) -> dict[tuple[str, str], str]:
    """What the cycle bridge makes of each claim, keyed by gate and subject.

    The claims are read as if already verified. That is not a shortcut around
    review — it is the only way to answer "what will reviewing this buy" before
    someone spends a week reviewing.
    """
    from app.modules.reports.verification_ledger import SourceClaim
    from app.modules.reports.verified_cycles import build_cycles

    as_verified = [
        SourceClaim(**{
            **claim.__dict__,
            "review_state": "VERIFIED",
            "reviewed_by": "preview",
            "qa_by": "preview-qa" if claim.needs_independent_qa else None,
        })
        for claim in claims
    ]
    cycles, problems = build_cycles(as_verified, today=today)

    staged: Counter = Counter()
    #: Cycles staged without a date, by the state that says why. A reviewer
    #: signing off "the next call is not announced" should be told their claim
    #: is recorded AND that it is not actionable — "stages 1 cycle(s)" alone
    #: reads like the opportunity will appear in a report, and it will not.
    undated: dict[str, Counter] = defaultdict(Counter)
    for cycle in cycles:
        staged[cycle.record_id] += 1
        if cycle.cycle_deadline is None:
            undated[cycle.record_id][cycle.cycle_state] += 1
    # Kept apart. A line nobody has to rewrite — "the next call is not
    # announced" — stages nothing and is still the right answer, and flagging it
    # beside a malformed one would tell a reviewer to send back correct work.
    corrections: dict[str, list[str]] = defaultdict(list)
    settled: dict[str, list[str]] = defaultdict(list)
    for problem in problems:
        target = corrections if problem.needs_correction else settled
        target[problem.subject_id].append(problem.reason)

    outcomes: dict[tuple[str, str], str] = {}
    for claim in claims:
        key = (claim.gate, claim.subject_id)
        if key in outcomes:
            continue
        count = staged.get(claim.subject_id, 0)
        trouble = corrections.get(claim.subject_id, [])
        states = undated.get(claim.subject_id)
        if count and not trouble and states:
            # ROLLING is actionable — no deadline because the call is always
            # open. The other states are recorded and deliberately inert.
            described = ", ".join(
                f"{n} {state.lower().replace('_', ' ')}" for state, n in sorted(states.items())
            )
            actionable = "actionable" if set(states) == {"ROLLING"} else "not actionable"
            outcomes[key] = f"stages {count} cycle(s) with no date ({described}), {actionable}"
        elif count and not trouble:
            outcomes[key] = f"stages {count} cycle(s)"
        elif count:
            outcomes[key] = f"stages {count} cycle(s); {len(trouble)} line(s) need rewriting"
        elif trouble:
            outcomes[key] = "needs rewriting: " + trouble[0]
        elif settled.get(claim.subject_id):
            outcomes[key] = "no cycle, and none is expected: " + settled[claim.subject_id][0]
        else:
            # Gates the cycle bridge does not read at all. Their value goes to
            # the incentive, grants and commercial importers instead.
            outcomes[key] = "not a cycle gate"
    return outcomes


def build(claims, *, today: date) -> Workbook:
    outcomes = _engine_outcomes(claims, today=today)
    book = Workbook()
    sheet = book.active
    sheet.title = "How to review"
    for index, (text, bold) in enumerate(INSTRUCTIONS, start=1):
        cell = sheet.cell(row=index, column=1, value=text)
        cell.font = Font(bold=bold or text.endswith(":"), size=13 if bold else 11)
    sheet.column_dimensions["A"].width = 100

    by_gate: dict[str, list] = defaultdict(list)
    for claim in claims:
        by_gate[claim.gate].append(claim)

    for gate in sorted(by_gate):
        page = book.create_sheet(gate[:31])
        page.cell(row=1, column=1, value=f"{gate} — {len(by_gate[gate])} claim(s) to review")
        page.cell(row=1, column=1).font = Font(bold=True, size=12)
        headers = [*CONTEXT_COLUMNS, *DECISION_COLUMNS]
        for column, name in enumerate(headers, start=1):
            cell = page.cell(row=2, column=column, value=name)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = _HEADER
            cell.alignment = Alignment(vertical="center", wrap_text=True)

        for offset, claim in enumerate(
            sorted(by_gate[gate], key=lambda c: (c.subject_id, c.field))
        ):
            row = 3 + offset
            shape = _shape_check(claim)
            outcome = outcomes.get((claim.gate, claim.subject_id), "")
            values = (
                claim.gate,
                claim.subject_id,
                claim.field,
                str(claim.value or ""),
                claim.source_url,
                claim.source_basis,
                str(claim.verified_on),
                claim.verified_by,
                "YES" if claim.needs_independent_qa else "",
                shape,
                outcome,
            )
            for column, value in enumerate(values, start=1):
                cell = page.cell(row=row, column=column, value=value)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            blocked = shape != "ok" or "needs rewriting" in outcome
            for column in range(len(CONTEXT_COLUMNS) + 1, len(headers) + 1):
                cell = page.cell(row=row, column=column, value=None)
                cell.fill = _BLOCKED if blocked else _DECISION

        widths = {"value": 46, "source_url": 40, "source_basis": 40,
                  "shape_check": 34, "engine_outcome": 40, "subject_id": 38}
        for column, name in enumerate(headers, start=1):
            page.column_dimensions[get_column_letter(column)].width = widths.get(name, 16)
        page.freeze_panes = "A3"
    return book


def _decisions(path: Path) -> list[dict]:
    book = load_workbook(path, data_only=True)
    rows = []
    for name in book.sheetnames:
        page = book[name]
        header = [c.value for c in page[2]] if page.max_row >= 2 else []
        if "gate" not in (header or []) or "decision" not in (header or []):
            continue
        index = {h: n for n, h in enumerate(header)}
        for number, raw in enumerate(page.iter_rows(min_row=3, values_only=True), start=3):
            decision = str(raw[index["decision"]] or "").strip().upper()
            if not decision:
                continue
            rows.append({
                "sheet": name,
                "row": number,
                "gate": str(raw[index["gate"]] or "").strip(),
                "subject_id": str(raw[index["subject_id"]] or "").strip(),
                "field": str(raw[index["field"]] or "").strip(),
                "decision": decision,
                "reviewed_by": str(raw[index["reviewed_by"]] or "").strip(),
                "qa_by": str(raw[index["qa_by"]] or "").strip() or None,
                "sole_owner_attestation": (
                    str(raw[index["sole_owner_attestation"]] or "").strip() or None
                ),
            })
    return rows


def apply_decisions(engine, path: Path, *, today: date, apply: bool):
    """Read every filled decision and hand them to the ledger in one batch.

    Batched deliberately. The per-claim path reflected the whole table twice for
    every decision, so a file of 245 took longer to write than it had taken to
    review. ``review_many`` validates each decision exactly as ``review`` does
    and collects refusals rather than raising, so one self-signed QA still does
    not discard the sound decisions filed beside it.
    """
    from app.modules.reports import verification_store as store
    from app.modules.reports.verification_ledger import REJECTED, VERIFIED

    def _where(row) -> str:
        return (
            f"{row['sheet']} row {row['row']} "
            f"({row['gate']}/{row['subject_id']}/{row['field']})"
        )

    applied, refused = [], []
    decisions, sites = [], {}
    for row in _decisions(path):
        if row["decision"] not in {VERIFIED, REJECTED}:
            refused.append((_where(row), f"{row['decision']!r} is not VERIFIED or REJECTED"))
            continue
        if not row["reviewed_by"]:
            refused.append((_where(row), "records no reviewer"))
            continue
        decision = store.ReviewDecision(
            gate=row["gate"],
            subject_id=row["subject_id"],
            field=row["field"],
            reviewer=row["reviewed_by"],
            state=row["decision"],
            qa_by=row["qa_by"],
            sole_owner_attestation=row["sole_owner_attestation"],
        )
        decisions.append(decision)
        sites[decision] = _where(row)

    if decisions:
        try:
            outcome = store.review_many(engine, decisions, today=today, apply=apply)
        except store.LedgerUnavailable as exc:
            return applied, refused + [("the ledger", str(exc))]
        applied += [(sites[d], d.state) for d, _ in outcome.reviewed]
        refused += [(sites[d], why) for d, why in outcome.refused]
    return applied, refused


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Where to write the review workbook")
    parser.add_argument(
        "--from-workbook", type=Path,
        help="Build from a filled research pack instead of the ledger (no DB needed)",
    )
    parser.add_argument("--apply", type=Path, help="Read decisions from a filled review workbook")
    parser.add_argument("--commit", action="store_true", help="With --apply, write them")
    parser.add_argument("--show", type=int, default=20)
    args = parser.parse_args()
    today = date.today()

    def _engine():
        import sqlalchemy as sa

        db_url = os.environ.get("DB_URL")
        if not db_url:
            from app.core.config import get_settings

            db_url = get_settings().DB_URL
        return sa.create_engine(db_url)

    if args.apply:
        applied, refused = apply_decisions(
            _engine(), args.apply, today=today, apply=args.commit
        )
        print(f"Decisions read : {len(applied) + len(refused)}")
        print(f"  {'applied' if args.commit else 'would apply'} : {len(applied)}")
        print(f"  refused        : {len(refused)}")
        if refused:
            print()
            print("Refused:")
            for where, why in refused[: args.show]:
                print(f"  {where}")
                print(f"      {why}")
        if not args.commit:
            print()
            print("Preflight only. Re-run with --commit to write these decisions.")
        return

    if not args.out:
        parser.exit(2, "Give --out for a new review workbook, or --apply a filled one\n")

    if args.from_workbook:
        claims = _claims_from_workbook(args.from_workbook, today=today)
        source = args.from_workbook.name
    else:
        claims = _claims_from_database(_engine(), today=today)
        source = "the ledger"

    if not claims:
        parser.exit(2, f"No pending claims in {source}.\n")

    book = build(claims, today=today)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    book.save(args.out)

    outcomes = _engine_outcomes(claims, today=today)
    blocked = sum(
        1 for c in claims
        if _shape_check(c) != "ok"
        or "needs rewriting" in outcomes.get((c.gate, c.subject_id), "")
    )
    print(f"Review workbook: {args.out}")
    print(f"  claims from {source}: {len(claims)}")
    print(f"  claims a reviewer can sign off usefully: {len(claims) - blocked}")
    print(f"  claims that would stage nothing as written: {blocked}")
    print()
    for gate, count in sorted(Counter(c.gate for c in claims).items()):
        print(f"  {gate:36} {count:>4}")
    print()
    print("Nothing is approved by this file. Decisions are applied with --apply.")


if __name__ == "__main__":
    main()
