"""Compare a stored report's legacy shape against its v2 orchestration. Read-only.

Usage::

    python scripts/compare_report_versions.py --report-id <uuid>
    python scripts/compare_report_versions.py --latest
    python scripts/compare_report_versions.py --sample

The report must have been generated with REPORT_ORCHESTRATION_V2_ENABLED on, so
that both shapes come from the same run against the same ProjectFacts snapshot.
Comparing two separate runs would attribute their input differences to the
rebuild.

Exit codes: 0 when every difference has a rule behind it, 1 when something needs
a human. Neither is a cutover decision — see the output's own first line.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]


def load_stored_report(engine: sa.Engine, report_id: str) -> dict:
    with engine.connect() as conn:
        if not sa.inspect(conn).has_table("reports"):
            raise RuntimeError("reports table is not present in this database")
        row = conn.execute(
            sa.text("SELECT report_data FROM reports WHERE id = :id"),
            {"id": report_id},
        ).first()
    if row is None:
        raise LookupError(f"No report with id {report_id}")
    data = row[0]
    if isinstance(data, str):
        import json

        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError(f"Report {report_id} has no readable report_data")
    return data


def latest_with_payload(engine: sa.Engine, *, scan: int = 50) -> tuple[str, dict]:
    """The newest stored report that carries a v2 payload.

    The payload is looked for in Python rather than with a JSON operator:
    ``report_data`` is ``json`` on this database and ``jsonb`` elsewhere, and
    the isolated tests run on SQLite where neither exists. Fifty rows is a cheap
    scan, and a dialect-specific query is a query that fails somewhere.
    """
    import json

    with engine.connect() as conn:
        if not sa.inspect(conn).has_table("reports"):
            raise RuntimeError("reports table is not present in this database")
        rows = conn.execute(
            sa.text(
                "SELECT id, report_data FROM reports "
                "ORDER BY created_at DESC LIMIT :scan"
            ),
            {"scan": scan},
        ).all()

    for identity, data in rows:
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                continue
        if isinstance(data, dict) and data.get("orchestrationV2"):
            return str(identity), data
    raise LookupError(
        f"None of the {len(rows)} most recent reports carries a v2 payload. "
        "Turn REPORT_ORCHESTRATION_V2_ENABLED on and generate one — the flag "
        "only affects reports produced after it is set."
    )


def _sample_pair() -> tuple[dict, dict]:
    """The worked fixture, so the comparison can be exercised without a database."""
    from app.modules.reports.sample_orchestration import (
        as_payload,
        build_sample_orchestration,
    )

    legacy = {
        "incentiveEstimates": [
            {"territory": "New York", "confirmedIncentive": "$7,650,000"},
            {"territory": "United Kingdom", "confirmedIncentive": "£5,740,000"},
        ],
        "fundingOpportunities": [
            {"name": "Film London Production Finance Market"},
            {"name": "A national production fund"},
        ],
        "executiveSummary": {"headlineNetBudget": "approximately $24,262,500"},
    }
    return legacy, as_payload(build_sample_orchestration())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-id", help="A stored report to compare")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Use the newest stored report that carries a v2 payload",
    )
    parser.add_argument(
        "--sample", action="store_true", help="Compare the worked fixture instead"
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from app.modules.reports.version_comparison import compare, render

    if args.sample:
        legacy, payload = _sample_pair()
    elif args.report_id or args.latest:
        from app.core.config import get_settings

        try:
            engine = sa.create_engine(get_settings().DB_URL)
            if args.latest:
                report_id, legacy = latest_with_payload(engine)
                print(f"Comparing report {report_id}")
            else:
                legacy = load_stored_report(engine, args.report_id)
        except (RuntimeError, LookupError, ValueError) as exc:
            parser.exit(2, f"{exc}\n")
        payload = legacy.get("orchestrationV2")
        if not payload:
            parser.exit(
                2,
                "This report carries no v2 payload. Regenerate it with "
                "REPORT_ORCHESTRATION_V2_ENABLED on, so both shapes come from "
                "one run against one ProjectFacts snapshot.\n",
            )
        snapshot = legacy.get("projectFactsSnapshotId")
        if snapshot and payload.get("projectfacts_snapshot_id") != snapshot:
            parser.exit(
                2,
                "The stored payload was computed against a different "
                "ProjectFacts snapshot. Comparing them would attribute their "
                "input differences to the rebuild.\n",
            )
    else:
        parser.exit(2, "Give --report-id, --latest or --sample\n")

    result = compare(legacy, payload)
    print(render(result))
    raise SystemExit(1 if result.has_unexpected else 0)


if __name__ == "__main__":
    main()
