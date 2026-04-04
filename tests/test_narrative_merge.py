"""Tests for ScriptAnalysisService._merge_ai_narratives and _fill_narrative_defaults.

These static methods handle merging AI-generated narrative content into the
pre-built deterministic skeleton, and filling safe defaults when AI fails.
"""
from __future__ import annotations

import copy
import pytest

from app.modules.scripts.service import ScriptAnalysisService

merge = ScriptAnalysisService._merge_ai_narratives
defaults = ScriptAnalysisService._fill_narrative_defaults


# ── Helpers ──────────────────────────────────────────────────────────────────

def _skeleton(
    *,
    territories: list[str] | None = None,
    crew: bool = True,
    comparables: bool = True,
    weather: bool = True,
    deep_dives: bool = True,
) -> dict:
    """Build a minimal valid skeleton for merge tests."""
    territories = territories or ["United Kingdom", "Canada"]
    rankings = []
    for t in territories:
        rankings.append({
            "name": t,
            "incentiveStrength": 70,
            "incentiveReliability": 80,
            "currencyAdvantage": 60,
            "costEfficiency": None,
            "crewDepth": None,
            "infrastructure": None,
            "reasoning": None,
            "keyAdvantages": None,
            "keyRisks": ["DB-computed risk A"],
        })

    skel: dict = {
        "genre": None,
        "tone": None,
        "scale": None,
        "complexity": None,
        "alternativeStrategy": None,
        "executiveSummary": {"keyInsights": None, "topTerritory": territories[0]},
        "locationRankings": rankings,
        "crewInsights": [],
        "comparables": [],
        "weatherLogistics": [],
        "territoryDeepDives": [],
        "scoringMethodology": None,
    }

    if crew:
        skel["crewInsights"] = [
            {"territory": t, "availability": None, "specialties": None, "tradeoff": None}
            for t in territories
        ]
    if comparables:
        skel["comparables"] = [
            {"title": "Film A", "relevanceDescription": None, "_budgetGapFlag": "larger"},
            {"title": "Film B", "relevanceDescription": None},
        ]
    if weather:
        skel["weatherLogistics"] = [
            {"territory": t, "infrastructure": None, "seasonalConsiderations": None}
            for t in territories
        ]
    if deep_dives:
        skel["territoryDeepDives"] = [
            {
                "name": t, "country": t, "rebate": "25% gross",
                "estimatedRebate": "£500,000", "score": None,
                "infrastructure": None, "keyAdvantages": None, "keyRisks": None,
                # DB-computed by builder — pre-set here to simulate builder output
                "culturalTestLikelihood": "High (85%)",
                "adminComplexity": "Medium",
                "paymentSpeed": "6-12 months",
            }
            for t in territories[:2]
        ]

    return skel


# ── Top-level narrative fields ───────────────────────────────────────────────

class TestTopLevelFields:
    def test_genre_tone_scale_from_ai(self):
        skel = _skeleton()
        ai = {"genre": "Thriller", "tone": "Dark", "scale": "Large"}
        result = merge(skel, ai)
        assert result["genre"] == "Thriller"
        assert result["tone"] == "Dark"
        assert result["scale"] == "Large"

    def test_missing_genre_falls_back(self):
        skel = _skeleton()
        result = merge(skel, {})
        assert result["genre"] == "Drama"  # default
        assert result["tone"] == "Unknown"
        assert result["scale"] == "Unknown"

    def test_complexity_validated(self):
        skel = _skeleton()
        assert merge(skel, {"complexity": "High"})["complexity"] == "High"

    def test_complexity_invalid_defaults_medium(self):
        skel = _skeleton()
        assert merge(skel, {"complexity": "Super Hard"})["complexity"] == "Medium"

    def test_complexity_missing_defaults_medium(self):
        skel = _skeleton()
        assert merge(skel, {})["complexity"] == "Medium"

    def test_alternative_strategy_from_ai(self):
        skel = _skeleton()
        result = merge(skel, {"alternativeStrategy": "Try New Zealand"})
        assert result["alternativeStrategy"] == "Try New Zealand"

    def test_alternative_strategy_fallback(self):
        skel = _skeleton()
        result = merge(skel, {})
        assert "top-ranked" in result["alternativeStrategy"].lower()


# ── Executive summary ────────────────────────────────────────────────────────

