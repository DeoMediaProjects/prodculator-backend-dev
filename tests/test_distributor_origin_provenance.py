"""A screenplay setting must never become a distributor production-market claim."""

from types import SimpleNamespace

from app.modules.reports.builder import ReportBuilder
from app.modules.reports.project_dna import build_project_dna


def _builder(request_metadata):
    analysis = SimpleNamespace(
        locations=[SimpleNamespace(country="Kenya", territory="Kenya")]
    )
    builder = ReportBuilder.__new__(ReportBuilder)
    builder.request_metadata = request_metadata
    builder.script_analysis = analysis
    builder.project_dna = build_project_dna(request_metadata, {}, analysis)
    builder._territory_names = ["Kenya"]
    builder._production_format = "feature"
    builder.datasets = {
        "distributors": [{
            "name": "Example buyer",
            "specialty_genres": ["drama"],
            "territory_reach": ["Kenya"],
            "format_focus": ["feature"],
        }]
    }
    return builder


def test_story_and_filming_location_do_not_claim_production_market_fit():
    builder = _builder({"genre": ["Drama"]})
    entries = builder._build_distributor_recommendations([])
    assert len(entries) == 1
    assert entries[0]["matchScore"] == 2.0
    assert not any("production market" in reason for reason in entries[0]["matchedOn"])


def test_declared_production_country_can_support_market_fit():
    builder = _builder({"genre": ["Drama"], "production_country": "Kenya"})
    entries = builder._build_distributor_recommendations([])
    assert entries[0]["matchScore"] == 5.0
    assert any("production market" in reason for reason in entries[0]["matchedOn"])
