"""Tests for the B2B client entitlement registry (SOW 4.4).

Worked example from the contract pack: Grey Consortium UK holds the
"AI Usage Module" exclusively with a reversion date of 2028-06-30. Until that
date no other client's package may include the sections that module covers.

Exclusivity breaches are a contractual problem, so composition REFUSES rather
than silently dropping a section — an admin who asked for it must be told why.
"""
from datetime import date
from unittest.mock import MagicMock

import pytest

from app.modules.b2b.entitlement_service import (
    EntitlementConflict,
    EntitlementService,
)

GREY = "sub_grey"
OTHER = "sub_invisible"
EXCLUSIVE_SECTIONS = ["sig_audience", "sig_audience_seg"]


def _service(rows):
    db = MagicMock()
    db.table.return_value.select.return_value.execute.return_value.data = rows
    return EntitlementService(db)


def _grey_row(**overrides):
    row = {
        "id": "ent-1",
        "b2b_subscription_id": GREY,
        "module_key": "ai_usage",
        "module_label": "AI Usage Module",
        "section_keys": list(EXCLUSIVE_SECTIONS),
        "is_exclusive": True,
        "reverts_at": "2028-06-30",
    }
    row.update(overrides)
    return row


# ------------------------------------------------------------- in-force logic


def test_exclusivity_in_force_before_reversion():
    svc = _service([])
    assert svc.is_in_force(_grey_row(), date(2026, 7, 26)) is True


def test_exclusivity_lapses_on_the_reversion_date():
    """The reversion date is when the module becomes generally available."""
    svc = _service([])
    row = _grey_row()
    assert svc.is_in_force(row, date(2028, 6, 29)) is True
    assert svc.is_in_force(row, date(2028, 6, 30)) is False
    assert svc.is_in_force(row, date(2028, 7, 1)) is False


def test_null_reversion_is_perpetual_exclusivity():
    svc = _service([])
    assert svc.is_in_force(_grey_row(reverts_at=None), date(2099, 1, 1)) is True


def test_non_exclusive_entitlement_is_never_in_force():
    """A plain entitlement grants access; it does not lock anyone else out."""
    svc = _service([])
    row = _grey_row(is_exclusive=False)
    assert svc.is_in_force(row, date(2026, 7, 26)) is False


def test_unparseable_reversion_date_does_not_silently_unlock():
    """Garbage in the date column must not be read as 'already reverted'."""
    svc = _service([])
    row = _grey_row(reverts_at="not-a-date")
    assert svc.is_in_force(row, date(2026, 7, 26)) is True


# ---------------------------------------------------------------- conflicts


def test_other_client_is_blocked_from_exclusive_sections():
    svc = _service([_grey_row()])

    conflicts = svc.conflicts_for(
        subscription_id=OTHER,
        section_keys=["sig_genre", "sig_audience"],
        on_date=date(2026, 7, 26),
    )

    assert len(conflicts) == 1
    assert conflicts[0]["section_key"] == "sig_audience"
    assert conflicts[0]["held_by_subscription_id"] == GREY
    assert conflicts[0]["module_label"] == "AI Usage Module"
    assert conflicts[0]["reverts_at"] == "2028-06-30"


def test_holder_is_not_blocked_from_its_own_exclusive_sections():
    svc = _service([_grey_row()])

    conflicts = svc.conflicts_for(
        subscription_id=GREY, section_keys=EXCLUSIVE_SECTIONS, on_date=date(2026, 7, 26)
    )

    assert conflicts == []


def test_no_conflict_after_reversion():
    svc = _service([_grey_row()])

    conflicts = svc.conflicts_for(
        subscription_id=OTHER, section_keys=EXCLUSIVE_SECTIONS, on_date=date(2028, 7, 1)
    )

    assert conflicts == []


def test_subscriptionless_composition_is_still_blocked():
    """An internal one-off has no subscription but its output can still reach a
    third party, so exclusivity must still apply."""
    svc = _service([_grey_row()])

    conflicts = svc.conflicts_for(
        subscription_id=None, section_keys=["sig_audience"], on_date=date(2026, 7, 26)
    )

    assert len(conflicts) == 1


def test_module_with_no_sections_blocks_nothing():
    """A contracted-but-unbuilt module records the obligation without enforcing."""
    svc = _service([_grey_row(section_keys=[])])

    conflicts = svc.conflicts_for(
        subscription_id=OTHER, section_keys=EXCLUSIVE_SECTIONS, on_date=date(2026, 7, 26)
    )

    assert conflicts == []


def test_assert_available_raises_with_conflict_detail():
    svc = _service([_grey_row()])

    with pytest.raises(EntitlementConflict) as excinfo:
        svc.assert_available(
            subscription_id=OTHER, section_keys=["sig_audience"], on_date=date(2026, 7, 26)
        )

    assert "sig_audience" in str(excinfo.value)
    assert "2028-06-30" in str(excinfo.value)
    assert excinfo.value.conflicts[0]["held_by_subscription_id"] == GREY


def test_allowed_section_keys_filters_and_preserves_order():
    svc = _service([_grey_row()])

    allowed = svc.allowed_section_keys(
        subscription_id=OTHER,
        section_keys=["sig_genre", "sig_audience", "sig_budget", "sig_audience_seg"],
        on_date=date(2026, 7, 26),
    )

    assert allowed == ["sig_genre", "sig_budget"]


# -------------------------------------------------------------------- writes


def test_grant_sets_created_at_only_on_first_write():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    db.table.return_value.upsert.return_value.execute.return_value.data = None
    svc = EntitlementService(db)

    svc.grant(
        subscription_id=GREY,
        module_key="ai_usage",
        module_label="AI Usage Module",
        section_keys=EXCLUSIVE_SECTIONS,
        is_exclusive=True,
        reverts_at=date(2028, 6, 30),
    )

    payload = db.table.return_value.upsert.call_args.args[0]
    assert "created_at" in payload
    assert payload["is_exclusive"] is True
    assert payload["reverts_at"] == date(2028, 6, 30)
    assert db.table.return_value.upsert.call_args.kwargs["on_conflict"] == (
        "b2b_subscription_id,module_key"
    )


def test_grant_updates_existing_without_rewriting_created_at():
    db = MagicMock()
    existing = _grey_row()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [existing]
    db.table.return_value.upsert.return_value.execute.return_value.data = None
    svc = EntitlementService(db)

    svc.grant(subscription_id=GREY, module_key="ai_usage", is_exclusive=True)

    payload = db.table.return_value.upsert.call_args.args[0]
    assert payload["id"] == existing["id"]  # reuses the row, no duplicate
    assert "created_at" not in payload


def test_revoke_returns_false_for_unknown_entitlement():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    svc = EntitlementService(db)

    assert svc.revoke("nope") is False
    db.table.return_value.delete.assert_not_called()