class TestExecutiveSummary:
    def test_key_insights_from_ai(self):
        skel = _skeleton()
        ai = {"executiveSummary_keyInsights": "The UK offers the best incentive."}
        result = merge(skel, ai)
        assert result["executiveSummary"]["keyInsights"] == "The UK offers the best incentive."

    def test_key_insights_fallback_when_missing(self):
        skel = _skeleton()
        result = merge(skel, {})
        assert "unavailable" in result["executiveSummary"]["keyInsights"].lower()

    def test_existing_key_insights_preserved_when_ai_absent(self):
        skel = _skeleton()
        skel["executiveSummary"]["keyInsights"] = "Pre-existing insight"
        result = merge(skel, {})
        assert result["executiveSummary"]["keyInsights"] == "Pre-existing insight"


# ── Location narratives ──────────────────────────────────────────────────────

class TestLocationNarratives:
    def test_ai_dimensions_merged(self):
        skel = _skeleton(territories=["UK"])
        ai = {"locationNarratives": {"UK": {"costEfficiency": 85, "crewDepth": 72, "infrastructure": 90}}}
        result = merge(skel, ai)
        loc = result["locationRankings"][0]
        assert loc["costEfficiency"] == 85
        assert loc["crewDepth"] == 72
        assert loc["infrastructure"] == 90

    def test_ai_dimensions_clamped(self):
        skel = _skeleton(territories=["UK"])
        ai = {"locationNarratives": {"UK": {"costEfficiency": 150, "crewDepth": -20, "infrastructure": 100}}}
        result = merge(skel, ai)
        loc = result["locationRankings"][0]
        assert loc["costEfficiency"] == 100
        assert loc["crewDepth"] == 0
        assert loc["infrastructure"] == 100

    def test_missing_ai_dimensions_default_50(self):
        skel = _skeleton(territories=["UK"])
        result = merge(skel, {})
        loc = result["locationRankings"][0]
        assert loc["costEfficiency"] == 50
        assert loc["crewDepth"] == 50
        assert loc["infrastructure"] == 50

    def test_reasoning_from_ai(self):
        skel = _skeleton(territories=["UK"])
        ai = {"locationNarratives": {"UK": {"reasoning": ["Strong studios", "Tax credit"]}}}
        result = merge(skel, ai)
        assert result["locationRankings"][0]["reasoning"] == ["Strong studios", "Tax credit"]

    def test_reasoning_fallback(self):
        skel = _skeleton(territories=["UK"])
        result = merge(skel, {})
        assert len(result["locationRankings"][0]["reasoning"]) == 1

    def test_key_advantages_from_ai(self):
        skel = _skeleton(territories=["UK"])
        ai = {"locationNarratives": {"UK": {"keyAdvantages": ["World-class crew"]}}}
        result = merge(skel, ai)
        assert result["locationRankings"][0]["keyAdvantages"] == ["World-class crew"]

    def test_db_risks_preserved_ai_risks_appended(self):
        skel = _skeleton(territories=["UK"])
        ai = {"locationNarratives": {"UK": {"keyRisks_additional": ["Weather unpredictable"]}}}
        result = merge(skel, ai)
        risks = result["locationRankings"][0]["keyRisks"]
        assert "DB-computed risk A" in risks
        assert "Weather unpredictable" in risks

    def test_duplicate_ai_risks_not_added(self):
        skel = _skeleton(territories=["UK"])
        ai = {"locationNarratives": {"UK": {"keyRisks_additional": ["DB-computed risk A"]}}}
        result = merge(skel, ai)
        risks = result["locationRankings"][0]["keyRisks"]
        assert risks.count("DB-computed risk A") == 1

    def test_unmatched_territory_ignored(self):
        skel = _skeleton(territories=["UK"])
        ai = {"locationNarratives": {"Narnia": {"costEfficiency": 99}}}
        result = merge(skel, ai)
        assert result["locationRankings"][0]["costEfficiency"] == 50  # default


# ── Crew narratives ──────────────────────────────────────────────────────────

class TestCrewNarratives:
    def test_crew_fields_from_ai(self):
        skel = _skeleton(territories=["UK"])
        ai = {"crewNarratives": {"UK": {
            "availability": "High", "specialties": ["VFX"], "tradeoff": "Premium rates"
        }}}
        result = merge(skel, ai)
        crew = result["crewInsights"][0]
        assert crew["availability"] == "High"
        assert crew["specialties"] == ["VFX"]
        assert crew["tradeoff"] == "Premium rates"

    def test_crew_defaults_when_missing(self):
        skel = _skeleton(territories=["UK"])
        result = merge(skel, {})
        crew = result["crewInsights"][0]
        assert crew["availability"] == "Medium"
        assert crew["specialties"] == []
        assert "crew cost" in crew["tradeoff"].lower()


