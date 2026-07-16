"""Waterfall regression across EVERY incentive record (implementation plan §4).

Runs the authoritative engine (ReportValidator._compute_corrected_rebate — the
single source of truth the reports, calculator and incentives service all call)
over every row in incentive_programs at five budget points, and checks the
six-step waterfall invariants hold for each:

  1. The engine never raises.
  2. qualifying_spend >= 0 and (unless the row declares an uplift >100%)
     never exceeds the budget.
  3. ATL deduction is non-negative and only reduces qualifying spend.
  4. gross_rebate == qualifying_spend x rate_gross (unless a rebate cap note
     says it was clamped), and never exceeds qualifying spend.
  5. net_rebate <= gross_rebate (a taxable credit nets DOWN, never up).
  6. All amounts are finite, non-negative numbers.

Exit code 0 = every record x budget point passed; 1 = anomalies listed below.
Read-only — never writes to the database. Target DB comes from DB_URL in .env.
"""
from __future__ import annotations

import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from app.modules.reports.validator import ReportValidator

BUDGET_POINTS_GBP = [500_000, 2_000_000, 10_000_000, 50_000_000, 150_000_000]
EPS = 1.0  # pennies-level float tolerance on GBP amounts


def main() -> int:
    load_dotenv()
    engine = create_engine(os.environ["DB_URL"])
    with engine.connect() as conn:
        cols = [r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='incentive_programs'"
        ))]
        rows = [dict(r._mapping) for r in conn.execute(text("SELECT * FROM incentive_programs"))]

    print(f"incentive_programs: {len(rows)} records, {len(cols)} columns")
    territory_incentives: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        territory_incentives[r.get("territory", "")].append(r)

    anomalies: list[str] = []
    zero_rate_rows = 0
    checked = 0

    for row in rows:
        name = f"{row.get('territory','?')} / {row.get('program_name') or row.get('name') or row.get('id')}"
        for budget in BUDGET_POINTS_GBP:
            tag = f"{name} @ £{budget/1e6:g}M"
            try:
                result = ReportValidator._compute_corrected_rebate(
                    row, float(budget), territory_incentives, production_format=None
                )
            except Exception as exc:  # invariant 1
                anomalies.append(f"RAISED  {tag}: {type(exc).__name__}: {exc}")
                continue

            if result is None:
                zero_rate_rows += 1
                continue
            checked += 1

            qs = result["qualifying_spend"]
            qs_before = result["qualifying_spend_before_atl"]
            qs_pct = result.get("qualifying_spend_pct")
            atl = result["atl_deduction_amount"]
            gross = result["gross_rebate"]
            net = result["net_rebate"]
            rate_gross = result["rate_gross"]

            # invariant 6 — sane numbers
            for label, v in (("qs", qs), ("gross", gross), ("net", net), ("atl", atl)):
                if v is None or not math.isfinite(float(v)) or float(v) < -EPS:
                    anomalies.append(f"BADNUM  {tag}: {label}={v!r}")

            # invariant 2 — qualifying spend bounded by budget (unless declared uplift)
            uplift = qs_pct is not None and float(qs_pct) > 100
            if not uplift and qs_before > budget + EPS:
                anomalies.append(f"QS>BUD  {tag}: qs_before_atl £{qs_before:,.0f} > budget")

            # invariant 3 — ATL only reduces
            if qs > qs_before + EPS:
                anomalies.append(f"ATL-UP  {tag}: qs £{qs:,.0f} > pre-ATL £{qs_before:,.0f}")

            # invariant 4 — gross consistent with rate unless clamped by rebate cap
            if not result.get("rebate_cap_note"):
                expected = qs * (float(rate_gross or 0) / 100.0)
                if abs(gross - expected) > max(EPS, expected * 0.001):
                    anomalies.append(
                        f"GROSS!=QSxRATE  {tag}: gross £{gross:,.0f} vs expected £{expected:,.0f}"
                    )
            if gross > qs + EPS and not result.get("rebate_cap_note"):
                anomalies.append(f"GROSS>QS  {tag}: gross £{gross:,.0f} > qs £{qs:,.0f} (rate >100%?)")

            # invariant 5 — net never exceeds gross
            if net > gross + EPS:
                anomalies.append(f"NET>GROSS  {tag}: net £{net:,.0f} > gross £{gross:,.0f}")

    print(f"checked: {checked} record x budget computations "
          f"({zero_rate_rows} zero/absent-rate skips)")
    if anomalies:
        print(f"\n{len(anomalies)} ANOMALIES:")
        for a in anomalies:
            print(f"  {a}")
        return 1
    print("all waterfall invariants hold for every incentive record")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
