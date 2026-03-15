#!/usr/bin/env python3
"""Quick debug: dump one crew_costs row to inspect all columns."""
import sys
sys.path.insert(0, ".")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.core.config import get_settings

engine = create_engine(get_settings().DB_URL)
with Session(engine) as s:
    row = s.execute(text("SELECT * FROM crew_costs LIMIT 1")).mappings().first()
    if row:
        print("=== crew_costs sample row ===")
        for k, v in row.items():
            print(f"  {k:30s} = {v!r}")
    else:
        print("(empty table)")

    print()
    count = s.execute(text("SELECT COUNT(*) FROM crew_costs")).scalar()
    print(f"Total rows: {count}")

    if count and count > 0:
        print()
        print("=== All territories + roles ===")
        results = s.execute(text(
            "SELECT country, role, day_rate, week_rate, union_rate_cents, non_union_rate_cents "
            "FROM crew_costs ORDER BY country, role LIMIT 20"
        )).fetchall()
        for r in results:
            print(f"  {r}")