# ── Comparable descriptions ──────────────────────────────────────────────────

class TestComparableDescriptions:
    def test_description_from_ai(self):
        skel = _skeleton()
        ai = {"comparableDescriptions": {"Film A": "Shot in UK with similar budget"}}
        result = merge(skel, ai)
        comp = next(c for c in result["comparables"] if c["title"] == "Film A")
        assert "Shot in UK" in comp["relevanceDescription"]

    def test_budget_gap_flag_appended(self):
        skel = _skeleton()
        ai = {"comparableDescriptions": {"Film A": "Shot in UK"}}
        result = merge(skel, ai)
        comp = next(c for c in result["comparables"] if c["title"] == "Film A")
        assert "budget gap" in comp["relevanceDescription"].lower()
        assert "_budgetGapFlag" not in comp  # internal field removed

    def test_budget_gap_not_duplicated(self):
        skel = _skeleton()
        ai = {"comparableDescriptions": {"Film A": "Shot in UK. Note: budget gap present."}}
        result = merge(skel, ai)
        comp = next(c for c in result["comparables"] if c["title"] == "Film A")
        # Should not double-add the flag since "budget gap" already in desc
        assert comp["relevanceDescription"].count("budget gap") == 1

    def test_missing_description_gets_default(self):
        skel = _skeleton()
        result = merge(skel, {})
        comp = next(c for c in result["comparables"] if c["title"] == "Film B")
        assert "comparable" in comp["relevanceDescription"].lower()

    def test_missing_description_with_flag_gets_default_plus_flag(self):
        skel = _skeleton()
        result = merge(skel, {})
        comp = next(c for c in result["comparables"] if c["title"] == "Film A")
        assert "budget gap" in comp["relevanceDescription"].lower()


# ── Weather narratives ───────────────────────────────────────────────────────

class TestWeatherNarratives:
    def test_weather_from_ai(self):
        skel = _skeleton(territories=["UK"])
        ai = {"weatherNarratives": {"UK": {
            "infrastructure": "Excellent studios", "seasonalConsiderations": "Mild summers"
        }}}
        result = merge(skel, ai)
        w = result["weatherLogistics"][0]
        assert w["infrastructure"] == "Excellent studios"
        assert w["seasonalConsiderations"] == "Mild summers"

    def test_weather_defaults(self):
        skel = _skeleton(territories=["UK"])
        result = merge(skel, {})
        w = result["weatherLogistics"][0]
        assert w["infrastructure"] is not None
        assert w["seasonalConsiderations"] is not None


# ── Deep dive narratives ─────────────────────────────────────────────────────

class TestDeepDiveNarratives:
    def test_deep_dive_from_ai(self):
        skel = _skeleton(territories=["UK"])
        ai = {"deepDiveNarratives": {"UK": {
            "infrastructure": "World-class",
            "keyAdvantages": ["Pinewood"],
            "keyRisks_additional": ["Brexit uncertainty"],
            # AI sends these but they should be ignored — values are DB-computed
            "culturalTestLikelihood": "Low (35%)",
            "adminComplexity": "Low",
        }}}
        result = merge(skel, ai)
        dive = result["territoryDeepDives"][0]
        assert dive["infrastructure"] == "World-class"
        assert dive["keyAdvantages"] == ["Pinewood"]
        assert "Brexit uncertainty" in dive["keyRisks"]
        # DB-computed values must not be overwritten by AI
        assert dive["culturalTestLikelihood"] == "High (85%)"  # pre-set in skeleton
        assert dive["adminComplexity"] == "Medium"              # pre-set in skeleton

    def test_deep_dive_defaults(self):
        skel = _skeleton(territories=["UK"])
        result = merge(skel, {})
        dive = result["territoryDeepDives"][0]
        assert dive["infrastructure"] is not None
        assert isinstance(dive["keyAdvantages"], list)
        assert isinstance(dive["keyRisks"], list)
        # DB-computed fields: retained from skeleton (builder always pre-sets them)
        assert dive["culturalTestLikelihood"] == "High (85%)"
        assert dive["adminComplexity"] == "Medium"

    def test_deep_dive_preserves_db_fields(self):
        skel = _skeleton(territories=["UK"])
        ai = {"deepDiveNarratives": {"UK": {
            "infrastructure": "World-class",
        }}}
        result = merge(skel, ai)
        dive = result["territoryDeepDives"][0]
        # DB-populated fields preserved through merge
        assert dive["country"] == "UK"
        assert dive["rebate"] == "25% gross"
        assert dive["estimatedRebate"] == "£500,000"
        assert dive["paymentSpeed"] == "6-12 months"


