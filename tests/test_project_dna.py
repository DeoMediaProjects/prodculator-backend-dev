from types import SimpleNamespace

from app.modules.reports.project_dna import build_project_dna


def test_missing_eligibility_facts_remain_unknown():
    dna = build_project_dna({}, {})
    for name in (
        "format",
        "premiere_history",
        "prior_screenings",
        "project_rights",
        "secured_finance",
        "applicant_residency",
        "footage_readiness",
        "target_sales_territories",
        "release_profile",
    ):
        fact = dna.get(name)
        assert fact.state == "UNKNOWN"
        assert fact.value is None
        assert fact.confirmation_required is True


def test_intake_and_script_facts_keep_distinct_provenance():
    analysis = SimpleNamespace(
        metadata=SimpleNamespace(format="short film", genres=["Drama"], tone="tense"),
        challenges=SimpleNamespace(languages=["French"]),
        locations=[SimpleNamespace(country="Kenya", territory="Nairobi")],
    )
    dna = build_project_dna(
        {
            "format": "feature film",
            "genre": ["Thriller"],
            "country": "United Kingdom",
            "primary_languages": ["English"],
        },
        {"_runtime_minutes": 92, "_budget_gbp": {"converted": 1250000}},
        analysis,
    )
    assert dna.value("format") == "feature"
    assert dna.get("format").source == "intake.format"
    assert dna.value("production_countries") == ["United Kingdom"]
    assert dna.value("story_countries") == ["Kenya"]
    assert dna.get("story_countries").confirmation_required is True
    assert dna.value("primary_languages") == ["English"]
    assert dna.value("script_dialogue_languages") == ["French"]
    assert dna.value("budget_gbp") == 1250000
    assert dna.value("runtime_minutes") == 92
    assert dna.value("genres") == ["Thriller"]
    assert dna.get("tone").source == "script_analysis.metadata.tone"
    assert dna.get("premiere_history").state == "UNKNOWN"


def test_commercial_preferences_are_known_only_when_declared():
    dna = build_project_dna(
        {"target_sales_territories": ["Kenya"], "release_profile": ["theatrical"]}, {}
    )
    assert dna.value("target_sales_territories") == ["Kenya"]
    assert dna.get("target_sales_territories").source == "intake.target_sales_territories"
    assert dna.value("release_profile") == ["theatrical"]


def test_invalid_values_do_not_become_known():
    dna = build_project_dna(
        {"genre": [], "runtime_minutes": "unknown", "country": "Undecided"},
        {"_budget_gbp": {"converted": 0}},
        SimpleNamespace(locations=[SimpleNamespace(country=None, territory=None)]),
    )
    for name in (
        "genres",
        "runtime_minutes",
        "budget_gbp",
        "story_countries",
        "production_countries",
    ):
        assert dna.get(name).state == "UNKNOWN"


def test_format_content_and_technique_are_not_conflated():
    animated = build_project_dna({"format": "Animated Feature"}, {})
    assert animated.value("format") == "feature"
    assert animated.value("technique") == "animation"
    assert animated.get("content_form").state == "UNKNOWN"

    documentary = build_project_dna({"format": "Documentary"}, {})
    assert documentary.get("format").state == "UNKNOWN"
    assert documentary.value("content_form") == "documentary"
    assert documentary.get("technique").state == "UNKNOWN"
