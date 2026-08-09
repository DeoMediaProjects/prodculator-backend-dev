"""Programme-level format eligibility: the model, the selection, the surfaces.

The rule this whole module exists to enforce: UNKNOWN IS NOT ELIGIBLE. Before this,
``applicable_formats`` was NULL on all 49 rows and NULL meant "all formats", so a
short film was quoted feature-scale rebates from programmes that may exclude shorts,
and an unchecked programme could win on rate against a verified one.

Numbering follows the acceptance list in the brief.
"""
from __future__ import annotations

import pytest

from app.core.formats import canonical_format
from app.modules.reports.format_eligibility import (
    ELIGIBLE,
    INELIGIBLE,
    NEEDS_CONFIRMATION,
    STATUS_CONDITIONAL,
    STATUS_UNKNOWN,
    STATUS_VERIFIED,
    UNVERIFIED,
    any_unverified_for_format,
    eligibility_status,
    evaluate_format_eligibility,
    parse_applicable_formats,
)
from app.modules.reports.helpers import best_incentive


def verified(formats, **extra):
    return {"applicable_formats": formats, "format_eligibility_status": STATUS_VERIFIED, **extra}


def conditional(conditions, formats=None, **extra):
    return {
        "applicable_formats": formats,
        "format_eligibility_status": STATUS_CONDITIONAL,
        "format_conditions": conditions,
        **extra,
    }


def unchecked(**extra):
    """The live shape of all 49 rows: no whitelist, no status."""
    return {"applicable_formats": None, **extra}


def verdict(row, fmt="Short", project=None):
    return evaluate_format_eligibility(row, fmt, project)["verdict"]


# ── 1. Verified eligible ─────────────────────────────────────────────────────

class TestVerifiedEligible:
    def test_short_allowed_by_a_verified_whitelist_is_eligible(self):
        assert verdict(verified(["feature", "short"])) == ELIGIBLE

    def test_display_labels_in_the_column_still_match(self):
        """Historically the column stored "Feature Film", not canonical tokens."""
        assert verdict(verified(["Feature Film", "Short"])) == ELIGIBLE

    def test_the_verdict_carries_provenance_for_the_reader(self):
        row = verified(
            ["short"],
            format_source_url="https://gov.example/programme",
            format_verified_at="2026-08-01",
        )
        result = evaluate_format_eligibility(row, "Short")
        assert result["confirmed"] is True
        assert result["sourceUrl"] == "https://gov.example/programme"
        assert result["verifiedAt"] == "2026-08-01"


# ── 2. Verified ineligible ───────────────────────────────────────────────────

class TestVerifiedIneligible:
    def test_a_verified_whitelist_without_short_excludes_it(self):
        assert verdict(verified(["feature", "tv_series"])) == INELIGIBLE

    def test_the_explanation_names_the_format(self):
        result = evaluate_format_eligibility(verified(["feature"]), "Short")
        assert "short" in result["explanation"].lower()
        assert result["confirmed"] is False


# ── 3 & 4. Unknown ───────────────────────────────────────────────────────────

class TestUnknownIsNeverEligible:
    def test_null_applicable_formats_is_unverified(self):
        """Case 3. The live state of every row."""
        assert verdict(unchecked()) == UNVERIFIED

    def test_status_unknown_is_unverified(self):
        """Case 4."""
        assert verdict({"applicable_formats": ["short"], "format_eligibility_status": STATUS_UNKNOWN}) == UNVERIFIED

    def test_unknown_is_never_reported_as_confirmed(self):
        for row in (unchecked(), {"format_eligibility_status": STATUS_UNKNOWN}):
            assert evaluate_format_eligibility(row, "Short")["confirmed"] is False

    def test_verified_without_a_whitelist_degrades_to_unknown(self):
        """A status claiming verification with nothing to verify against states
        nothing. It must not exclude every format either."""
        assert eligibility_status({"format_eligibility_status": STATUS_VERIFIED}) == STATUS_UNKNOWN
        assert verdict({"format_eligibility_status": STATUS_VERIFIED}) == UNVERIFIED

    def test_an_empty_whitelist_is_not_all_formats(self):
        assert parse_applicable_formats([]) is None
        assert verdict({"applicable_formats": [], "format_eligibility_status": STATUS_VERIFIED}) == UNVERIFIED

    def test_an_unrecognised_status_falls_back_to_unknown(self):
        assert eligibility_status({"format_eligibility_status": "probably fine"}) == STATUS_UNKNOWN

    def test_an_unverified_whitelist_still_excludes_a_format_it_omits(self):
        """Asymmetric on purpose. Someone recorded a scope for this programme, and
        honouring it understates the rebate, which is the recoverable direction.
        Ignoring it overstates the rebate, which is the failure this module exists
        to prevent."""
        assert verdict({"applicable_formats": ["feature"]}) == INELIGIBLE

    def test_an_unverified_whitelist_is_not_promoted_to_a_confirmation(self):
        """The same list cannot exclude AND confirm. A format on an unverified list
        is still only unverified: nobody has checked the list is complete."""
        assert verdict({"applicable_formats": ["feature", "short"]}) == UNVERIFIED


