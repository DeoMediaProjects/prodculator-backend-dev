from app.modules.reports.project_dna import build_project_dna
from app.modules.reports.project_facts_v1 import build_project_facts_snapshot
from types import SimpleNamespace


def test_blank_spend_and_unknown_eligibility_are_not_zero_or_no():
    metadata = {
        "script_title": "Regression project",
        "format": "Feature Film",
        "genre": ["Comedy", "Drama"],
        "budget_amount": 30_000_000,
        "budget_currency": "USD",
        "country": "United States",
        "territories_considering": ["United Kingdom", "France"],
        "territory_scenarios": [
            {"territory": "United Kingdom", "scenario_spend": None},
            {"territory": "France", "scenario_spend": 0},
        ],
    }
    snapshot = build_project_facts_snapshot(metadata, build_project_dna(metadata, {}))
    payload = snapshot.as_dict()

    spend = payload["facts"]["expected_spend_by_territory"]["value"]
    assert spend == {"United Kingdom": None, "France": 0}
    assert payload["facts"]["total_budget"]["source"] == "USER"
    assert payload["facts"]["premiere_history"]["status"] == "UNKNOWN"
    assert payload["facts"]["secured_finance"]["status"] == "UNKNOWN"


def test_snapshot_identity_is_stable_but_changes_with_objective_input():
    base = {"format": "Feature Film", "budget_amount": 1_000_000, "genre": ["Drama"]}
    first = build_project_facts_snapshot(base, build_project_dna(base, {}))
    again = build_project_facts_snapshot(dict(base), build_project_dna(base, {}))
    changed = {**base, "budget_amount": 2_000_000}
    second = build_project_facts_snapshot(changed, build_project_dna(changed, {}))

    assert first.snapshot_id == again.snapshot_id
    assert first.snapshot_id != second.snapshot_id
    assert first.version == "1"


def test_all_blank_territory_spend_is_unknown():
    metadata = {
        "territories_considering": ["United Kingdom", "France"],
        "territory_scenarios": [],
    }
    snapshot = build_project_facts_snapshot(metadata, build_project_dna(metadata, {}))
    spend = snapshot.as_dict()["facts"]["expected_spend_by_territory"]
    assert spend["status"] == "UNKNOWN"
    assert spend["value"] == {"United Kingdom": None, "France": None}


def test_declared_and_script_genres_keep_separate_provenance():
    metadata = {"genre": ["Drama"]}
    analysis = SimpleNamespace(metadata=SimpleNamespace(genres=["Comedy"]))
    dna = build_project_dna(metadata, {}, analysis)
    snapshot = build_project_facts_snapshot(metadata, dna).as_dict()
    assert snapshot["facts"]["declared_genres"]["value"] == ["Drama"]
    assert snapshot["script_analysis"]["script_genres"]["value"] == ["Comedy"]
    assert snapshot["script_analysis"]["script_genres"]["source"] == "SCRIPT"
