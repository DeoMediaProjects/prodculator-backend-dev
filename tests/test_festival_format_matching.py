"""Festival matching must use the one format vocabulary, not a second hand-kept map.

A short film's report said "No festival matches for this production's format and
timing" while 107 festivals in the dataset accept shorts. The builder mapped the
wizard's format label to the festival vocabulary through a hardcoded dict, and that
dict had no key for "Short" — only "Short Film", a label the wizard does not offer.
A miss returned no festivals at all rather than falling back, so two of the seven
selectable formats matched nothing, ever.

app/core/formats.py exists so both sides read one table. These tests pin every label
the wizard can produce to a token the festival data actually uses.
"""
from __future__ import annotations

import pytest

from app.core.formats import canonical_format
from app.modules.reports.matching import match_festivals

# The tokens present in film_festivals.eligible_formats.
FESTIVAL_TOKENS = {"short", "documentary", "feature", "animation", "experimental", "tv_series"}

# Exactly the options the wizard offers (FORMAT_OPTIONS in AnalysisWizard.tsx).
WIZARD_FORMATS = [
    "Feature Film",
    "TV Series",
    "TV Pilot",
    "Limited Series",
    "Short",
    "Documentary",
    "Animated Feature",
]


@pytest.mark.parametrize("label", WIZARD_FORMATS)
def test_every_selectable_format_maps_to_a_token_the_data_uses(label):
    token = canonical_format(label)
    assert token in FESTIVAL_TOKENS, f"{label!r} maps to {token!r}, which no festival lists"


@pytest.mark.parametrize("label,expected", [
    ("Short", "short"),
    ("Short Film", "short"),
    ("Animated Feature", "animation"),
    ("Feature Film", "feature"),
    ("TV Pilot", "tv_series"),
    ("Limited Series", "tv_series"),
    ("Documentary", "documentary"),
])
def test_the_specific_labels_that_used_to_miss(label, expected):
    """Short and Animated Feature had no key in the old map and matched nothing."""
    assert canonical_format(label) == expected


def festival(name, formats, min_m=0, max_m=24):
    return {
        "name": name,
        "eligible_formats": formats,
        "min_months_after_completion": min_m,
        "max_months_after_completion": max_m,
        # Genre-agnostic, so these tests isolate the format gate. A festival with
        # no genre overlap is excluded by a separate gate that is not under test.
        "genre_tags": ["all"],
        "tier": "tier_1",
    }


def run(fmt, festivals):
    return match_festivals(
        festivals,
        genres=["drama"],
        representation_gender=None,
        representation_minority=[],
        production_format=fmt,
        completion_date=None,
        comparable_production_festivals=None,
        target_audience=[],
        audience_segments=[],
    )


class TestTheGateItself:
    def test_a_short_reaches_festivals_that_accept_shorts(self):
        fests = [festival("Shorts Fest", ["short"]), festival("Features Only", ["feature"])]
        names = [m.festival["name"] for m in run(canonical_format("Short"), fests)]
        assert names == ["Shorts Fest"]

    def test_an_animated_feature_reaches_animation_festivals(self):
        fests = [festival("Annecy", ["animation"]), festival("Docs", ["documentary"])]
        names = [m.festival["name"] for m in run(canonical_format("Animated Feature"), fests)]
        assert names == ["Annecy"]

    def test_the_raw_display_label_would_have_matched_nothing(self):
        """Pins why canonicalisation is required rather than incidental: the label
        the wizard produces is not the token the data holds."""
        fests = [festival("Shorts Fest", ["short"])]
        assert run("Animated Feature", fests) == []
        assert run("Feature Film", [festival("Cannes", ["feature"])]) == []

    def test_a_festival_that_lists_several_formats_matches_each_of_them(self):
        fests = [festival("Sundance", ["feature", "short", "documentary"])]
        for label in ("Feature Film", "Short", "Documentary"):
            assert len(run(canonical_format(label), fests)) == 1, label

    def test_a_format_the_festival_excludes_is_still_excluded(self):
        """The gate must keep gating. Fixing the vocabulary must not turn it off."""
        assert run(canonical_format("Short"), [festival("Features Only", ["feature"])]) == []

    def test_a_festival_with_no_declared_formats_matches_nothing(self):
        assert run(canonical_format("Short"), [festival("Unknown", [])]) == []
        assert run(canonical_format("Short"), [festival("Unknown", None)]) == []
