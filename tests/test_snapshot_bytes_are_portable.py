"""Reviewed data files hash the same on every platform.

This is the regression for a CI failure that passed on every developer's
machine. The snapshot hashes were generated on Windows, where Git's default
line-ending conversion rewrites LF to CRLF on checkout, so the bytes on disk
differed from the bytes in the repository. Linux CI read the repository bytes,
hashed them, and rejected every snapshot — while Windows kept agreeing with
itself.

That is the worst shape a bug can take: the only machine that would catch it is
the one that cannot. `.gitattributes` marks these files `-text` so Git never
converts them, and these tests fail if that protection is ever removed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GITATTRIBUTES = ROOT / ".gitattributes"

#: Every directory whose contents are hashed in code, and so must be byte-exact.
HASHED_DATA_DIRS: tuple[str, ...] = (
    "data/handoff_snapshots",
    "data/curated_opportunities",
    "data/handoff_contracts",
)


def _data_files() -> list[Path]:
    files: list[Path] = []
    for folder in HASHED_DATA_DIRS:
        files.extend(sorted((ROOT / folder).glob("*.json")))
    return files


def test_there_are_data_files_to_protect():
    """A guard that silently protects nothing is worse than no guard."""
    assert _data_files()


@pytest.mark.parametrize("path", _data_files(), ids=lambda p: p.name)
def test_no_reviewed_data_file_carries_windows_line_endings(path: Path):
    raw = path.read_bytes()
    assert b"\r\n" not in raw, (
        f"{path.name} has CRLF line endings in the working tree. Its hash will "
        "not match on Linux. Check .gitattributes marks it -text, then "
        "re-checkout the file."
    )


def test_gitattributes_exists():
    assert GITATTRIBUTES.exists(), (
        ".gitattributes is what stops Git rewriting these files' line endings"
    )


@pytest.mark.parametrize("folder", HASHED_DATA_DIRS)
def test_every_hashed_directory_is_marked_binary(folder: str):
    rules = GITATTRIBUTES.read_text(encoding="utf-8")
    assert f"{folder}/*.json -text" in rules, (
        f"{folder} is hashed in code but not marked -text in .gitattributes, so "
        "Git may rewrite its line endings and break the hash on other platforms"
    )


def test_the_pinned_snapshot_hashes_match_the_files_on_disk():
    """The pins and the bytes agree — the assertion CI actually failed on."""
    from scripts.stage_engine_handoffs import SNAPSHOT_HASHES

    snapshots = ROOT / "data" / "handoff_snapshots"
    for filename, expected in SNAPSHOT_HASHES.items():
        actual = hashlib.sha256((snapshots / filename).read_bytes()).hexdigest()
        assert actual == expected, f"{filename} does not match its pinned hash"


def test_the_curated_cycle_pin_matches_the_file_on_disk():
    from scripts.stage_curated_cycles import CURATED, CURATED_SHA256

    actual = hashlib.sha256(CURATED.read_bytes()).hexdigest()
    assert actual == CURATED_SHA256
