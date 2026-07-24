"""Regression test for a real client-facing bug: distributor matching's
"general" representation-tag check was skipping ANY distributor whose tags
included "general", even when combined with a real specialty (e.g.
["lgbtq+", "general"]). Real active distributors carry exactly this
combination, so they silently lost their representation-match score and
could drop out of a client's report when that client filtered for
representation matching. The fix: only skip when "general" is the ONLY tag.
"""
from app.modules.reports.matching import match_distributors


def _base_distributor(**overrides) -> dict:
    dist = {
        "name": "Test Distributor",
        "specialty_genres": ["drama"],
    }
    dist.update(overrides)
    return dist


class TestGeneralTagRepresentationMatching:
    def test_general_only_tag_does_not_score_representation(self):
        """A distributor with NO real specialty (only "general") must not
        get representation-match points -- opt-in only, correctly strict."""
        dist = _base_distributor(specialty_representation=["general"])
        matches = match_distributors(
            [dist],
            genres=[],
            representation_gender="Woman",
            representation_minority=["LGBTQ+"],
            matched_festival_names=[],
        )
        assert not matches or not any(
            "Representation selection" in r for r in matches[0].reasons
        )

    def test_combined_general_and_specialty_tag_scores_representation(self):
        """The actual bug: a distributor tagged ["lgbtq+", "general"] must
        still score on the lgbtq+ specialty -- "general" being present
        elsewhere in the array must not blank out real specialty tags."""
        dist = _base_distributor(specialty_representation=["lgbtq+", "general"])
        matches = match_distributors(
            [dist],
            genres=[],
            representation_gender=None,
            representation_minority=["LGBTQ+"],
            matched_festival_names=[],
        )
        assert len(matches) == 1
        assert any("Representation selection" in r for r in matches[0].reasons)
        assert matches[0].score >= 3.0

    def test_racial_ethnic_minority_combined_with_general_scores(self):
        dist = _base_distributor(specialty_representation=["racial_ethnic_minority", "general"])
        matches = match_distributors(
            [dist],
            genres=[],
            representation_gender=None,
            representation_minority=["Racial/Ethnic Minority"],
            matched_festival_names=[],
        )
        assert len(matches) == 1
        assert any("Representation selection" in r for r in matches[0].reasons)

    def test_pure_specialty_tag_unaffected(self):
        """No "general" at all -- unaffected by this fix, still scores."""
        dist = _base_distributor(specialty_representation=["women"])
        matches = match_distributors(
            [dist],
            genres=[],
            representation_gender="Woman",
            representation_minority=[],
            matched_festival_names=[],
        )
        assert len(matches) == 1
        assert any("Representation selection" in r for r in matches[0].reasons)

    def test_missing_representation_field_defaults_to_general_only(self):
        dist = _base_distributor()  # no specialty_representation key at all
        matches = match_distributors(
            [dist],
            genres=[],
            representation_gender="Woman",
            representation_minority=["LGBTQ+"],
            matched_festival_names=[],
        )
        assert not matches or not any(
            "Representation selection" in r for r in matches[0].reasons
        )
