"""A full-table refresh must not silently drop engine columns.

PROD-FIX-007's root cause was not a wrong value — it was a wholesale
`DELETE` + `INSERT` in migration ab2c3d4e5f61 whose row builder wrote 53 of the
table's columns and simply omitted the rest. Every column an earlier migration
had carefully populated (`is_supplementary`, `rate_tier_json`, `cap_basis`,
`qualifying_spend_labour_pct`, …) silently reverted to its default, and the
guard clauses that depended on them became dead code. Nothing failed; reports
just quietly started modelling the wrong programme.

This test makes the omission visible. Any column the refresh does not write is
either listed in _INTENTIONALLY_UNSET with a reason, or the test fails.

When the dataset is next refreshed, expect this test to fail — that is the
point. Add the newly-written columns to the builder, or record here why the
refresh is entitled to leave them empty.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VERSIONS = _REPO_ROOT / "alembic" / "versions"
_V4_REFRESH = _VERSIONS / "ab2c3d4e5f61_incentives_v4_refresh.py"

_ADD_COLUMN = re.compile(
    r'add_column\(\s*["\']incentive_programs["\']\s*,\s*sa\.Column\(\s*["\']([a-z_]+)["\']'
)
# aa1b2c3d4e5f declares its columns as (name, type) tuples rather than via
# add_column calls.
_TUPLE_COLUMN = re.compile(r'^\s*\(\s*["\']([a-z_]+)["\']\s*,\s*sa\.')

# Columns the refresh legitimately does not populate. Each entry is a decision,
# not an oversight — anything not listed here must be written by the builder.
_INTENTIONALLY_UNSET: dict[str, str] = {
    # Producer-specific eligibility, curated separately from the rate dataset
    # and restored by its own reviewed pass (see h8c9d0e1f2a3's docstring).
    "nationality_requirements": "curated separately; not in the v4 source",
    "co_production_eligible": "curated separately; not in the v4 source",
    "co_production_treaties": "curated separately; not in the v4 source",
    "spv_eligible": "curated separately; not in the v4 source",
    "cultural_test_required": "curated separately; not in the v4 source",
    "admin_complexity": "curated separately; not in the v4 source",
    # Regional stacking model — the v4 source is a flat programme list.
    "parent_territory": "regional stacking model; not in the v4 source",
    "stacking_group": "regional stacking model; not in the v4 source",
    "stackable_with": "regional stacking model; not in the v4 source",
    "scope": "regional stacking model; not in the v4 source",
    # Calculation detail the v4 source does not carry per-programme.
    "rate_tier_json": "tier data not in the v4 source; pending reviewed restore",
    "cap_basis": "not in the v4 source; all rows were NULL pre-refresh too",
    "qualifying_spend_labour_pct": "labour/PDV share not in the v4 source",
    "applicable_formats": "format restrictions not in the v4 source",
    "payee_note": "not in the v4 source",
    "filing_note": "not in the v4 source",
    "eligibility_rules_json": "structured rules not in the v4 source",
    # Operational columns, set by the scraper/admin at runtime.
    "expiry_date": "set by admin/scraper, not by a dataset refresh",
    "last_auto_check": "set by the scraper at runtime",
    "source_name": "set by admin/scraper, not by a dataset refresh",
    "vfx_uplift_pct": "not in the v4 source",
    "qualifying_spend_labour": "superseded by qualifying_spend_labour_pct",
    "programme_level": "national/regional classification; not read by the engine",
}


def _declared_columns() -> set[str]:
    """Every column any migration adds to incentive_programs."""
    columns: set[str] = set()
    for path in _VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf8")
        columns.update(_ADD_COLUMN.findall(text))
        if path.name.startswith("aa1b2c3d4e5f"):
            for line in text.splitlines():
                m = _TUPLE_COLUMN.match(line)
                if m:
                    columns.add(m.group(1))
    return columns


def _refresh_written_columns() -> set[str]:
    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location("_v4_refresh", _V4_REFRESH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return set(module._build_row(module._SOURCE_ROWS[0], "2026-01-01").keys())
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            del sys.modules["alembic"]


def test_refresh_writes_every_engine_column_or_declares_why_not() -> None:
    written = _refresh_written_columns()
    declared = _declared_columns()
    missing = declared - written - set(_INTENTIONALLY_UNSET)

    assert not missing, (
        "ab2c3d4e5f61 deletes every incentive_programs row and reinserts, so any "
        "column it does not write is reset to its default. These columns are "
        "neither written nor declared intentionally unset:\n  "
        + "\n  ".join(sorted(missing))
        + "\n\nEither populate them in _build_row or add them to "
        "_INTENTIONALLY_UNSET with a reason."
    )


def test_intentionally_unset_list_has_no_stale_entries() -> None:
    """Keep the waiver list honest — a column that IS written must not be waived."""
    written = _refresh_written_columns()
    stale = sorted(set(_INTENTIONALLY_UNSET) & written)
    assert not stale, (
        "these columns are written by the refresh but still listed as "
        f"intentionally unset: {stale}"
    )


def test_supplementary_flag_survives_the_refresh() -> None:
    """The specific regression: is_supplementary must be written, not defaulted.

    Migration j1k2l3m4n5o6 set this flag on the UK VFX credit so a VFX-only
    credit could never be chosen as a territory's primary programme. The v4
    refresh reset it, and the Lion King report modelled 39%/29.25% against the
    whole budget as a result.
    """
    assert "is_supplementary" in _refresh_written_columns()

    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location("_v4_refresh", _V4_REFRESH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = [module._build_row(s, "2026-01-01") for s in module._SOURCE_ROWS]
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            del sys.modules["alembic"]

    supplementary = {
        (r["territory"], r["program"]) for r in rows if r.get("is_supplementary")
    }
    assert supplementary == {
        ("United Kingdom", "UK VFX Expenditure Credit (Uplift)")
    }, f"unexpected supplementary set: {sorted(supplementary)}"


@pytest.mark.parametrize("territory", ["Ontario", "Quebec", "Alberta",
                                       "British Columbia", "Western Cape"])
def test_single_programme_territories_are_not_flagged_supplementary(
    territory: str,
) -> None:
    """Flagging these would delete the territory from every report.

    Each of these territories has exactly one programme in the dataset, and
    ReportBuilder._is_supplementary_only_territory drops a territory whose
    programmes are all supplementary. Their pre-refresh rows DID carry the
    flag, so this is a live trap for anyone restoring it mechanically.
    """
    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location("_v4_refresh", _V4_REFRESH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = [module._build_row(s, "2026-01-01") for s in module._SOURCE_ROWS]
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            del sys.modules["alembic"]

    rows_here = [r for r in rows if r["territory"] == territory]
    assert len(rows_here) == 1, f"{territory} programme count changed"
    assert not rows_here[0].get("is_supplementary")
