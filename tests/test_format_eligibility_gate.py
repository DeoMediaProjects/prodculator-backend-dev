"""FIX-02: per-format eligibility, three gate states, and the short-format banner.

The whitelist model that came before could say "this list is authoritative" or
"nobody checked". It could not say what is actually true of the research: that
someone established short films are excluded from a programme while nobody has
looked at documentary yet. Both collapsed into one silence.

`eligible_formats` is tri-state per format. The third state carries the weight:

    true   researched, qualifies
    false  researched, does not qualify
    null   NOT RESEARCHED — behaviour must be byte-for-byte as before

The scoring rule is the part that is easy to get wrong, so it is pinned hard:
FORMAT_INELIGIBLE scores 0 because there is no rebate to value, and
FORMAT_UNVERIFIED keeps its computed strength because "nobody checked" is not
evidence the rebate is worthless.
"""
from __future__ import annotations

import pytest

from app.modules.reports.builder import ReportBuilder
from app.modules.reports.format_eligibility import (
    FORMAT_CONFIRMED,
    FORMAT_INELIGIBLE,
    FORMAT_UNVERIFIED,
    GATE_BADGE,
    evaluate_format_eligibility,
    gate_state,
    parse_eligible_formats,
    scores_zero,
)
from app.modules.reports.helpers import index_incentives_by_territory


def row(territory="United Kingdom", rate=30, **extra):
    return {
        "territory": territory,
        "program": f"{territory} programme",
        "program_name": f"{territory} programme",
        "rate_gross": rate,
        "status": "active",
        "is_supplementary": False,
        **extra,
    }


def state(r, fmt="Short", project=None):
    return gate_state(evaluate_format_eligibility(r, fmt, project).get("verdict"))


class TestNullMeansNoRegression:
    """The acceptance criterion the whole migration rests on."""

    def test_a_row_with_no_new_columns_is_unverified_exactly_as_before(self):
        assert state(row()) == FORMAT_UNVERIFIED

    def test_an_empty_map_is_unverified(self):
        assert state(row(eligible_formats="{}")) == FORMAT_UNVERIFIED

    def test_an_explicit_null_for_this_format_is_unverified(self):
        assert state(row(eligible_formats='{"short": null}')) == FORMAT_UNVERIFIED

    def test_a_researched_sibling_format_says_nothing_about_this_one(self):
        """feature=false must not leak into the short verdict."""
        assert state(row(eligible_formats='{"feature": false}')) == FORMAT_UNVERIFIED

    @pytest.mark.parametrize("junk", ["not json", "[]", "null", "42", '"short"'])
    def test_an_unreadable_column_is_unresearched_not_a_finding(self, junk):
        """A storage problem must never read as a fact about a tax programme."""
        assert state(row(eligible_formats=junk)) == FORMAT_UNVERIFIED

    def test_the_existing_whitelist_path_still_decides_when_per_format_is_null(self):
        r = row(
            format_eligibility_status="verified",
            applicable_formats='["Feature Film"]',
        )
        assert state(r) == FORMAT_INELIGIBLE


class TestTheThreeStates:
    def test_researched_false_is_ineligible(self):
        assert state(row(eligible_formats='{"short": false}')) == FORMAT_INELIGIBLE

    def test_researched_true_is_confirmed(self):
        assert state(row(eligible_formats='{"short": true}')) == FORMAT_CONFIRMED

    def test_unresearched_is_unverified(self):
        assert state(row()) == FORMAT_UNVERIFIED

    @pytest.mark.parametrize(
        "gate,badge",
        [(FORMAT_CONFIRMED, "green"), (FORMAT_INELIGIBLE, "red"),
         (FORMAT_UNVERIFIED, "neutral")],
    )
    def test_each_state_carries_its_badge(self, gate, badge):
        assert GATE_BADGE[gate] == badge

    def test_the_research_beats_an_older_whitelist(self):
        """A whitelist saying features-only, and research saying shorts qualify.
        The per-format answer is the later and more specific one."""
        r = row(
            format_eligibility_status="verified",
            applicable_formats='["Feature Film"]',
            eligible_formats='{"short": true}',
        )
        assert state(r) == FORMAT_CONFIRMED

    def test_format_notes_reach_the_explanation(self):
        r = row(eligible_formats='{"short": false}', format_notes="Shorts under 40 min excluded.")
        result = evaluate_format_eligibility(r, "Short")
        assert "Shorts under 40 min excluded." in result["explanation"]


