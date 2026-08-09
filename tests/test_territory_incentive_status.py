"""Three-state incentive position for the intake territory picker.

A single boolean told a producer the same thing about three different situations:
South Africa's programme is suspended, Brazil's is awaiting verification, and
Nigeria has no programme at all. The first two hold a real incentive record whose
bankability cannot be confirmed; the third has nothing. Marking all three
identically overstates the third case and understates the first two.

Derived from the programme records rather than a list of country names, so a
programme being reinstated or suspended changes the picker without a code edit.
"""
from __future__ import annotations

import pytest

from app.modules.health.router import _territory_rows_to_options


ROWS = [
    {"territory": "United Kingdom", "status": "ACTIVE", "is_supplementary": False},
    {"territory": "South Africa", "status": "SUSPENDED", "is_supplementary": False},
    {"territory": "Brazil", "status": "ADMIN_VERIFY_REQUIRED", "is_supplementary": False},
    {"territory": "Nigeria", "status": "NO_PROGRAMME", "is_supplementary": False},
]


def _status_by_label(rows, *, include_all: bool = True) -> dict[str, str]:
    options = _territory_rows_to_options(rows, include_all=include_all)
    return {o["label"]: o["incentiveStatus"] for o in options}


class TestIncentiveStatus:
    def test_an_active_programme_is_active(self):
        assert _status_by_label(ROWS)["United Kingdom"] == "active"

    def test_a_suspended_programme_is_unconfirmed_not_absent(self):
        """South Africa. The programme exists; its bankability does not."""
        assert _status_by_label(ROWS)["South Africa"] == "unconfirmed"

    def test_a_programme_awaiting_verification_is_unconfirmed(self):
        """Brazil."""
        assert _status_by_label(ROWS)["Brazil"] == "unconfirmed"

    def test_no_programme_on_record_is_none(self):
        """Nigeria. Distinct from the two above, which is the whole point."""
        assert _status_by_label(ROWS)["Nigeria"] == "none"

    def test_a_territory_with_no_rows_at_all_is_none(self):
        statuses = _status_by_label(ROWS)
        # Any enum territory with no incentive row is surfaced by include_all.
        assert statuses.get("Iceland") in (None, "none")

    def test_one_active_programme_outranks_a_suspended_sibling(self):
        """A country with both an active and a suspended programme is active: it
        has something bankable to model, whatever else is on its record."""
        rows = ROWS + [
            {"territory": "South Africa", "status": "ACTIVE", "is_supplementary": False},
        ]
        assert _status_by_label(rows)["South Africa"] == "active"

    def test_a_supplementary_row_does_not_make_a_territory_unconfirmed(self):
        """Supplementary uplifts are excluded from this question entirely: they
        stack onto a primary programme and are not one themselves."""
        rows = [
            {"territory": "Iceland", "status": "SUSPENDED", "is_supplementary": True},
        ]
        assert _status_by_label(rows).get("Iceland") in (None, "none")

    def test_the_boolean_still_agrees_with_the_three_state_value(self):
        """Older clients read hasActiveIncentive; it must not contradict."""
        options = _territory_rows_to_options(ROWS, include_all=True)
        for option in options:
            expected = option["incentiveStatus"] == "active"
            assert option["hasActiveIncentive"] is expected, option["label"]

    @pytest.mark.parametrize("status", ["active", "unconfirmed", "none"])
    def test_every_option_carries_one_of_the_three_values(self, status):
        values = {o["incentiveStatus"] for o in _territory_rows_to_options(ROWS, include_all=True)}
        assert values <= {"active", "unconfirmed", "none"}
