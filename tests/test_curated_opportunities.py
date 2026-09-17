"""Acceptance checks for the first officially sourced festival/market cycles."""

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from app.modules.reports.curated_opportunities import parse_curated_cycles
from app.modules.reports.opportunity_strategy import build_opportunity_strategy
from app.modules.reports.project_dna import build_project_dna

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "data/curated_opportunities/2026-09-17_initial_cycles.json"
)
TODAY = date(2026, 9, 17)


def _payload():
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def test_first_tranche_has_two_festival_sections_and_one_market_track():
    cycles = parse_curated_cycles(_payload(), today=TODAY)
    assert len(cycles) == 3
    assert len({cycle.id for cycle in cycles}) == 3
    assert {cycle.kind for cycle in cycles} == {"FESTIVAL", "MARKET_LAB_WIP"}
    assert all(cycle.cycle_verified and not cycle.rules_complete for cycle in cycles)
    assert all(cycle.source_url.startswith("https://") for cycle in cycles)


def test_known_runtime_failure_and_unknown_material_rules():
    cycles = parse_curated_cycles(_payload(), today=TODAY)
    sixty_minute_feature = build_project_dna({"format": "feature film"}, {"_runtime_minutes": 60})
    festivals = build_opportunity_strategy(
        cycles, sixty_minute_feature, kind="FESTIVAL", package="producer", today=TODAY
    )
    assert festivals.actionable_count == 2
    assert festivals.eligible_count == 0
    assert festivals.potential_count == 0
    assert festivals.recommendations == ()

    ninety_two_minute_feature = build_project_dna(
        {"format": "animated feature"}, {"_runtime_minutes": 92}
    )
    festivals = build_opportunity_strategy(
        cycles, ninety_two_minute_feature, kind="FESTIVAL", package="producer", today=TODAY
    )
    assert festivals.potential_count == 1
    recommendation = festivals.recommendations[0]
    assert recommendation.eligibility == "POTENTIALLY_ELIGIBLE"
    assert recommendation.application_status == "OPEN"
    assert any("50%" in condition for condition in recommendation.conditions_to_confirm)

    markets = build_opportunity_strategy(
        cycles, ninety_two_minute_feature, kind="MARKET_LAB_WIP", package="single", today=TODAY
    )
    assert markets.potential_count == 1
    assert any(
        "copyright owner" in condition
        for condition in markets.recommendations[0].conditions_to_confirm
    )
    assert ninety_two_minute_feature.get("secured_finance").state == "UNKNOWN"


def test_short_section_is_distinct_from_feature_section():
    cycles = parse_curated_cycles(_payload(), today=TODAY)
    short = build_project_dna({"format": "short film"}, {"_runtime_minutes": 18})
    strategy = build_opportunity_strategy(
        cycles, short, kind="FESTIVAL", package="single", today=TODAY
    )
    assert len(strategy.recommendations) == 1
    assert strategy.recommendations[0].opportunity.name.endswith("International Short Film")
    assert any(
        "professional work" in condition
        for condition in strategy.recommendations[0].conditions_to_confirm
    )


def test_curated_payload_rejects_unknown_fields_unsourced_rules_and_duplicate_cycles():
    payload = _payload()
    wrong_field = deepcopy(payload)
    wrong_field["cycles"][0]["gates"][0]["project_field"] = "made_up_fact"
    with pytest.raises(ValueError, match="unknown Project DNA field"):
        parse_curated_cycles(wrong_field, today=TODAY)

    no_source = deepcopy(payload)
    no_source["cycles"][0]["gates"][0]["source_url"] = ""
    with pytest.raises(ValueError, match="source URL"):
        parse_curated_cycles(no_source, today=TODAY)

    duplicate = deepcopy(payload)
    duplicate["cycles"].append(deepcopy(duplicate["cycles"][0]))
    with pytest.raises(ValueError, match="Duplicate curated cycle"):
        parse_curated_cycles(duplicate, today=TODAY)


def test_initial_source_check_cannot_masquerade_as_paid_cutover():
    payload = _payload()
    payload["review_status"] = "READY_FOR_PAID"
    with pytest.raises(ValueError, match="non-cutover review state"):
        parse_curated_cycles(payload, today=TODAY)