class TestTheatricalRelease:
    """Kept out of the format map on purpose: a programme can accept shorts and
    still require a theatrical commitment the short will never meet, and folding
    that into `short: false` records the wrong reason."""

    def test_a_theatrical_requirement_holds_an_accepted_format_at_unverified(self):
        r = row(
            eligible_formats='{"short": true}',
            theatrical_release_required=True,
            theatrical_release_note="Must open in at least 10 cinemas.",
        )
        assert state(r) == FORMAT_UNVERIFIED

    def test_the_requirement_is_explained_rather_than_asserted(self):
        r = row(
            eligible_formats='{"short": true}',
            theatrical_release_required=True,
            theatrical_release_note="Must open in at least 10 cinemas.",
        )
        assert "Must open in at least 10 cinemas." in evaluate_format_eligibility(r, "Short")["explanation"]

    def test_explicitly_not_required_does_not_hold_it_back(self):
        r = row(eligible_formats='{"short": true}', theatrical_release_required=False)
        assert state(r) == FORMAT_CONFIRMED

    def test_unresearched_theatrical_does_not_hold_it_back(self):
        """null is not false, but it is not a blocking finding either."""
        r = row(eligible_formats='{"short": true}', theatrical_release_required=None)
        assert state(r) == FORMAT_CONFIRMED

    @pytest.mark.parametrize("junk", ["", 0, 1, "yes"])
    def test_a_non_boolean_is_unresearched(self, junk):
        r = row(eligible_formats='{"short": true}', theatrical_release_required=junk)
        assert state(r) == FORMAT_CONFIRMED


class TestScoringRule:
    def test_only_a_researched_exclusion_zeroes_the_dimension(self):
        assert scores_zero(evaluate_format_eligibility(
            row(eligible_formats='{"short": false}'), "Short")["verdict"]) is True

    def test_unverified_does_not_zero_the_dimension(self):
        """The rule FIX-02 states explicitly: never convert to 0 merely because
        eligibility is unverified."""
        assert scores_zero(evaluate_format_eligibility(row(), "Short")["verdict"]) is False

    def test_confirmed_does_not_zero_the_dimension(self):
        assert scores_zero(evaluate_format_eligibility(
            row(eligible_formats='{"short": true}'), "Short")["verdict"]) is False


def _builder(rows, fmt="Short"):
    b = ReportBuilder.__new__(ReportBuilder)
    b.datasets = {"_user_territories": [], "_shoot_months": [], "_shoot_weeks": 4}
    b._territory_incentives = index_incentives_by_territory(rows)
    b._territory_inactive_incentives = {}
    b._territory_financials = {}
    b._territory_profiles = {}
    b._project_facts = {"budget_gbp": 45_730, "format": fmt}
    b._production_format = fmt
    b._production_priority = "full"
    b._currency_scores = None
    b.is_preview = False
    b._no_incentive_territories = set()
    return b


