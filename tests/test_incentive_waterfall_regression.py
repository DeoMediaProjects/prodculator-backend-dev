"""Runs the every-record waterfall regression (implementation plan §4) when a
seeded Postgres is available; skips cleanly otherwise (the pytest suite's own
DB is an unseeded sqlite). scripts/verify_incentive_waterfall.py is the tool;
this wrapper makes CI/staging runs part of the suite.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_incentive_waterfall.py"


def _seeded_postgres_available() -> bool:
    db_url = os.environ.get("DB_URL", "")
    if not db_url.startswith(("postgresql", "postgres")):
        return False
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(db_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM incentive_programs")).scalar()
        return bool(count)
    except Exception:
        return False


@pytest.mark.skipif(
    not _seeded_postgres_available(),
    reason="requires a Postgres with seeded incentive_programs (run vs local/staging DB)",
)
def test_waterfall_invariants_hold_for_every_incentive_record():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, f"waterfall anomalies:\n{result.stdout}\n{result.stderr}"
