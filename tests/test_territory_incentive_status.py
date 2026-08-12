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


class TestOwnIncentive:
    """`hasActiveIncentive` cannot say whether a country stands on its own.

    The United States is in the picker only because six of its states carry
    programmes; there is no federal film incentive. The picker needs to tell that
    apart from a country with a national programme, so it stops charging a
    territory slot for a selection that models nothing and stops offering it as a
    filming commitment.
    """

    US_ROWS = [
        {"territory": "California", "status": "ACTIVE", "is_supplementary": False},
        {"territory": "New York", "status": "ACTIVE", "is_supplementary": False},
        {"territory": "United Kingdom", "status": "ACTIVE", "is_supplementary": False},
    ]

    def _by_label(self, rows):
        return {o["label"]: o for o in _territory_rows_to_options(rows, include_all=True)}

    def test_a_country_present_only_via_its_states_has_no_incentive_of_its_own(self):
        options = self._by_label(self.US_ROWS)
        assert options["United States"]["hasActiveIncentive"] is True
        assert options["United States"]["hasOwnIncentive"] is False

    def test_a_state_with_its_own_programme_says_so(self):
        assert self._by_label(self.US_ROWS)["California"]["hasOwnIncentive"] is True

    def test_a_country_with_a_national_programme_says_so(self):
        assert self._by_label(self.US_ROWS)["United Kingdom"]["hasOwnIncentive"] is True

    def test_a_suspended_programme_still_counts_as_the_territory_s_own(self):
        """South Africa's DTIC rebate is its own, suspended or not — the flag is
        about whose programme it is, not whether it can be banked today."""
        options = self._by_label(ROWS)
        assert options["South Africa"]["hasOwnIncentive"] is True
        assert options["South Africa"]["incentiveStatus"] == "unconfirmed"

    def test_a_territory_surfaced_only_by_include_all_owns_nothing(self):
        assert self._by_label(ROWS)["Iceland"]["hasOwnIncentive"] is False

    def test_a_no_programme_record_is_not_an_incentive_of_its_own(self):
        """Nigeria's row states the absence of a rebate. Reading it as ownership
        would let a country with nothing to model pass as a real selection."""
        assert self._by_label(ROWS)["Nigeria"]["hasOwnIncentive"] is False
