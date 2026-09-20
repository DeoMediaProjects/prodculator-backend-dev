"""The worked sample, asserted against the regression it was built from.

A sample whose every fact is known proves the happy path and nothing else. This
one is the $30m production with every territory spend blank, so each behaviour
the rebuild added has to be visible in the output. These tests are where that
visibility is checked rather than assumed.
"""
from __future__ import annotations

from app.modules.reports.orchestration import (
    CONDITIONAL_STATUTORY_BENEFITS,
    DOCUMENTED_COMMITTED_FINANCE,
    SELECTIVE_PIPELINE_OPPORTUNITIES,
    STRATEGIC_ACCESS_OPPORTUNITIES,
)
from app.modules.reports.sample_orchestration import (
    SAMPLE_PROJECT_FACTS,
    SAMPLE_SNAPSHOT_ID,
    as_payload,
    build_sample_orchestration,
    sample_engine_results,
)


def _sections(result):
    return {section["section_key"]: section for section in result.sections}


def _blocks(result, section_key):
    return _sections(result)[section_key]["blocks"]


# ── It assembles at all ──────────────────────────────────────────────────────


def test_the_sample_assembles_thirteen_sections():
    result = build_sample_orchestration()
    assert len(result.sections) == 13
    assert result.qa["status"] == "PASS"


def test_every_engine_result_shares_the_one_snapshot():
    for engine in sample_engine_results():
        assert engine.projectfacts_snapshot_id == SAMPLE_SNAPSHOT_ID


def test_the_payload_matches_the_frozen_schema_shape():
    import json
    from pathlib import Path

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "handoff_contracts"
            / "report_orchestration_result_v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    payload = as_payload(build_sample_orchestration())
    assert [key for key in schema["required"] if key not in payload] == []


# ── Blank spend produces no rebate anywhere ──────────────────────────────────


def test_the_fixture_has_no_territory_spend():
    """The regression's own input. Absent, not zero."""
    spend = SAMPLE_PROJECT_FACTS["facts"]["expected_spend_by_territory"]
    assert spend["status"] == "UNKNOWN"
    assert all(value is None for value in spend["value"].values())


def test_no_incentive_programme_carries_an_amount():
    result = build_sample_orchestration()
    for block in _blocks(result, "tax_incentive_analysis"):
        for programme in block["data"]["recommendations"]:
            assert programme["amount"] is None, programme["programme"]


def test_every_programme_says_what_it_needs_instead_of_guessing():
    result = build_sample_orchestration()
    for block in _blocks(result, "tax_incentive_analysis"):
        for programme in block["data"]["recommendations"]:
            assert programme["needed_to_calculate"]
            assert programme["calculation_status"] == "REQUIRES_COST_BREAKDOWN"


def test_the_programmes_are_still_named_rather_than_hidden():
    """A producer needs to know the New York credit exists, just not a number."""
    result = build_sample_orchestration()
    named = {
        programme["territory"]
        for block in _blocks(result, "tax_incentive_analysis")
        for programme in block["data"]["recommendations"]
    }
    assert named == {"New York", "United Kingdom", "France"}


def test_no_section_anywhere_carries_a_rebate_figure():
    """The whole-report assertion, not just the incentive section's."""
    import json

    payload = json.dumps(as_payload(build_sample_orchestration()))
    # The regression's headline figures, in the forms they appeared in.
    for figure in ("5,740,000", "£5.74M", "7,650,000", "30,000,000"):
        assert figure not in payload, figure


# ── The finance market is routed out of Grants ───────────────────────────────


def test_the_finance_market_does_not_appear_under_grants():
    result = build_sample_orchestration()
    names = {
        record.get("name")
        for block in _blocks(result, "grant_funding_opportunities")
        for record in block["data"]["recommendations"]
    }
    assert "Film London Production Finance Market" not in names


def test_the_finance_market_appears_under_markets_instead():
    result = build_sample_orchestration()
    names = {
        record.get("name")
        for block in _blocks(result, "industry_development_market_strategy")
        for record in block["data"]["recommendations"]
    }
    assert "Film London Production Finance Market" in names


