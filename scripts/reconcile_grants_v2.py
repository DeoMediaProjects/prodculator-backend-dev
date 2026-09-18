"""Dry-run the Grants v2 migration against the live grant table. Read-only.

Usage::

    python scripts/reconcile_grants_v2.py

This never writes. It has no ``--apply``, on purpose: the handoff's migration
instructions require a restorable backup and a reviewed before/after diff before
anything is imported, and a script that can both report and import invites the
second step to be taken on the strength of the first. Importing is a separate,
later, explicitly reviewed operation.

What it prints is the answer to one question — for every row currently live,
does the freeze say what becomes of it? — plus the rows where it does not. A
non-zero exit means the migration is not ready to rehearse, not that the script
failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "handoff_snapshots" / "grants_v2_2026-09-04.json"

#: The snapshot this reconciliation was written against. A regenerated snapshot
#: from a different master is a different migration.
SNAPSHOT_SHA256 = "c10eea198e0ba15947e678a72779818fd6f445bce3ede5bdadb4ca057af94237"

LIVE_TABLE = "grant_opportunities"


def load_snapshot(path: Path = SNAPSHOT, *, expected_sha256: str | None = None) -> dict:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError(
            f"{path.name}: content hash differs from the reviewed snapshot"
        )
    return json.loads(raw)


def read_live_rows(engine: sa.Engine) -> list[dict]:
    """Only ``id`` and ``title``. A reconciliation needs no more than that.

    Selecting the whole row would pull grant terms into a report that is
    circulated for review, and none of them bear on whether the freeze names the
    record.
    """
    with engine.connect() as conn:
        if not sa.inspect(conn).has_table(LIVE_TABLE):
            raise RuntimeError(f"{LIVE_TABLE} is not present in this database")
        result = conn.execute(sa.text(f"SELECT id, title FROM {LIVE_TABLE}"))
        return [{"id": str(row[0]), "title": row[1]} for row in result]


def build_report(snapshot: dict, live_rows: list[dict]):
    from app.modules.grants.v2_migration import reconcile

    return reconcile(
        live_rows,
        snapshot.get("migration_maps", {}).get("legacy_id_mapping", []),
        source_open_questions=snapshot.get("open_questions") or {},
    )


def render(report) -> str:
    lines = [
        "Grants v2 migration dry run — read-only, nothing was written",
        "",
        f"Live rows inspected:        {report.live_row_count}",
        f"Retained as live matches:   {len(report.retained)}",
        f"Of those, paid-safe today:  {len(report.paid_safe)}",
        f"Blocking findings:          {len(report.blocking)}",
        f"Mappings with no live row:  {len(report.mappings_without_a_live_row)}",
        "",
    ]

    if report.blocking:
        lines.append("Live rows the freeze does not settle:")
        for verdict in report.blocking:
            title = verdict.title or "(untitled)"
            lines.append(f"  {verdict.live_id}  {title}")
            for reason in verdict.blocking_reasons:
                lines.append(f"      - {reason}")
        lines.append("")

    if report.mappings_without_a_live_row:
        lines.append(
            "Mapping entries naming a live ID that is no longer in the table:"
        )
        for mapped_id in report.mappings_without_a_live_row:
            lines.append(f"  {mapped_id}")
        lines.append("")

    open_questions = {k: v for k, v in report.source_open_questions.items() if v}
    if open_questions:
        lines.append("Open questions carried from the frozen source:")
        for name, items in open_questions.items():
            lines.append(f"  {name.replace('_', ' ')}: {len(items)}")
            for item in items:
                lines.append(f"      - {item}")
        lines.append("")

    lines.append(
        "READY: every live row has a settled destination."
        if report.is_ready_to_import
        else "NOT READY: resolve the findings above in the source maps before import."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, default=SNAPSHOT, help="Reviewed snapshot path"
    )
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from app.core.config import get_settings

    try:
        snapshot = load_snapshot(
            args.snapshot,
            expected_sha256=SNAPSHOT_SHA256 if args.snapshot == SNAPSHOT else None,
        )
        engine = sa.create_engine(get_settings().DB_URL)
        report = build_report(snapshot, read_live_rows(engine))
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(2, f"{exc}\n")

    print(render(report))
    raise SystemExit(0 if report.is_ready_to_import else 1)


if __name__ == "__main__":
    main()