class TestIncentiveStrengthReflectsTheGate:
    def _strength(self, rows, territories, fmt="Short"):
        b = _builder(rows, fmt)
        ranked = b._build_location_rankings(territories)
        return {r["name"]: r["incentiveStrength"] for r in ranked}

    def test_a_format_ineligible_programme_scores_zero(self):
        s = self._strength([row("California", eligible_formats='{"short": false}')], ["California"])
        assert s["California"] == 0

    def test_an_unverified_programme_is_not_scored(self):
        """DELIBERATELY INVERTED. This previously asserted that an unverified
        programme keeps its full computed strength, on the reasoning that "nobody
        checked" is not evidence the rebate is worthless.

        That reasoning is sound about the PROGRAMME and wrong about the RANKING, and
        the EJE report showed why: UK AVEC (Enhanced/IFTC) had unverified short-film
        eligibility and a confirmed incentive of £0, scored Incentive Value 88, and
        took first place on the strength of a rebate the same page called
        illustrative. The report told the reader not to rely on it and ranked the
        territory first because of it.

        None is "not scored", which _weighted_score treats as neutral. That is
        distinct from 0, which means a researched exclusion — no rebate to value.
        The illustrative figure still appears in the incentive section, labelled.
        """
        s = self._strength([row("New Mexico")], ["New Mexico"])
        assert s["New Mexico"] is None

    def test_a_confirmed_programme_keeps_its_computed_strength(self):
        s = self._strength([row("United Kingdom", eligible_formats='{"short": true}')], ["United Kingdom"])
        assert s["United Kingdom"] > 0

    def test_confirmed_and_unverified_do_not_score_the_same(self):
        """DELIBERATELY INVERTED, same reason as above.

        Eligibility state now changes the arithmetic as well as the caveat, because a
        benefit this production has not been confirmed able to claim cannot be
        evidence for where it should shoot.
        """
        confirmed = self._strength(
            [row("A", rate=30, eligible_formats='{"short": true}')], ["A"],
        )["A"]
        unverified = self._strength([row("B", rate=30)], ["B"])["B"]
        assert confirmed > 0
        assert unverified is None

    def test_a_verified_exclusion_and_an_unresolved_one_are_different_values(self):
        """Zero and not-scored are two different statements and must not collapse."""
        excluded = self._strength(
            [row("California", eligible_formats='{"short": false}')], ["California"],
        )["California"]
        unresolved = self._strength([row("New Mexico")], ["New Mexico"])["New Mexico"]
        assert excluded == 0
        assert unresolved is None


class TestShortFormatBanner:
    def _banner(self, rows, territories, fmt="Short"):
        return _builder(rows, fmt)._short_format_gate_banner(territories)

    def test_two_affected_territories_raise_it(self):
        b = self._banner([row("United Kingdom"), row("New Mexico")],
                         ["United Kingdom", "New Mexico"])
        assert b is not None
        assert "2 of your territories" in b["title"]

    def test_one_affected_territory_does_not(self):
        b = self._banner(
            [row("United Kingdom"), row("New Mexico", eligible_formats='{"short": true}')],
            ["United Kingdom", "New Mexico"],
        )
        assert b is None

    def test_a_mix_of_ineligible_and_unverified_counts_together(self):
        b = self._banner(
            [row("California", eligible_formats='{"short": false}'), row("New Mexico")],
            ["California", "New Mexico"],
        )
        assert b["ineligibleTerritories"] == ["California"]
        assert b["unverifiedTerritories"] == ["New Mexico"]

    def test_the_two_kinds_are_worded_differently(self):
        """Confirmed exclusion and unchecked are different facts and the banner
        must not blur them into one sentence."""
        b = self._banner(
            [row("California", eligible_formats='{"short": false}'), row("New Mexico")],
            ["California", "New Mexico"],
        )
        assert "confirmed as not accepting" in b["body"]
        assert "have not established" in b["body"]

    def test_all_confirmed_raises_nothing(self):
        b = self._banner(
            [row("United Kingdom", eligible_formats='{"short": true}'),
             row("New Mexico", eligible_formats='{"short": true}')],
            ["United Kingdom", "New Mexico"],
        )
        assert b is None

    def test_a_feature_report_never_raises_it(self):
        b = self._banner([row("United Kingdom"), row("New Mexico")],
                         ["United Kingdom", "New Mexico"], fmt="Feature Film")
        assert b is None

    def test_it_counts_territories_not_programme_rows(self):
        """Three unverified programmes in one territory is still one territory the
        producer cannot rely on."""
        b = self._banner(
            [row("United Kingdom", rate=30), row("United Kingdom", rate=25),
             row("United Kingdom", rate=20)],
            ["United Kingdom"],
        )
        assert b is None


class TestParseEligibleFormats:
    def test_every_key_is_present_and_null_by_default(self):
        parsed = parse_eligible_formats(None)
        assert set(parsed) == {
            "feature", "short", "documentary", "tv_series", "animation", "unscripted",
        }
        assert all(v is None for v in parsed.values())

    def test_only_real_booleans_are_taken_as_answers(self):
        parsed = parse_eligible_formats('{"short": true, "feature": 1, "documentary": "yes"}')
        assert parsed["short"] is True
        assert parsed["feature"] is None
        assert parsed["documentary"] is None

    def test_a_dict_is_accepted_as_well_as_a_json_string(self):
        assert parse_eligible_formats({"short": False})["short"] is False