def test_the_suppression_is_explained_in_the_result():
    result = build_sample_orchestration()
    conflicts = result.cross_engine_conflicts
    assert len(conflicts) == 1
    assert conflicts[0]["routed_to"] == "markets_labs_wip"
    assert conflicts[0]["suppressed_from"] == ["grants"]


def test_the_genuine_grant_survives_the_routing():
    """Routing removes the misrouted record, not the section's real contents."""
    result = build_sample_orchestration()
    names = {
        record.get("name")
        for block in _blocks(result, "grant_funding_opportunities")
        for record in block["data"]["recommendations"]
    }
    assert "A national production fund" in names


# ── Nothing becomes committed finance ────────────────────────────────────────


def test_committed_finance_is_empty():
    """Matched is not awarded. This production has secured nothing."""
    result = build_sample_orchestration()
    assert result.financial_readiness[DOCUMENTED_COMMITTED_FINANCE] == []


def test_each_opportunity_lands_in_its_own_bucket():
    readiness = build_sample_orchestration().financial_readiness
    assert len(readiness[CONDITIONAL_STATUTORY_BENEFITS]) == 1
    assert len(readiness[SELECTIVE_PIPELINE_OPPORTUNITIES]) == 1
    assert len(readiness[STRATEGIC_ACCESS_OPPORTUNITIES]) == 3


# ── Sales carries access routes and no buyer intent ──────────────────────────


def test_every_sales_company_states_its_access_route():
    result = build_sample_orchestration()
    for block in _blocks(result, "sales_distribution_strategy"):
        for company in block["data"]["recommendations"]:
            assert company["access_route"]
            assert company["access_note"]


def test_a_published_contact_is_not_described_as_an_open_route():
    result = build_sample_orchestration()
    published = [
        company
        for block in _blocks(result, "sales_distribution_strategy")
        for company in block["data"]["recommendations"]
        if company["access_route"] == "CONTACT_PUBLISHED"
    ]
    assert published
    for company in published:
        assert "not confirmation" in company["access_note"]


def test_no_company_is_described_in_acquisition_language():
    """The vocabulary the implementation note forbids, checked whole-payload."""
    import json

    payload = json.dumps(as_payload(build_sample_orchestration())).lower()
    for phrase in (
        "will buy",
        "likely to acquire",
        "interested buyer",
        "actively scouting",
    ):
        assert phrase not in payload, phrase


# ── Unknowns survive to the reader ───────────────────────────────────────────


def test_unknown_premiere_history_reaches_the_festival_section_as_a_condition():
    result = build_sample_orchestration()
    conditions = [
        condition
        for block in _blocks(result, "festival_strategy")
        for festival in block["data"]["recommendations"]
        for condition in festival["conditions_to_confirm"]
    ]
    assert any("Premiere history" in condition for condition in conditions)


def test_the_required_unknown_facts_are_listed_on_the_snapshot():
    assert set(SAMPLE_PROJECT_FACTS["unknown_required_facts"]) == {
        "expected_spend_by_territory",
        "premiere_history",
        "rights_status",
        "secured_finance",
    }


def test_next_steps_name_the_missing_spend_first():
    result = build_sample_orchestration()
    actions = [step["action"] for step in result.next_steps]
    assert any("qualifying spend" in action for action in actions)


# ── Package depth ────────────────────────────────────────────────────────────


def test_the_sample_shows_counts_rather_than_implying_a_small_universe():
    result = build_sample_orchestration()
    block = _blocks(result, "festival_strategy")[0]["data"]
    assert block["eligible_universe_count"] == 41
    assert block["displayed_count"] <= block["package_entitlement"]


def test_a_smaller_package_shows_less_of_the_same_ranking():
    producer = build_sample_orchestration("producer")
    professional = build_sample_orchestration("professional")
    big = _blocks(producer, "sales_distribution_strategy")[0]["data"]
    small = _blocks(professional, "sales_distribution_strategy")[0]["data"]
    assert small["recommendations"] == big["recommendations"][: len(small["recommendations"])]
    assert small["eligible_universe_count"] == big["eligible_universe_count"]