# ── Full merge end-to-end ────────────────────────────────────────────────────

class TestFullMerge:
    def test_complete_merge_preserves_db_fields(self):
        skel = _skeleton(territories=["UK", "Canada"])
        original_strength = skel["locationRankings"][0]["incentiveStrength"]
        original_reliability = skel["locationRankings"][0]["incentiveReliability"]

        ai = {
            "genre": "Comedy",
            "tone": "Light",
            "scale": "Medium",
            "complexity": "Low",
            "locationNarratives": {
                "UK": {"costEfficiency": 80, "crewDepth": 75, "infrastructure": 85,
                        "reasoning": ["Great"], "keyAdvantages": ["Studios"]},
                "Canada": {"costEfficiency": 70, "crewDepth": 65, "infrastructure": 60,
                           "reasoning": ["Tax"], "keyAdvantages": ["PSTC"]},
            },
            "executiveSummary_keyInsights": "UK is recommended.",
        }
        result = merge(skel, ai)

        # DB fields untouched
        assert result["locationRankings"][0]["incentiveStrength"] == original_strength
        assert result["locationRankings"][0]["incentiveReliability"] == original_reliability
        # AI fields applied
        assert result["genre"] == "Comedy"
        assert result["locationRankings"][0]["costEfficiency"] == 80

    def test_skeleton_is_mutated_in_place(self):
        skel = _skeleton()
        result = merge(skel, {"genre": "Horror"})
        assert result is skel
        assert skel["genre"] == "Horror"

    def test_scoring_methodology_added(self):
        skel = _skeleton()
        result = merge(skel, {})
        assert result.get("scoringMethodology") is not None


# ── _fill_narrative_defaults ─────────────────────────────────────────────────

class TestFillNarrativeDefaults:
    def test_fills_top_level_fields(self):
        skel = _skeleton()
        defaults(skel)
        assert skel["genre"] == "Drama"
        assert skel["tone"] == "Unknown"
        assert skel["scale"] == "Unknown"
        assert skel["complexity"] == "Medium"
        assert skel["alternativeStrategy"] is not None

    def test_does_not_overwrite_existing(self):
        skel = _skeleton()
        skel["genre"] = "Comedy"
        defaults(skel)
        assert skel["genre"] == "Comedy"

    def test_fills_executive_summary(self):
        skel = _skeleton()
        defaults(skel)
        assert "unavailable" in skel["executiveSummary"]["keyInsights"].lower()

    def test_fills_location_dimensions(self):
        skel = _skeleton(territories=["UK"])
        defaults(skel)
        loc = skel["locationRankings"][0]
        assert loc["costEfficiency"] == 50
        assert loc["crewDepth"] == 50
        assert loc["infrastructure"] == 50
        assert len(loc["reasoning"]) >= 1

    def test_fills_crew_defaults(self):
        skel = _skeleton(territories=["UK"])
        defaults(skel)
        crew = skel["crewInsights"][0]
        assert crew["availability"] == "Medium"
        assert crew["specialties"] == []

    def test_fills_comparable_defaults(self):
        skel = _skeleton()
        defaults(skel)
        for comp in skel["comparables"]:
            assert comp.get("relevanceDescription") is not None

    def test_fills_weather_defaults(self):
        skel = _skeleton(territories=["UK"])
        defaults(skel)
        w = skel["weatherLogistics"][0]
        assert w["infrastructure"] is not None
        assert w["seasonalConsiderations"] is not None

    def test_fills_deep_dive_defaults(self):
        skel = _skeleton(territories=["UK"])
        defaults(skel)
        dive = skel["territoryDeepDives"][0]
        assert dive["infrastructure"] is not None
        assert isinstance(dive["keyAdvantages"], list)
        assert isinstance(dive["keyRisks"], list)
        # DB-computed fields: retained from skeleton (builder always pre-sets them)
        assert dive["culturalTestLikelihood"] == "High (85%)"
        assert dive["adminComplexity"] == "Medium"

    def test_scoring_methodology_added(self):
        skel = _skeleton()
        defaults(skel)
        assert skel.get("scoringMethodology") is not None
