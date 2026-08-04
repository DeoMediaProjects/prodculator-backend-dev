"""Every name a migration references must actually resolve.

A migration's upgrade() body usually runs exactly once, against a real
database, often in a deploy window. A NameError in it is therefore found at the
worst possible moment — as happened with g7b8c9d0e1f2, which referenced
`_AUDIT_SPAN` after the detector was moved to app.core.audit_notes and imported
under its public name. Unit tests covered the detector and the data, but
nothing executed the migration function itself, so the typo shipped.

Executing every migration needs a live Postgres. Statically resolving every
free variable does not, and catches exactly this class of defect across the
whole versions/ directory in well under a second.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_BUILTINS = set(dir(builtins))


def _bound_names(node: ast.AST) -> set[str]:
    """Names bound anywhere inside a function: params, assignments, imports…"""
    bound: set[str] = set()

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            bound.add(a.arg)
        if args.vararg:
            bound.add(args.vararg.arg)
        if args.kwarg:
            bound.add(args.kwarg.arg)

    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            bound.add(child.id)
        elif isinstance(child, ast.arg):
            bound.add(child.arg)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            for alias in child.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(child, ast.ExceptHandler) and child.name:
            bound.add(child.name)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(child.name)
        elif isinstance(child, ast.Global):
            bound.update(child.names)

    return bound


def _module_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                names.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _unresolved(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf8"))
    available = _module_level_names(tree) | _BUILTINS | {"__name__", "__file__"}

    problems: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = _bound_names(node)
        for child in ast.walk(node):
            if not (isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)):
                continue
            if child.id in local or child.id in available:
                continue
            problems.append(f"{node.name}() line {child.lineno}: {child.id}")
    return problems


_MIGRATIONS = sorted(_VERSIONS.glob("*.py"))


@pytest.mark.parametrize("path", _MIGRATIONS, ids=lambda p: p.stem)
def test_migration_has_no_unresolved_names(path: Path) -> None:
    problems = _unresolved(path)
    assert not problems, (
        f"{path.name} references names that do not resolve — this would raise "
        f"NameError mid-migration:\n  " + "\n  ".join(problems)
    )


def test_the_checker_actually_catches_a_bad_reference(tmp_path: Path) -> None:
    """Guard the guard: a test that never fails is worse than no test.

    Reproduces the exact g7b8c9d0e1f2 defect — a module importing AUDIT_SPAN
    but referencing _AUDIT_SPAN.
    """
    bad = tmp_path / "bad_migration.py"
    bad.write_text(
        "from app.core.audit_notes import AUDIT_SPAN\n"
        "\n"
        "def upgrade():\n"
        "    if _AUDIT_SPAN.search('x'):\n"
        "        pass\n",
        encoding="utf8",
    )
    problems = _unresolved(bad)
    assert any("_AUDIT_SPAN" in p for p in problems)


def test_checker_accepts_valid_references(tmp_path: Path) -> None:
    good = tmp_path / "good_migration.py"
    good.write_text(
        "from app.core.audit_notes import AUDIT_SPAN\n"
        "\n"
        "_LOCAL = 1\n"
        "\n"
        "def upgrade():\n"
        "    rows = [r for r in range(_LOCAL)]\n"
        "    for r in rows:\n"
        "        if AUDIT_SPAN.search(str(r)):\n"
        "            print(r)\n",
        encoding="utf8",
    )
    assert _unresolved(good) == []
