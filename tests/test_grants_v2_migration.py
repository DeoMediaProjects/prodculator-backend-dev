"""Reconciling live grant rows against the frozen Grants v2 migration maps.

The behaviour under test is a refusal: a live grant row may keep taking paid
recommendation slots only where the freeze says so explicitly. Every other
outcome — unmapped, unresolved, needing review, naming an action nobody encoded —
is a blocking finding rather than a quiet retention.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.modules.grants.v2_migration import (
    ARCHIVE,
    CORRECT,
    NEEDS_REVIEW,
    RECLASSIFY,
    REPLACE,
    SPLIT,
    SUSPEND,
    parse_decision,
    reconcile,
)

SNAPSHOT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "handoff_snapshots"
    / "grants_v2_2026-09-04.json"
)


def _mapping(
    existing_id: str,
    decision: str,
    *,
    verification: str = "VERIFIED_CURRENT",
    resolved: str | None = None,
    title: str = "A grant",
) -> dict:
    return {
        "existing_id": existing_id,
        "existing_title": title,
        "decision": decision,
        "verification_status": verification,
        "resolved_live_id": existing_id if resolved is None else resolved,
    }


# ── Parsing a compound decision ──────────────────────────────────────────────


def test_a_single_action_parses():
    actions, unrecognised = parse_decision("CORRECT")
    assert actions == {CORRECT}
    assert unrecognised == ()


def test_a_compound_decision_parses_to_every_action_it_names():
    actions, _ = parse_decision("ARCHIVE_RECLASSIFY")
    assert actions == {ARCHIVE, RECLASSIFY}


def test_correct_suspend_is_not_read_as_correct():
    """The prefix-match trap: this would leave a suspended programme matchable."""
    actions, _ = parse_decision("CORRECT_SUSPEND")
    assert actions == {CORRECT, SUSPEND}


def test_needs_review_nonmatchable_parses_as_one_token():
    actions, unrecognised = parse_decision("NEEDS_REVIEW_NONMATCHABLE")
    assert actions == {NEEDS_REVIEW}
    assert unrecognised == ()


def test_needs_terms_is_an_unresolved_decision():
    actions, _ = parse_decision("CORRECT_RECLASSIFY_NEEDS_TERMS")
    assert actions == {CORRECT, RECLASSIFY, NEEDS_REVIEW}


def test_an_unencoded_action_is_reported_not_defaulted():
    actions, unrecognised = parse_decision("CORRECT_TRANSMOGRIFY")
    assert actions == {CORRECT}
    assert unrecognised == ("TRANSMOGRIFY",)


def test_an_absent_decision_names_no_action():
    assert parse_decision(None) == (frozenset(), ())
    assert parse_decision("   ") == (frozenset(), ())


# ── Verdicts ─────────────────────────────────────────────────────────────────


def test_a_corrected_verified_row_stays_matchable_and_paid_safe():
    report = reconcile(
        [{"id": "a", "title": "A grant"}], [_mapping("a", "CORRECT")]
    )
    verdict = report.verdicts[0]
    assert verdict.stays_matchable
    assert verdict.paid_safe
    assert not verdict.is_blocking


def test_a_retiring_action_ends_live_matching():
    for decision in ("ARCHIVE", "RECLASSIFY", "SPLIT", "SUSPEND", "REPLACE"):
        report = reconcile(
            [{"id": "a", "title": "A grant"}], [_mapping("a", decision)]
        )
        assert not report.verdicts[0].stays_matchable, decision


def test_a_retiring_action_wins_over_a_correcting_one():
    """Correcting a record does not undo the decision to retire it."""
    report = reconcile(
        [{"id": "a", "title": "A grant"}], [_mapping("a", "CORRECT_RECLASSIFY")]
    )
    assert not report.verdicts[0].stays_matchable


def test_an_unmapped_live_row_blocks():
    """Silence is not retention. This is the default-survival case."""
    report = reconcile([{"id": "orphan", "title": "Old grant"}], [])
    verdict = report.verdicts[0]
    assert verdict.is_blocking
    assert not verdict.stays_matchable
    assert "does not say what becomes of this row" in verdict.blocking_reasons[0]


def test_a_needs_review_decision_blocks_and_does_not_retain():
    report = reconcile(
        [{"id": "a", "title": "A grant"}], [_mapping("a", "NEEDS_REVIEW")]
    )
    verdict = report.verdicts[0]
    assert verdict.is_blocking
    assert not verdict.stays_matchable


def test_a_mapping_with_no_resolved_live_id_blocks():
    report = reconcile(
        [{"id": "a", "title": "A grant"}], [_mapping("a", "CORRECT", resolved="")]
    )
    verdict = report.verdicts[0]
    assert verdict.is_blocking
    assert any("dangling" in reason for reason in verdict.blocking_reasons)


def test_a_split_may_resolve_to_a_different_live_id():
    """That redirect is what a split parent is for, so it is not a finding."""
    report = reconcile(
        [{"id": "parent", "title": "Generic fund"}],
        [_mapping("parent", "SPLIT", resolved="child")],
    )
    assert not report.verdicts[0].is_blocking


def test_a_redirect_without_a_split_or_replace_blocks():
    report = reconcile(
        [{"id": "a", "title": "A grant"}],
        [_mapping("a", "CORRECT", resolved="somewhere-else")],
    )
    assert report.verdicts[0].is_blocking


def test_partial_verification_retains_the_row_but_not_as_paid_safe():
    """Two separate questions: is it live, and may it be sold as confirmed."""
    report = reconcile(
        [{"id": "a", "title": "A grant"}],
        [_mapping("a", "CORRECT", verification="PARTIALLY_VERIFIED")],
    )
    verdict = report.verdicts[0]
    assert verdict.stays_matchable
    assert not verdict.paid_safe


def test_a_live_row_without_an_id_blocks():
    report = reconcile([{"id": None, "title": "A grant"}], [])
    assert report.verdicts[0].is_blocking


def test_a_mapping_naming_a_departed_live_row_is_reported():
    report = reconcile([{"id": "a", "title": "A grant"}], [_mapping("gone", "CORRECT")])
    assert report.mappings_without_a_live_row == ["gone"]
    assert not report.is_ready_to_import


# ── Readiness ────────────────────────────────────────────────────────────────


def test_readiness_needs_every_live_row_settled():
    report = reconcile(
        [{"id": "a", "title": "A grant"}, {"id": "b", "title": "Another"}],
        [_mapping("a", "CORRECT"), _mapping("b", "ARCHIVE")],
    )
    assert report.is_ready_to_import


def test_a_source_open_question_blocks_readiness():
    """A duplicate title the freeze never ruled on is not resolved by silence."""
    report = reconcile(
        [{"id": "a", "title": "A grant"}],
        [_mapping("a", "CORRECT")],
        source_open_questions={"duplicate_canonical_titles": ["BFI Something"]},
    )
    assert not report.is_ready_to_import


def test_an_empty_open_question_list_does_not_block():
    report = reconcile(
        [{"id": "a", "title": "A grant"}],
        [_mapping("a", "CORRECT")],
        source_open_questions={"duplicate_canonical_titles": []},
    )
    assert report.is_ready_to_import


# ── Against the real frozen snapshot ─────────────────────────────────────────


def test_the_frozen_snapshot_is_present_and_shaped_as_reviewed():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert len(snapshot["records"]) == 253
    assert len(snapshot["migration_maps"]["legacy_id_mapping"]) == 88
    assert (
        snapshot["source_sha256"]
        == "7c5bc7576aae435fbcc6926d7cb9997e8dcf0c9edf5a02ab84dfb89a95091e1a"
    )


def test_the_frozen_snapshot_still_carries_its_unsettled_rows():
    """These are the gaps that keep the migration from being ready.

    Asserted rather than described so that resolving one in the source is a
    deliberate act with a failing test attached, not a silent change of state.
    """
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    questions = snapshot["open_questions"]
    assert len(questions["duplicate_canonical_titles"]) == 3
    assert len(questions["records_routed_elsewhere"]) == 1
    assert len(questions["mappings_without_resolved_live_id"]) == 4


def test_every_frozen_mapping_decision_is_encoded():
    """No decision in the real freeze parses to an action this module invents."""
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    unrecognised: dict[str, tuple[str, ...]] = {}
    for row in snapshot["migration_maps"]["legacy_id_mapping"]:
        decision = row.get("decision")
        actions, leftover = parse_decision(decision)
        if leftover or not actions:
            unrecognised[decision] = leftover
    assert unrecognised == {}


def test_the_freeze_retires_more_rows_than_it_retains():
    """A sanity check on the reconciliation, not a requirement of the data.

    The 88 mapped rows resolve to a minority retained: most are split,
    reclassified, archived or replaced. If this ever inverts, the parser has
    probably started reading a compound decision as a simple retention.
    """
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    mapping = snapshot["migration_maps"]["legacy_id_mapping"]
    live = [{"id": row["existing_id"], "title": row["existing_title"]} for row in mapping]
    report = reconcile(live, mapping)
    assert len(report.retained) < len(report.verdicts) / 2


# ── The dry-run script ───────────────────────────────────────────────────────


def test_the_dry_run_snapshot_hash_is_pinned_to_the_file_on_disk():
    """A regenerated snapshot from a different master is a different migration."""
    import hashlib

    from scripts.reconcile_grants_v2 import SNAPSHOT, SNAPSHOT_SHA256

    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == SNAPSHOT_SHA256


def test_the_dry_run_rejects_a_snapshot_whose_hash_changed(tmp_path):
    from scripts.reconcile_grants_v2 import load_snapshot

    altered = tmp_path / "grants.json"
    altered.write_text("{}", encoding="utf-8")
    try:
        load_snapshot(altered, expected_sha256="0" * 64)
    except ValueError as exc:
        assert "differs from the reviewed snapshot" in str(exc)
    else:  # pragma: no cover - the guard is the point of the test
        raise AssertionError("A changed snapshot must not load")


def test_the_dry_run_names_its_findings_and_refuses_readiness():
    from scripts.reconcile_grants_v2 import build_report, load_snapshot, render
    from scripts.reconcile_grants_v2 import SNAPSHOT

    snapshot = load_snapshot(SNAPSHOT)
    mapping = snapshot["migration_maps"]["legacy_id_mapping"]
    live = [
        {"id": row["existing_id"], "title": row["existing_title"]} for row in mapping
    ]
    live.append({"id": "never-reviewed", "title": "A row the freeze never saw"})

    report = build_report(snapshot, live)
    output = render(report)

    assert not report.is_ready_to_import
    assert "NOT READY" in output
    assert "never-reviewed" in output
    assert "nothing was written" in output
