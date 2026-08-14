"""Issue 6: script metrics are counted from the file and are reproducible.

Two reports from apparently the same screenplay disagreed:

    earlier   ~45 scenes   ~80% interior   5 shooting days   complexity Medium
    latest     29 scenes    72% interior   6 shooting days   complexity High

Neither was a parse. Scene counts were LLM ``extSceneCount``/``intSceneCount`` values
summed across chunks; the interior percentage divided one model number by the sum of
two; shooting days were a trimmed mean of per-chunk guesses floored at
``total_scenes // 10``; complexity was a free choice of one of four labels in a
separate later call with no count fed into it. On top of that, every chunk after the
first was prefixed with an 800-character overlap tail of the previous one and nothing
told the model to skip it, so a heading in that window could be counted twice, and a
single chunk timing out silently removed its scenes from the total.

The only regex in the pipeline was used to pick chunk boundaries; its match count was
never the scene count.

These tests assert the two properties that were missing: the numbers come from the
text, and the same bytes produce the same numbers.

NOTE ON THE FIXTURE: the actual EJE screenplay is not in this repository (there are no
screenplay fixtures anywhere under the project, and storage/ is empty), so it cannot
be committed as a test asset. ``fixtures/screenplay_eje_style.txt`` is a
representative stand-in built to exercise the specific constructs the EJE report
implies — Zulu dialogue, INT./EXT. and I/E slugs, CONTINUOUS and CONT'D
continuations, repeated locations, mixed day/night — because those are what the parser
has to get right. Swap in the real screenplay and these tests still apply.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.scripts.screenplay_metrics import (
    PARSER_VERSION,
    compute_metrics,
    estimate_shooting_days,
    parse_scenes,
    score_complexity,
)

FIXTURE = Path(__file__).parent / "fixtures" / "screenplay_eje_style.txt"


@pytest.fixture(scope="module")
def script_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def metrics(script_text: str) -> dict:
    return compute_metrics(script_text)


# ── Determinism ──────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_repeated_runs_are_identical(self, script_text: str):
        """The property the old pipeline could not offer at any temperature."""
        first = compute_metrics(script_text)
        for _ in range(5):
            again = compute_metrics(script_text)
            assert again == first

    def test_every_reported_metric_is_stable(self, script_text: str):
        runs = [compute_metrics(script_text) for _ in range(3)]
        for key in (
            "totalScenes", "interiorPct", "exteriorPct", "dayScenes", "nightScenes",
            "distinctLocations", "complexity", "estimatedShootingDays",
        ):
            values = {run[key] for run in runs}
            assert len(values) == 1, f"{key} varied across runs: {values}"

    def test_the_digest_identifies_the_input(self, script_text: str):
        assert compute_metrics(script_text)["scriptSha256"] == (
            compute_metrics(script_text)["scriptSha256"]
        )
        assert compute_metrics(script_text + "\n")["scriptSha256"] != (
            compute_metrics(script_text)["scriptSha256"]
        )

    def test_the_parser_version_is_recorded(self, metrics: dict):
        """So "the same file gave different numbers" is answerable."""
        assert metrics["parserVersion"] == PARSER_VERSION


# ── Scene detection ──────────────────────────────────────────────────────────

class TestSceneDetection:
    def test_scene_count_matches_the_fixture(self, metrics: dict):
        # 17 headings, of which 3 are continuations of the heading above them:
        # "(CONTINUOUS)" twice and "(CONT'D)" once, each at the same location as the
        # scene it follows. 14 countable scenes.
        assert metrics["headingsFound"] == 17
        assert metrics["continuationHeadings"] == 3
        assert metrics["totalScenes"] == 14

    def test_continuations_are_excluded_from_the_count(self, script_text: str):
        """There was no dedup of any kind before this, and the 800-character chunk
        overlap actively encouraged double-counting."""
        scenes = parse_scenes(script_text)
        continuations = [s for s in scenes if s.is_continuation]
        assert len(continuations) == 3
        for scene in continuations:
            assert "CONT" in scene.raw.upper()

    def test_parenthesised_continuation_markers_are_recognised(self):
        """"(CONTINUOUS)" and "(CONT'D)" are the standard notation.

        Anchoring the marker on end-of-line alone missed both, because the closing
        bracket sits between the word and the line end.
        """
        for marker in ("(CONTINUOUS)", "(CONT'D)", "CONTINUOUS", "CONT'D"):
            text = (
                f"INT. KITCHEN - DAY\n\nAction.\n\n"
                f"INT. KITCHEN - DAY {marker}\n\nMore action.\n"
            )
            result = compute_metrics(text)
            assert result["totalScenes"] == 1, marker
            assert result["continuationHeadings"] == 1, marker

    def test_a_bare_later_is_a_new_scene_not_a_continuation(self):
        """Time has moved, so it is a new scene even at the same location."""
        text = (
            "INT. BEDROOM - NIGHT\n\nAction.\n\n"
            "INT. BEDROOM - LATER\n\nMore action.\n"
        )
        assert compute_metrics(text)["totalScenes"] == 2

    def test_a_continuation_at_a_different_location_is_a_new_scene(self):
        """CONTINUOUS marks time, not place. A different location is a real scene."""
        text = (
            "INT. KITCHEN - DAY\n\nAction.\n\n"
            "INT. HALLWAY - CONTINUOUS\n\nMore action.\n"
        )
        result = compute_metrics(text)
        assert result["totalScenes"] == 2

    def test_prose_beginning_with_a_prefix_word_is_not_a_heading(self):
        text = (
            "INT. KITCHEN - DAY\n\n"
            "Interior designers had been and gone.\n"
            "External pressure mounts.\n"
        )
        assert compute_metrics(text)["totalScenes"] == 1

    def test_scene_numbers_are_tolerated(self):
        text = (
            "12  INT. KITCHEN - DAY\n\nAction.\n\n"
            "A1. EXT. STREET - NIGHT\n\nAction.\n"
        )
        assert compute_metrics(text)["totalScenes"] == 2

    def test_indented_headings_are_found(self):
        """Screenplay PDFs carry a leading indent after text extraction."""
        text = "    INT. KITCHEN - DAY\n\nAction.\n"
        assert compute_metrics(text)["totalScenes"] == 1


# ── Interior / exterior ──────────────────────────────────────────────────────

class TestInteriorExterior:
    def test_the_split_is_computed_from_counted_headings(self, metrics: dict):
        assert metrics["interiorScenes"] == 8
        assert metrics["exteriorScenes"] == 4
        assert metrics["mixedScenes"] == 2
        # The three buckets always sum to the scene total. They did not before: a scene
        # the model classified as neither silently changed the denominator.
        assert metrics["interiorScenes"] + metrics["exteriorScenes"] + metrics["mixedScenes"] == (
            metrics["totalScenes"]
        )

    def test_mixed_headings_count_toward_both_percentages(self, metrics: dict):
        """INT./EXT. and I/E are genuinely both.

        Before this the prompt named INT. and EXT. only and said nothing about mixed
        slugs, so one could land in either bucket, in neither, or in both — and because
        the percentage divided by (int + ext), a scene counted in neither silently moved
        it. That is a plausible cause of 80% vs 72% on one file.
        """
        # (8 interior + 2 mixed) / 14 and (4 exterior + 2 mixed) / 14.
        assert metrics["interiorPct"] == pytest.approx(71.4)
        assert metrics["exteriorPct"] == pytest.approx(42.9)

    def test_int_ext_variants_all_classify_as_mixed(self):
        for prefix in ("INT./EXT.", "INT/EXT.", "EXT./INT.", "I/E.", "E/I."):
            text = f"{prefix} CAR - DAY\n\nAction.\n"
            result = compute_metrics(text)
            assert result["mixedScenes"] == 1, prefix
            assert result["interiorScenes"] == 0, prefix
            assert result["exteriorScenes"] == 0, prefix

    def test_est_counts_as_exterior(self):
        assert compute_metrics("EST. CITY SKYLINE - DAY\n\nAction.\n")["exteriorScenes"] == 1


# ── Day / night ──────────────────────────────────────────────────────────────

class TestTimeOfDay:
    def test_day_and_night_are_counted(self, metrics: dict):
        assert metrics["dayScenes"] == 4
        assert metrics["nightScenes"] == 9   # includes DUSK and both DAWN scenes
        assert metrics["otherTimeScenes"] == 1  # the bare "LATER"
        assert (
            metrics["dayScenes"] + metrics["nightScenes"] + metrics["otherTimeScenes"]
            == metrics["totalScenes"]
        )

    def test_time_is_read_from_the_time_segment_not_the_location(self):
        """A location called DAYCARE is not a day scene by virtue of its name."""
        text = "INT. DAYCARE CENTRE - NIGHT\n\nAction.\n"
        result = compute_metrics(text)
        assert result["nightScenes"] == 1
        assert result["dayScenes"] == 0

    def test_dusk_and_dawn_variants_are_night(self):
        for token in ("DUSK", "EVENING", "MIDNIGHT", "TWILIGHT"):
            text = f"EXT. STREET - {token}\n\nAction.\n"
            assert compute_metrics(text)["nightScenes"] == 1, token


# ── Locations ────────────────────────────────────────────────────────────────

class TestLocations:
    def test_repeated_locations_are_grouped(self, metrics: dict):
        # The corridor appears twice but the second is a continuation, so it counts once.
        assert metrics["namedLocations"]["HOSPITAL CORRIDOR"] == 1
        # The bedroom appears twice as two genuine scenes (NIGHT, then LATER).
        assert metrics["namedLocations"]["HOUSE - BEDROOM"] == 2

    def test_multi_part_locations_keep_their_sub_location(self, metrics: dict):
        """"INT. HOUSE - HALLWAY - NIGHT" is the hallway, not the house."""
        assert "HOUSE - HALLWAY" in metrics["namedLocations"]
        assert "HOUSE - KITCHEN" in metrics["namedLocations"]

    def test_distinct_location_count(self, metrics: dict):
        assert metrics["distinctLocations"] == 11

    def test_primary_location_is_the_most_frequent(self, metrics: dict):
        assert metrics["primaryLocation"] in metrics["namedLocations"]
        counts = metrics["namedLocations"]
        assert counts[metrics["primaryLocation"]] == max(counts.values())


# ── Shooting days ────────────────────────────────────────────────────────────

class TestShootingDays:
    def test_days_follow_from_scenes_and_complexity(self):
        # A stated rule, so a producer can disagree with the assumption rather than
        # with an opaque estimate.
        assert estimate_shooting_days(14, "Medium") == 2
        assert estimate_shooting_days(35, "Medium") == 5
        assert estimate_shooting_days(35, "High") == 7
        assert estimate_shooting_days(35, "Very High") == 10
        assert estimate_shooting_days(35, "Low") == 4

    def test_a_higher_complexity_never_produces_fewer_days(self):
        for scenes in (5, 20, 45, 90):
            days = [
                estimate_shooting_days(scenes, band)
                for band in ("Low", "Medium", "High", "Very High")
            ]
            assert days == sorted(days), f"{scenes}: {days}"

    def test_no_scenes_means_no_days(self):
        assert estimate_shooting_days(0, "Medium") == 0

    def test_an_unknown_complexity_falls_back_to_medium(self):
        assert estimate_shooting_days(14, "Wibble") == estimate_shooting_days(14, "Medium")


# ── Complexity ───────────────────────────────────────────────────────────────

class TestComplexity:
    def test_complexity_is_scored_from_inputs_not_chosen(self):
        low = score_complexity(
            scene_count=8, exterior_pct=10.0, night_pct=0.0,
            distinct_locations=2, flags={},
        )
        assert low["complexity"] == "Low"

        high = score_complexity(
            scene_count=95, exterior_pct=60.0, night_pct=40.0,
            distinct_locations=25, flags={"stunts": True, "waterWork": True},
        )
        assert high["complexity"] == "Very High"

    def test_the_drivers_are_reported(self):
        result = score_complexity(
            scene_count=50, exterior_pct=55.0, night_pct=35.0,
            distinct_locations=10, flags={"stunts": True},
        )
        assert result["drivers"]
        assert any("50 scenes" in d for d in result["drivers"])
        assert any("stunt" in d for d in result["drivers"])

    def test_multilingual_dialogue_raises_complexity(self):
        """The EJE report identifies Zulu-language casting as a real driver."""
        mono = score_complexity(
            scene_count=16, exterior_pct=35.0, night_pct=50.0,
            distinct_locations=13, flags={"languages": ["English"]},
        )
        multi = score_complexity(
            scene_count=16, exterior_pct=35.0, night_pct=50.0,
            distinct_locations=13, flags={"languages": ["English", "Zulu"]},
        )
        assert multi["points"] > mono["points"]
        assert any("languages" in d for d in multi["drivers"])

    def test_the_same_inputs_always_score_the_same_label(self):
        args = dict(
            scene_count=42, exterior_pct=48.0, night_pct=25.0,
            distinct_locations=11, flags={"weatherDependent": True},
        )
        labels = {score_complexity(**args)["complexity"] for _ in range(10)}
        assert len(labels) == 1


# ── Unparseable input ────────────────────────────────────────────────────────

class TestUnparseableInput:
    def test_no_headings_reports_not_parsed_rather_than_zero(self):
        """A zero from a failed parse must never outrank a real estimate."""
        result = compute_metrics("A treatment. No scene headings anywhere in it.")
        assert result["parsed"] is False
        assert result["totalScenes"] is None

    def test_empty_input_is_safe(self):
        result = compute_metrics("")
        assert result["parsed"] is False
        assert result["totalScenes"] is None
