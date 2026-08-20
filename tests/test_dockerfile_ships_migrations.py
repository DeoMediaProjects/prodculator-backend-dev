"""The image must contain everything `alembic upgrade head` needs.

The alembic PACKAGE arrives via requirements.txt, which makes the binary present
and the command look available — but the version files and alembic.ini were never
COPYed, so migrations could not run in the Railway console at all. A production
migration therefore meant pointing a local shell at DATABASE_PUBLIC_URL and
hand-feeding the URL, which risks aiming at the wrong database and bypasses the
one environment that already holds the correct DB_URL.

Nothing else would catch a regression here: the app boots fine without alembic,
so the image would look healthy right up to the next migration.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_DOCKERFILE = _BACKEND / "Dockerfile"
_DOCKERIGNORE = _BACKEND / ".dockerignore"


def _copied_paths() -> list[str]:
    """Source paths named by COPY/ADD instructions in the Dockerfile."""
    paths: list[str] = []
    for line in _DOCKERFILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*(?:COPY|ADD)\s+(.*)", line, re.I)
        if not match:
            continue
        parts = [p for p in match.group(1).split() if not p.startswith("--")]
        paths.extend(parts[:-1])  # last token is the destination
    return paths


@pytest.mark.parametrize("required", ["alembic.ini", "alembic"])
def test_the_image_copies_the_migration_files(required):
    assert required in _copied_paths(), (
        f"Dockerfile does not COPY {required!r}. `alembic upgrade head` cannot run "
        f"in the container without it, even though the alembic package installs "
        f"from requirements.txt."
    )


def test_the_app_package_is_copied_because_migrations_import_from_it():
    """Version files import app.alembic_utils, app.core.* and app.models.*."""
    assert "app" in _copied_paths()


def test_dockerignore_does_not_exclude_the_migrations():
    if not _DOCKERIGNORE.exists():
        pytest.skip("no .dockerignore")
    patterns = [
        line.strip()
        for line in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    for pattern in patterns:
        stem = pattern.rstrip("/")
        assert stem not in {"alembic", "alembic.ini"}, (
            f".dockerignore excludes {pattern!r}, which would defeat the COPY"
        )


def test_the_env_file_is_still_excluded():
    """Migrations must read DB_URL from the environment, not a baked-in .env.

    A committed .env inside the image would silently override Railway's variables
    and could point a production migration at the wrong database.
    """
    if not _DOCKERIGNORE.exists():
        pytest.skip("no .dockerignore")
    patterns = {
        line.strip()
        for line in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    }
    assert ".env" in patterns
    assert ".env" not in _copied_paths()


def test_every_migration_import_is_satisfied_by_the_copied_paths():
    """Sweep the version files for `from app...` imports and confirm each module
    exists, so a migration cannot reference something the image lacks."""
    copied = set(_copied_paths())
    versions = _BACKEND / "alembic" / "versions"
    modules: set[str] = set()
    for path in list(versions.glob("*.py")) + [_BACKEND / "alembic" / "env.py"]:
        text = path.read_text(encoding="utf-8", errors="replace")
        modules.update(re.findall(r"^\s*from\s+(app\.[\w.]+)", text, re.M))
        modules.update(re.findall(r"^\s*import\s+(app\.[\w.]+)", text, re.M))

    assert modules, "expected the migrations to import from the app package"
    missing = []
    for module in sorted(modules):
        top = module.split(".")[0]
        if top not in copied:
            missing.append(f"{module} (top-level {top!r} not COPYed)")
            continue
        rel = Path(*module.split("."))
        if not (_BACKEND / rel).with_suffix(".py").exists() and not (
            _BACKEND / rel
        ).is_dir():
            missing.append(f"{module} (no such module on disk)")
    assert not missing, "migrations import modules the image would not have:\n" + "\n".join(
        missing
    )
