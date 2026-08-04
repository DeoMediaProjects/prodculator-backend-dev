"""PROD-FIX-006 — internal QA notes must never reach client-facing output.

Internal data-audit annotations written for the admin/data team were appearing
verbatim in the Tax Incentive Analysis section of client PDF and Excel output,
because migration ab2c3d4e5f61 wrote the seed `notes` string (which carries the
annotations inline) into both `notes` and `eligibility_notes`, and
`eligibility_notes` is read by ReportBuilder and appended to the client-facing
requirements list.

Three layers are covered here, matching the three layers of the fix:

  1. the detector itself                     (app.core.audit_notes)
  2. the dataset — all 49 v4 programme rows  (the ingestion fix)
  3. the report boundary guard               (ReportValidator, fail-closed)
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from app.core.audit_notes import (
    AUDIT_MARKERS,
    NARRATIVE_COLUMNS,
    contains_audit_text,
    split_audit_text,
)
from app.modules.reports.validator import ReportValidator

_REPO_ROOT = Path(__file__).resolve().parents[1]
_V4_MIGRATION = (
    _REPO_ROOT / "alembic" / "versions" / "ab2c3d4e5f61_incentives_v4_refresh.py"
)

# The exact strings quoted in the QA brief, from the A_Quiet_Place (Serbia) and
# Blacbet Master (Germany, Ontario) reports.
_BRIEF_EXAMPLES = [
    "[FLAGGED 2026-07: found historical evidence of first-come-first-served "
    "annual budget allocation (was EUR6.7M in 2018, almost certainly grown "
    "since) but could not confirm a current 2026 annual pool figure. "
    "Per-project cap not found either way, needs direct confirmation from Film "
    "Center Serbia before treating 'No cap' as reliable.]",
    "[UPDATED 2026-07: rate confirmed 30% (raised from 20%, Feb 2026) with cap "
    "raised to EUR5M, these were already correct. qsMin corrected...]",
    "[STRUCTURAL FLAG 2026-07: Programme name and rate appear mismatched, "
    "needs reconciliation.]",
    "[RENAMED 2026-07: The 21.5% figure itself was NOT independently "
    "reconfirmed this session, flagged for a direct check against Ontario "
    "Creates.]",
]


@pytest.fixture(scope="module")
def v4_rows() -> list[dict]:
    """The 49 programme rows the v4 refresh migration inserts.

    Loaded by importing the migration with `alembic.op` stubbed, so the row
    builder is exercised exactly as it runs against a real database without
    needing one.
    """
    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location("_v4_refresh", _V4_MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return [
            module._build_row(src, "2026-08-04T00:00:00Z")
            for src in module._SOURCE_ROWS
        ]
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            del sys.modules["alembic"]


# ── 1. Detector ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("example", _BRIEF_EXAMPLES)
def test_detector_catches_every_example_from_the_brief(example: str) -> None:
    assert contains_audit_text(example)
    clean, extracted = split_audit_text(example)
    assert extracted, "annotation should have been extracted"
    assert clean is None, "these examples are annotation end to end"


@pytest.mark.parametrize("marker", AUDIT_MARKERS)
def test_detector_catches_each_known_marker(marker: str) -> None:
    assert contains_audit_text(f"Some prose. [{marker} 2026-07: internal note.]")


def test_detector_catches_unknown_marker_variants() -> None:
    """The convention is matched by shape, not by an enumeration.

    The brief listed four markers; the live dataset carries ten. A detector
    tied to a fixed list would go stale on the next data-audit pass.
    """
    assert contains_audit_text("Prose. [SOMETHING ENTIRELY NEW 2026-09: note.]")


def test_detector_preserves_client_prose() -> None:
    """Legitimate report content must survive untouched.

    Canada's notes explain a real eligibility constraint in client language and
    contain capitalised emphasis — the detector must not mistake that for an
    annotation.
    """
    prose = (
        "Federal CPTC: 25% of qualified Canadian labour expenditure, for "
        "CANADIAN-OWNED AND CONTROLLED production companies making genuinely "
        "Canadian content. This is NOT available to a foreign production "
        "company regardless of budget."
    )
    assert not contains_audit_text(prose)
    clean, extracted = split_audit_text(prose)
    assert clean == prose
    assert extracted == []


def test_detector_ignores_non_strings() -> None:
    for value in (None, 42, 3.5, True, [], {}):
        assert not contains_audit_text(value)


def test_split_keeps_the_clean_half_and_moves_the_annotation() -> None:
    clean, extracted = split_audit_text(
        "Cash rebate after production completion and audit. "
        "[FLAGGED 2026-07: could not confirm a current 2026 annual pool figure.]"
    )
    assert clean == "Cash rebate after production completion and audit"
    assert len(extracted) == 1
    assert "could not confirm" in extracted[0]


# ── 2. Dataset ───────────────────────────────────────────────────────────────


def test_v4_dataset_has_no_audit_text_in_narrative_columns(v4_rows) -> None:
    """The acceptance criterion: zero matches across all territories.

    Covers the full 49-programme dataset, not just the three programmes that
    happened to surface in the reports QA reviewed.
    """
    leaks = [
        f"{row['territory']} / {row['program']} / {column}"
        for row in v4_rows
        for column in NARRATIVE_COLUMNS
        if contains_audit_text(row.get(column))
    ]
    assert not leaks, "audit text still in narrative-facing columns:\n  " + "\n  ".join(
        leaks
    )


def test_v4_dataset_retains_audit_notes_for_the_data_team(v4_rows) -> None:
    """The other half of the acceptance criterion: nothing is destroyed.

    The annotations must still be reachable by the admin/data team via
    internal_audit_notes — separated, not deleted.
    """
    with_audit = [r for r in v4_rows if r.get("internal_audit_notes")]
    assert len(with_audit) >= 30, (
        f"only {len(with_audit)} rows retained audit notes — the extraction "
        f"may be dropping them instead of moving them"
    )


def test_serbia_matches_the_brief_exactly(v4_rows) -> None:
    """The specific record quoted in the brief, end to end."""
    serbia = next(r for r in v4_rows if r["territory"] == "Serbia")

    assert not contains_audit_text(serbia["notes"])
    assert not contains_audit_text(serbia["eligibility_notes"])
    assert "Cash rebate after production completion" in serbia["notes"]

    # ...and the caution itself is preserved for the data team.
    assert "Film Center Serbia" in serbia["internal_audit_notes"]


def test_eligibility_notes_never_carries_audit_text(v4_rows) -> None:
    """eligibility_notes is the field that actually reached the PDF.

    ReportBuilder._build_single_estimate appends it to the client-facing
    requirements list, and ExcelService writes it to the export.
    """
    for row in v4_rows:
        assert not contains_audit_text(row.get("eligibility_notes")), (
            f"{row['territory']} / {row['program']}"
        )


# ── 3. Report boundary guard ─────────────────────────────────────────────────


def test_guard_strips_audit_text_from_a_generated_report() -> None:
    """Backstop for a record edited after the migration.

    The schema separation is the primary fix; this proves the report still
    cannot ship an annotation if a new record reintroduces one.
    """
    report = {
        "incentiveEstimates": [
            {
                "territory": "Serbia",
                "program": "Serbia Film Commission Cash Rebate",
                "requirements": [
                    "Minimum spend EUR300K",
                    _BRIEF_EXAMPLES[0],
                ],
                "eligibilityNote": (
                    "Cash rebate after audit. " + _BRIEF_EXAMPLES[1]
                ),
            }
        ],
    }
    warnings: list[str] = []
    ReportValidator._strip_leaked_audit_text(report, warnings)

    estimate = report["incentiveEstimates"][0]
    # The annotation-only list entry is dropped rather than left as a null hole.
    assert estimate["requirements"] == ["Minimum spend EUR300K"]
    # The mixed field keeps its client-safe half.
    assert estimate["eligibilityNote"] == "Cash rebate after audit"
    assert any("audit-leak" in w for w in warnings)


def test_guard_drops_a_field_that_was_entirely_audit_text() -> None:
    report = {"incentiveEstimates": [{"eligibilityNote": _BRIEF_EXAMPLES[2]}]}
    ReportValidator._strip_leaked_audit_text(report, [])
    assert "eligibilityNote" not in report["incentiveEstimates"][0]


def test_guard_leaves_a_clean_report_untouched() -> None:
    report = {
        "executiveSummary": {
            "recommendedTerritory": "United Kingdom",
            "keyInsights": ["AVEC delivers 25.5% net on qualifying spend."],
        },
        "incentiveEstimates": [
            {"territory": "United Kingdom", "requirements": ["BFI cultural test"]}
        ],
    }
    warnings: list[str] = []
    ReportValidator._strip_leaked_audit_text(report, warnings)

    assert warnings == []
    assert report["incentiveEstimates"][0]["requirements"] == ["BFI cultural test"]
    assert report["executiveSummary"]["keyInsights"] == [
        "AVEC delivers 25.5% net on qualifying spend."
    ]


def test_guard_runs_as_part_of_assert_integrity() -> None:
    """The guard must be wired in, not merely present.

    Regression cover for the guard being defined but never called — the whole
    point is that it sits on the path every report takes.
    """
    report = {
        "incentiveEstimates": [{"eligibilityNote": "Fine. " + _BRIEF_EXAMPLES[3]}],
    }
    result, warnings = ReportValidator.assert_integrity(report, {})

    assert not contains_audit_text(result["incentiveEstimates"][0]["eligibilityNote"])
    assert any("audit-leak" in w for w in warnings)
