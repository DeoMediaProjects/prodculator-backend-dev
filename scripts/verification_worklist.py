"""What still needs verifying before the v2 cutover, per gate. Read-only.

Usage::

    python scripts/verification_worklist.py            # all gates, summary
    python scripts/verification_worklist.py --gate FESTIVAL_SECTION --csv out.csv

The plan document can say a gate is open. This says which records, by ID, with
the official source already on the row where the snapshot carries one — so the
work can be split between people and picked up without re-deriving the list.

It reads the frozen snapshots and, where the ledger table exists, the
verifications already recorded. It writes nothing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data" / "handoff_snapshots"


@dataclass(frozen=True)
class WorkItem:
    gate: str
    subject_id: str
    field: str
    subject_name: str
    #: The source the snapshot already points at, where it has one. Not
    #: evidence — a starting point, so the researcher is not searching from
    #: scratch for a page the freeze already found.
    known_source: str
    reason: str


def _load(name: str):
    return json.loads((SNAPSHOTS / name).read_text(encoding="utf-8"))


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def festival_section_work() -> list[WorkItem]:
    """Every paid-eligible festival needs section-level rules it does not have.

    The snapshot has no section data at all — not a thin column, an absent one.
    A festival-level deadline cannot answer when THIS feature must be submitted,
    because feature, short and episodic windows differ.
    """
    items = []
    for row in _load("festivals_v2_1_2026-09-15.json"):
        if not _truthy(row.get("paid_match_eligible_v2")):
            continue
        gates_done = all(
            _truthy(row.get(field))
            for field in (
                "current_cycle_verified",
                "eligibility_verified",
                "premiere_rules_verified",
            )
        )
        items.append(
            WorkItem(
                gate="FESTIVAL_SECTION",
                subject_id=str(row.get("id")),
                field="sections",
                subject_name=str(row.get("name") or ""),
                known_source=str(row.get("website_url") or ""),
                reason=(
                    "No section-level deadlines or rules recorded"
                    if gates_done
                    else "Record-level gates also incomplete"
                ),
            )
        )
    return items


def market_work() -> list[WorkItem]:
    """Cycles that were never established, and prose that is not yet a rule."""
    items = []
    for row in _load("markets_labs_wip_v1_2026-09-16.json"):
        subject = str(row.get("id"))
        name = str(row.get("name") or "")
        source = str(row.get("source") or "")
        verification = str(row.get("verification") or "").strip().upper()
        if verification == "PROGRAM_VERIFIED_CYCLE_PENDING" or not row.get("deadline"):
            items.append(
                WorkItem(
                    "MARKET_CYCLE", subject, "deadline", name, source,
                    "Programme confirmed, current cycle not established",
                )
            )
        if str(row.get("hard_gates") or "").strip():
            items.append(
                WorkItem(
                    "MARKET_HARD_GATE", subject, "hard_gates", name, source,
                    "Hard gates are prose and need typed rules with independent QA",
                )
            )
    return items


def commercial_work() -> list[WorkItem]:
    """Directory-sourced profiles, and the scope fields still unknown."""
    items = []
    for row in _load("sales_distribution_v1_2026-09-16.json")["companies"]:
        subject = str(row.get("company_id"))
        name = str(row.get("company_name") or "")
        source = str(row.get("source_url") or "")
        basis = str(row.get("source_basis") or "")
        if "director" in basis.lower():
            items.append(
                WorkItem(
                    "COMMERCIAL_COMPANY_PROFILE", subject, "profile", name, source,
                    f"Sourced from a directory listing, not the company: {basis}",
                )
            )
        for field in ("acquisition_stage", "genre_specialties", "festival_market_signal"):
            if str(row.get(field) or "").strip().upper() in {"", "NONE", "UNKNOWN"}:
                items.append(
                    WorkItem(
                        "COMMERCIAL_COMPANY_PROFILE", subject, field, name, source,
                        f"{field.replace('_', ' ')} not established",
                    )
                )
    return items


def grants_work() -> list[WorkItem]:
    """The live rows the freeze does not settle, from the reconciliation."""
    sys.path.insert(0, str(ROOT))
    from app.modules.grants.v2_migration import reconcile

    snapshot = _load("grants_v2_2026-09-04.json")
    mapping = snapshot["migration_maps"]["legacy_id_mapping"]
    # Without a live table to read, the mapped rows themselves are the
    # reviewable universe. The dry run against a real database additionally
    # surfaces live rows the mapping never names.
    live = [
        {"id": row["existing_id"], "title": row["existing_title"]} for row in mapping
    ]
    report = reconcile(live, mapping, source_open_questions=snapshot["open_questions"])
    items = [
        WorkItem(
            "GRANTS_MIGRATION_DECISION",
            verdict.live_id,
            "decision",
            str(verdict.title or ""),
            "",
            "; ".join(verdict.blocking_reasons),
        )
        for verdict in report.blocking
    ]
    for name, entries in (snapshot["open_questions"] or {}).items():
        for entry in entries:
            items.append(
                WorkItem(
                    "GRANTS_MIGRATION_DECISION", str(entry), name, str(entry), "",
                    name.replace("_", " "),
                )
            )
    return items


GATES = {
    "FESTIVAL_SECTION": festival_section_work,
    "MARKET": market_work,
    "COMMERCIAL_COMPANY_PROFILE": commercial_work,
    "GRANTS_MIGRATION_DECISION": grants_work,
}


def _already_verified() -> set[tuple[str, str, str]]:
    """Claims the ledger already holds as verified, so the queue shrinks.

    Returns an empty set when the ledger is unreachable or absent rather than
    failing: the worklist is useful before anyone has recorded anything, and a
    researcher without database access should still be able to see what is
    outstanding.
    """
    sys.path.insert(0, str(ROOT))
    try:
        import sqlalchemy as sa

        from app.core.config import get_settings
        from app.modules.reports import verification_store as store

        engine = sa.create_engine(get_settings().DB_URL)
        return {
            (claim.gate, claim.subject_id, claim.field)
            for claim in store.readable_claims_for_worklist(engine, today=date.today())
        }
    except Exception:  # noqa: BLE001 — see docstring
        return set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", choices=sorted(GATES), help="One gate only")
    parser.add_argument("--csv", type=Path, help="Write the full worklist here")
    parser.add_argument(
        "--include-verified",
        action="store_true",
        help="List every claim, including ones already verified in the ledger",
    )
    args = parser.parse_args()

    selected = [args.gate] if args.gate else sorted(GATES)
    items: list[WorkItem] = []
    for gate in selected:
        items.extend(GATES[gate]())

    if not args.include_verified:
        done = _already_verified()
        if done:
            before = len(items)
            items = [
                item
                for item in items
                if (item.gate, item.subject_id, item.field) not in done
            ]
            print(f"{before - len(items)} claims already verified in the ledger\n")

    by_gate: dict[str, int] = {}
    by_subject: dict[str, set[str]] = {}
    for item in items:
        by_gate[item.gate] = by_gate.get(item.gate, 0) + 1
        by_subject.setdefault(item.gate, set()).add(item.subject_id)

    print(f"Outstanding source verifications as at {date.today().isoformat()}")
    print()
    for gate in sorted(by_gate):
        print(
            f"  {gate:32} {by_gate[gate]:5} claims across "
            f"{len(by_subject[gate]):4} records"
        )
    print()
    print(f"  {'TOTAL':32} {len(items):5} claims")
    print()
    print("Incentive engine classification is measured against the live database")
    print("and is not included here; run the inventory query separately.")

    if args.csv:
        # The researcher columns are emitted blank so this file is filled in
        # place and handed straight to record_verifications.py. A worklist that
        # has to be reshaped into a different CSV before it can be recorded is
        # a step where a subject_id gets mistyped.
        with args.csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "gate",
                    "subject_id",
                    "field",
                    "value",
                    "source_url",
                    "source_basis",
                    "verified_on",
                    "verified_by",
                    "notes",
                    "subject_name",
                    "known_source",
                    "reason",
                ]
            )
            for item in items:
                writer.writerow(
                    [
                        item.gate,
                        item.subject_id,
                        item.field,
                        "",  # value — what the source says this field is
                        "",  # source_url — the official page it was read from
                        "",  # source_basis — what that page actually says
                        "",  # verified_on — ISO date it was read
                        "",  # verified_by — who read it
                        "",  # notes
                        item.subject_name,
                        item.known_source,
                        item.reason,
                    ]
                )
        print(f"\nWorklist written to {args.csv}")
        print("Fill value, source_url, source_basis, verified_on and verified_by,")
        print("then record it with scripts/record_verifications.py")


if __name__ == "__main__":
    main()