# ── 5 & 6. Conditional ───────────────────────────────────────────────────────

class TestConditional:
    def test_a_runtime_condition_is_settled_when_the_runtime_is_known(self):
        """Case 5."""
        row = conditional("Minimum runtime 40 minutes for live action.")
        assert verdict(row, project={"runtime_minutes": 52}) == ELIGIBLE

    def test_a_runtime_condition_below_the_minimum_excludes(self):
        row = conditional("Minimum runtime 40 minutes for live action.")
        assert verdict(row, project={"runtime_minutes": 12}) == INELIGIBLE

    def test_insufficient_project_data_asks_for_confirmation(self):
        """Case 6. Never assumed either way."""
        row = conditional("Minimum runtime 40 minutes for live action.")
        assert verdict(row, project={}) == NEEDS_CONFIRMATION
        assert verdict(row, project=None) == NEEDS_CONFIRMATION
        assert verdict(row, project={"runtime_minutes": None}) == NEEDS_CONFIRMATION

    def test_an_unevaluable_condition_is_surfaced_verbatim(self):
        row = conditional("Requires a theatrical release commitment in-territory.")
        result = evaluate_format_eligibility(row, "Short")
        assert result["verdict"] == NEEDS_CONFIRMATION
        assert "theatrical release" in result["explanation"]

    def test_a_conditional_whitelist_still_excludes_an_absent_format(self):
        """A condition cannot rescue a format the programme does not cover."""
        row = conditional("Animation may qualify from 24 minutes.", formats=["animation"])
        assert verdict(row, "Short", {"runtime_minutes": 90}) == INELIGIBLE

    def test_a_conditional_row_with_no_condition_text_asks_for_confirmation(self):
        assert verdict(conditional(None)) == NEEDS_CONFIRMATION


# ── 7. Other formats unaffected ──────────────────────────────────────────────

class TestOtherFormats:
    def test_a_feature_against_a_feature_programme_is_eligible(self):
        """Case 7."""
        assert verdict(verified(["feature"]), "Feature Film") == ELIGIBLE

    @pytest.mark.parametrize("fmt", ["Feature Film", "Documentary", "TV Series", "Animated Feature"])
    def test_a_verified_programme_listing_the_format_is_eligible(self, fmt):
        token = canonical_format(fmt)
        assert verdict(verified([token]), fmt) == ELIGIBLE

    def test_no_format_supplied_is_neither_confirmed_nor_excluded(self):
        assert verdict(verified(["feature"]), None) == NEEDS_CONFIRMATION
        assert verdict(unchecked(), None) == UNVERIFIED


# ── 8. Selection prefers verified over unknown ───────────────────────────────

class TestBestIncentivePrefersDependable:
    def test_a_verified_programme_beats_a_higher_rate_unknown_one(self):
        """Case 8. The inversion is the point: the larger number was never checked."""
        rows = [
            {**unchecked(), "program": "unchecked 40%", "rate_gross": 40},
            {**verified(["short"]), "program": "verified 20%", "rate_gross": 20},
        ]
        assert best_incentive(rows, "Short")["program"] == "verified 20%"

    def test_a_verified_ineligible_programme_is_dropped(self):
        """Case 2, through the selector."""
        rows = [
            {**verified(["feature"]), "program": "feature only 40%", "rate_gross": 40},
            {**verified(["short"]), "program": "shorts ok 20%", "rate_gross": 20},
        ]
        assert best_incentive(rows, "Short")["program"] == "shorts ok 20%"

    def test_rate_still_decides_between_equally_dependable_programmes(self):
        rows = [
            {**verified(["short"]), "program": "low", "rate_gross": 20},
            {**verified(["short"]), "program": "high", "rate_gross": 35},
        ]
        assert best_incentive(rows, "Short")["program"] == "high"

    def test_conditional_outranks_unverified(self):
        rows = [
            {**unchecked(), "program": "unchecked 40%", "rate_gross": 40},
            {**conditional("Theatrical release required."), "program": "conditional 10%", "rate_gross": 10},
        ]
        assert best_incentive(rows, "Short")["program"] == "conditional 10%"

    def test_all_ineligible_still_returns_a_row_without_claiming_eligibility(self):
        """Graceful degradation is retained so no caller crashes, but the verdict on
        the returned row says ineligible, so nothing can present it as available."""
        rows = [{**verified(["feature"]), "program": "only row", "rate_gross": 40}]
        chosen = best_incentive(rows, "Short")
        assert chosen["program"] == "only row"
        assert verdict(chosen) == INELIGIBLE

    def test_a_feature_project_selection_is_unchanged_by_this_work(self):
        """Case 12: non-format behaviour intact. Highest rate still wins."""
        rows = [
            {**verified(["feature"]), "program": "feature 40%", "rate_gross": 40},
            {**verified(["feature"]), "program": "feature 25%", "rate_gross": 25},
        ]
        assert best_incentive(rows, "Feature Film")["program"] == "feature 40%"

    def test_no_format_supplied_keeps_the_original_highest_rate_behaviour(self):
        rows = [
            {**unchecked(), "program": "high", "rate_gross": 40},
            {**unchecked(), "program": "low", "rate_gross": 10},
        ]
        assert best_incentive(rows)["program"] == "high"


