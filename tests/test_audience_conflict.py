"""One intake checkbox must not outvote the screenplay.

The Devil Wears Prada regression found Chicago and New York International
Children's Film Festivals recommended for an adult workplace comedy-drama. The
matcher was working: a target-audience field said kids/family, those festivals
accept kids/family, the two overlapped.

The rule the regression report states has two halves, and both are tested
here: the declared value is preserved, and it stops earning matches while the
script contradicts it — with the conflict surfaced rather than applied in
silence.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.reports.audience_conflict import (
    ADULT,
    CHILDREN_FAMILY,
    detect_audience_conflict,
)


def _script(audience: str | None):
    return SimpleNamespace(targetAudience=audience)


class TestDetection:
    def test_kids_declared_against_an_adult_screenplay(self):
        conflict = detect_audience_conflict(
            ["Children/Family"], _script("Adults")
        )
        assert conflict is not None
        assert conflict.declared_bucket == CHILDREN_FAMILY
        assert conflict.script_bucket == ADULT

    def test_it_works_in_the_other_direction(self):
        # A children's screenplay declared for adults is the same defect, and
        # would push an adult-only festival list at an animated family film.
        conflict = detect_audience_conflict(["Adults"], _script("Family"))
        assert conflict is not None
        assert conflict.declared_bucket == ADULT

    def test_agreement_is_not_a_conflict(self):
        assert detect_audience_conflict(["Adults"], _script("Mature")) is None
        assert detect_audience_conflict(["Family"], _script("Kids")) is None


class TestItRefusesToGuess:
    def test_general_audiences_is_the_analyser_shrugging(self):
        # "General audiences" is the script service's own default when it could
        # not decide. Reading it as a children's film would raise a conflict on
        # every script the analyser was unsure about, and suppress matches for
        # all of them.
        assert detect_audience_conflict(["Adults"], _script("General audiences")) is None

    @pytest.mark.parametrize("declared", [[], None, [""], ["  "]])
    def test_nothing_declared_is_not_a_contradiction(self, declared):
        assert detect_audience_conflict(declared, _script("Adults")) is None

    def test_no_script_audience_is_not_a_contradiction(self):
        assert detect_audience_conflict(["Children"], _script(None)) is None
        assert detect_audience_conflict(["Children"], None) is None

    def test_declaring_both_is_not_arbitrated(self):
        # A production declaring children AND adults has told us something this
        # rule cannot settle. Picking one would be the behaviour it exists to
        # stop.
        assert detect_audience_conflict(
            ["Children/Family", "Adults"], _script("Adults")
        ) is None

    def test_an_unrecognised_audience_is_undecided_not_conflicting(self):
        # A false conflict costs a producer real matches, so the vocabulary is
        # small on purpose and anything outside it is silence.
        assert detect_audience_conflict(["Urban"], _script("Arthouse")) is None

    def test_young_adult_does_not_contradict_family(self):
        # YA overlaps family viewing. Treating it as adult-only would suppress
        # matches on a distinction the words do not carry.
        assert detect_audience_conflict(["Young Adult"], _script("Family")) is None


class TestTheNote:
    def test_it_names_both_sides_and_resolves_neither(self):
        note = detect_audience_conflict(["Children/Family"], _script("Adults")).note
        assert "Children/Family" in note
        assert "adults" in note.lower()
        # It must not say which of the two is the mistaken one. "Correct
        # whichever is wrong" declines to pick, which is the point.
        assert "whichever" in note.lower()
        assert "your declared audience is wrong" not in note.lower()
        assert "the script is wrong" not in note.lower()

    def test_it_says_matching_has_stopped_using_the_declared_value(self):
        # A producer whose matches quietly changed, with no reason given, has
        # been handed a different report and no way to understand it.
        note = detect_audience_conflict(["Kids"], _script("Mature")).note
        assert "does not use the declared audience" in note


class TestTheMatchersStopScoringIt:
    """The declared value is preserved everywhere and earns nothing here."""

    # `genre_tags` so the festival clears the genre step, and the caller side
    # of the format gate takes a canonical token rather than a display label —
    # the matcher documents that contract deliberately.
    FESTIVALS = [{
        "name": "Some Children's Film Festival",
        "eligible_formats": ["Feature Film"],
        "genre_tags": ["all"],
        "audience_focus": ["children"],
        "representation_focus": ["general"],
        "tier": "Mid-tier",
    }]

    def _scores(self, *, conflicted: bool):
        from app.modules.reports.matching import match_festivals

        return match_festivals(
            self.FESTIVALS,
            genres=["horror"],
            representation_gender=None,
            representation_minority=[],
            production_format="feature",
            completion_date=None,
            # "children" exactly: the matcher intersects lowercased strings,
            # so "Children/Family" would not have met a festival tagged
            # "children" with or without this rule. Worth knowing, and a
            # separate question from the one under test here.
            target_audience=["children"],
            audience_segments=[],
            audience_in_conflict=conflicted,
        )

    def test_the_audience_overlap_scores_when_there_is_no_conflict(self):
        matched = self._scores(conflicted=False)
        assert any(
            "declared target audience" in reason
            for m in matched for reason in m.reasons
        )

    def test_it_scores_nothing_when_the_script_contradicts_it(self):
        matched = self._scores(conflicted=True)
        assert not any(
            "declared target audience" in reason
            for m in matched for reason in m.reasons
        )
