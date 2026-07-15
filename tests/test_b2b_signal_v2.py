"""Folds the B2B handoff pack's 17 verification checks into the pytest suite.

The checks live in tests/b2b_signal_v2_checks.py (delivered as
test_b2b_signal_v2_standalone.py in the handoff pack). That file is a
script — it stubs heavy deps via tests/_stub_boot.py, runs its assertions at
import time and calls sys.exit — so it is executed in a subprocess rather than
imported, keeping its stubbed module registry away from the rest of the suite.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKS_SCRIPT = REPO_ROOT / "tests" / "b2b_signal_v2_checks.py"


def test_b2b_signal_v2_verification_checks():
    env = os.environ.copy()
    env.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters-long")
    # The script does sys.path.insert(0, ".") to find _stub_boot, so run from
    # tests/ and put the repo root on PYTHONPATH for the app.* imports.
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, str(CHECKS_SCRIPT)],
        cwd=str(REPO_ROOT / "tests"),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"B2B v2 checks failed:\n{output}"
    assert "17 passed, 0 failed" in output, f"Expected 17/17 checks passing:\n{output}"