# ── 9 & 10. The blanket warning is data-driven ───────────────────────────────

class TestBlanketWarningIsDataDriven:
    def test_it_is_raised_while_any_programme_is_unverified(self):
        """Case 9."""
        rows = [verified(["short"]), unchecked()]
        assert any_unverified_for_format(rows, "Short") is True

    def test_it_retires_once_every_programme_is_known(self):
        """Case 10. No code change required to switch it off."""
        rows = [verified(["short"]), verified(["feature"])]
        assert any_unverified_for_format(rows, "Short") is False

    def test_a_conditional_programme_that_cannot_be_settled_still_raises_it(self):
        rows = [verified(["short"]), conditional("Theatrical release required.")]
        assert any_unverified_for_format(rows, "Short") is True

    def test_a_conditional_programme_the_project_settles_does_not_raise_it(self):
        """The project facts have to reach the check, or a conditional programme holds
        the caveat open forever however complete the research gets."""
        rows = [conditional("Minimum runtime 20 minutes.")]
        assert any_unverified_for_format(rows, "Short", {"runtime_minutes": 25}) is False
        assert any_unverified_for_format(rows, "Short", {"runtime_minutes": 8}) is False
        assert any_unverified_for_format(rows, "Short") is True  # runtime unrecorded

    def test_no_programmes_raises_nothing(self):
        assert any_unverified_for_format([], "Short") is False
        assert any_unverified_for_format(None, "Short") is False


# ── 14. Programmes in one territory may differ ───────────────────────────────

class TestPerProgrammeNotPerTerritory:
    def test_two_programmes_in_one_territory_can_disagree(self):
        """Case 14, and the reason this is not a territory-level flag."""
        germany = [
            {**verified(["animation", "short"]), "territory": "Germany", "program": "shorts scheme", "rate_gross": 20},
            {**verified(["feature"]), "territory": "Germany", "program": "feature scheme", "rate_gross": 40},
        ]
        assert verdict(germany[0]) == ELIGIBLE
        assert verdict(germany[1]) == INELIGIBLE
        # And the selector picks the one this production can actually use.
        assert best_incentive(germany, "Short")["program"] == "shorts scheme"

    def test_a_territory_is_not_written_off_by_one_ineligible_programme(self):
        """UX requirement: another programme in the territory failing must not
        remove the territory."""
        germany = [
            {**verified(["feature"]), "territory": "Germany", "program": "feature only", "rate_gross": 40},
            {**unchecked(), "territory": "Germany", "program": "unchecked", "rate_gross": 15},
        ]
        chosen = best_incentive(germany, "Short")
        assert chosen["program"] == "unchecked"
        assert verdict(chosen) == UNVERIFIED


# ── Taxonomy ─────────────────────────────────────────────────────────────────

class TestCanonicalTaxonomy:
    @pytest.mark.parametrize("label", ["Short", "short", "short film", "short-film", "short_film", "Shorts"])
    def test_every_short_spelling_collapses_to_one_token(self, label):
        assert canonical_format(label) == "short"

    def test_the_taxonomy_has_one_definition(self):
        """signal_normalise re-exports the shared table rather than holding a copy."""
        from app.core.formats import canonical_format as shared
        from app.modules.b2b.signal_normalise import canonical_format as reexported

        assert reexported is shared

    def test_an_unrecognised_format_is_normalised_not_dropped(self):
        assert canonical_format("Music Video") == "music_video"
