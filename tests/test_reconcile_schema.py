"""The schema reconciler must be able to create a table the models declare.

This is the gap that took the audit trail down in production: admin_audit_logs
was absent, and the reconciler detected it but printed "run alembic upgrade head"
without creating anything. Against a create_all-provisioned database that
instruction does not work, so the table stayed missing and every audit endpoint
returned 500.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import app.models.sql_models  # noqa: F401  (registers every table on the metadata)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "reconcile_schema.py"

# The table whose absence broke production. Asserted specifically, rather than
# only checking that some tables appeared, so a regression here is unambiguous.
AUDIT_TABLE = "admin_audit_logs"


def _run(db_url: str, *args: str) -> str:
    """Run the reconciler against *db_url* and return its stdout."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        # The script reads DB_URL from the environment via load_dotenv, which does
        # not override an existing variable, so this wins over the repo's .env.
        env={**_passthrough_env(), "DB_URL": db_url},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _passthrough_env() -> dict[str, str]:
    import os

    # Keep the interpreter working (PATH, SYSTEMROOT on Windows) and any settings
    # the app's config requires at import time.
    keep = ("PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "JWT_SECRET_KEY", "PYTHONPATH")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.setdefault("JWT_SECRET_KEY", "x" * 64)
    return env


@pytest.fixture()
def empty_db(tmp_path: Path) -> str:
    """A database with none of the application's tables."""
    db_path = tmp_path / "reconcile.db"
    url = f"sqlite:///{db_path}"
    engine = create_engine(url)
    assert AUDIT_TABLE not in inspect(engine).get_table_names()
    engine.dispose()
    return url


def test_dry_run_reports_the_missing_table_without_creating_it(empty_db: str):
    out = _run(empty_db)

    assert AUDIT_TABLE in out
    assert "missing table" in out.lower()
    assert "dry run" in out.lower()

    engine = create_engine(empty_db)
    try:
        assert AUDIT_TABLE not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_apply_creates_the_missing_table(empty_db: str):
    _run(empty_db, "--apply")

    engine = create_engine(empty_db)
    try:
        tables = set(inspect(engine).get_table_names())
        assert AUDIT_TABLE in tables
        # The columns the audit reader selects have to be there, or the endpoint
        # trades one 500 for another.
        cols = {c["name"] for c in inspect(engine).get_columns(AUDIT_TABLE)}
        for required in (
            "id", "actor_id", "actor_email", "actor_role", "action",
            "resource_type", "resource_id", "before_json", "after_json",
            "method", "path", "status_code", "ip_address", "user_agent",
            "error_message", "created_at",
        ):
            assert required in cols, f"{required} missing from {AUDIT_TABLE}"
    finally:
        engine.dispose()


def test_apply_is_idempotent(empty_db: str):
    _run(empty_db, "--apply")
    out = _run(empty_db, "--apply")

    # Second run has nothing left to do, and must not error on the table it just
    # created: this runs by hand against production, so a repeat is expected.
    assert "No missing tables." in out


def test_reconciler_creates_every_declared_table(empty_db: str):
    """Guards the restriction to missing tables: on an empty database that means
    all of them, so a bug that silently skipped tables would show up here."""
    _run(empty_db, "--apply")

    engine = create_engine(empty_db)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    declared = set(SQLModel.metadata.tables)
    assert declared - tables == set()
