"""The 13-section orchestrator: assembles, never decides.

The regression these guard is a report whose sections disagreed because each had
reasoned independently. So most of what is tested here is restraint — the
orchestrator refusing to merge two runs' facts, refusing to let a finance market
appear under Grants, refusing to let a matched opportunity become committed
finance, and refusing to let a package's display depth look like a search depth.
"""
from __future__ import annotations

import pytest

from app.modules.reports.orchestration import (
    ACTIONABLE_CONFIRMED,
    ACTIONABLE_WITH_CONDITIONS,
    CONDITIONAL_STATUTORY_BENEFITS,
    DOCUMENTED_COMMITTED_FINANCE,
    FINANCE_BUCKETS,
    INELIGIBLE,
    SECTIONS,
    SELECTIVE_PIPELINE_OPPORTUNITIES,
    STRATEGIC_ACCESS_OPPORTUNITIES,
    UMBRELLA_STATES,
    UNKNOWN_OR_NEEDS_CONFIRMATION,
    WATCH_UPCOMING,
    EngineResult,
    InconsistentInputVersion,
    assemble,
    finance_bucket,
    package_entitlement,
    resolve_routing,
    umbrella_state,
)

SNAPSHOT = "snapshot-a"


def _result(engine="grants", *, snapshot=SNAPSHOT, version="1", **overrides):
    base = {
        "engine_name": engine,
        "engine_version": "2.0",
        "projectfacts_snapshot_id": snapshot,
        "projectfacts_version": version,
        "eligible_universe_count": 23,
        "recommendations": tuple(f"rec-{i}" for i in range(23)),
    }
    base.update(overrides)
    return EngineResult(**base)


def _assemble(results, package="professional", finance_items=()):
    return assemble(
        report_run_id="run-1",
        projectfacts_snapshot_id=SNAPSHOT,
        projectfacts_version="1",
        engine_results=results,
        package=package,
        finance_items=finance_items,
        generated_at="2026-09-18T00:00:00+00:00",
    )


# ── The snapshot rule ────────────────────────────────────────────────────────


def test_a_result_from_another_snapshot_refuses_to_assemble():
    """Two runs' facts in one report reads plausibly and only the numbers lie."""
    with pytest.raises(InconsistentInputVersion, match="INCONSISTENT_INPUT_VERSION"):
        _assemble([_result(snapshot="snapshot-b")])


def test_a_result_from_another_snapshot_version_refuses_too():
    with pytest.raises(InconsistentInputVersion):
        _assemble([_result(version="2")])


def test_one_snapshot_across_every_engine_assembles():
    out = _assemble([_result("grants"), _result("festivals"), _result("sales")])
    assert out.qa["status"] == "PASS"
    assert any(
        check["check"] == "single_projectfacts_snapshot" and check["status"] == "PASS"
        for check in out.qa["checks"]
    )


# ── Section layout ───────────────────────────────────────────────────────────


def test_there_are_exactly_thirteen_sections():
    assert len(SECTIONS) == 13
    assert [number for number, *_ in SECTIONS] == list(range(1, 14))
    assert len(_assemble([_result()]).sections) == 13


def test_every_section_declares_at_least_one_source():
    """The frozen schema requires it, including for orchestrator-only sections."""
    for section in _assemble([_result()]).sections:
        assert section["source_engines"]


def test_grants_section_consumes_only_the_grants_engine():
    """Section 08 naming one engine is what keeps a finance market out of it."""
    section = next(s for s in SECTIONS if s[0] == 8)
    assert section[3] == ("grants",)


def test_markets_have_their_own_section_and_do_not_share_with_grants():
    markets = next(s for s in SECTIONS if s[0] == 9)
    assert markets[3] == ("markets_labs_wip",)


def test_sales_section_consumes_only_the_sales_strategy():
    section = next(s for s in SECTIONS if s[0] == 12)
    assert section[3] == ("sales",)


def test_executive_summary_and_next_steps_own_no_calculation():
    sections = {s["section_key"]: s for s in _assemble([_result()]).sections}
    assert sections["executive_summary"]["owns_no_calculation"]
    assert sections["next_steps"]["owns_no_calculation"]
    assert not sections["grant_funding_opportunities"]["owns_no_calculation"]


def test_a_section_whose_engine_did_not_run_carries_no_blocks():
    sections = {s["section_key"]: s for s in _assemble([_result("grants")]).sections}
    assert sections["grant_funding_opportunities"]["blocks"]
    assert sections["festival_strategy"]["blocks"] == []


# ── Package entitlement is display depth, never search depth ─────────────────


def test_entitlement_is_five_or_ten_by_package():
    assert package_entitlement("free") == 5
    assert package_entitlement("professional") == 5
    assert package_entitlement("producer") == 10
    assert package_entitlement("studio") == 10


def test_both_counts_travel_so_shown_is_never_read_as_searched():
    out = _assemble([_result(eligible_universe_count=23)])
    block = out.sections[7]["blocks"][0]["data"]
    assert block["displayed_count"] == 5
    assert block["eligible_universe_count"] == 23


def test_five_and_ten_are_the_same_list_truncated():
    """A cheaper package gets less advice, never different advice."""
    five = _assemble([_result()], package="professional").sections[7]["blocks"][0]
    ten = _assemble([_result()], package="studio").sections[7]["blocks"][0]
    assert five["data"]["recommendations"] == ten["data"]["recommendations"][:5]


def test_the_universe_count_does_not_shrink_with_the_package():
    five = _assemble([_result()], package="professional").sections[7]["blocks"][0]
    ten = _assemble([_result()], package="studio").sections[7]["blocks"][0]
    assert (
        five["data"]["eligible_universe_count"]
        == ten["data"]["eligible_universe_count"]
        == 23
    )


# ── Routing conflicts ────────────────────────────────────────────────────────


def test_a_finance_market_claimed_by_grants_is_routed_to_markets():
    """Film London Production Finance Market, the regression's own example."""
    filtered, conflicts = resolve_routing(
        {
            "grants": [{"id": "flpfm", "name": "Film London Production Finance Market"}],
            "markets_labs_wip": [
                {"id": "flpfm", "name": "Film London Production Finance Market"}
            ],
        }
    )
    assert filtered["grants"] == []
    assert len(filtered["markets_labs_wip"]) == 1
    assert conflicts[0].routed_to == "markets_labs_wip"
    assert conflicts[0].suppressed_from == ("grants",)


def test_a_suppression_is_explained_rather_than_silent():
    _, conflicts = resolve_routing(
        {
            "grants": [{"id": "x", "name": "Something"}],
            "markets_labs_wip": [{"id": "x", "name": "Something"}],
        }
    )
    assert "Routing wins over presence" in conflicts[0].rule
    assert conflicts[0].claimed_by == ("grants", "markets_labs_wip")


def test_an_incentive_outranks_every_other_claim():
    filtered, conflicts = resolve_routing(
        {
            "grants": [{"id": "s481", "name": "Section 481"}],
            "incentive": [{"id": "s481", "name": "Section 481"}],
        }
    )
    assert conflicts[0].routed_to == "incentive"
    assert filtered["grants"] == []


def test_a_record_claimed_once_is_not_a_conflict():
    filtered, conflicts = resolve_routing(
        {"grants": [{"id": "a", "name": "A"}], "festivals": [{"id": "b", "name": "B"}]}
    )
    assert conflicts == []
    assert len(filtered["grants"]) == 1


def test_a_record_with_no_id_cannot_be_routed_and_is_left_alone():
    filtered, conflicts = resolve_routing({"grants": [{"name": "Unnamed"}]})
    assert conflicts == []
    assert len(filtered["grants"]) == 1


# ── Finance buckets ──────────────────────────────────────────────────────────


def test_there_are_four_buckets():
    assert len(FINANCE_BUCKETS) == 4


def test_an_incentive_is_conditional_not_committed():
    assert finance_bucket("incentive") == CONDITIONAL_STATUTORY_BENEFITS


def test_a_matched_grant_is_pipeline_not_committed():
    assert finance_bucket("grant_match") == SELECTIVE_PIPELINE_OPPORTUNITIES


def test_a_market_or_festival_or_sales_route_is_access_not_money():
    for kind in (
        "market_opportunity",
        "lab_opportunity",
        "festival_selection",
        "sales_route",
        "distribution_route",
    ):
        assert finance_bucket(kind) == STRATEGIC_ACCESS_OPPORTUNITIES, kind


def test_only_documented_award_evidence_reaches_committed_finance():
    assert (
        finance_bucket("grant_match", has_documented_award=True)
        == DOCUMENTED_COMMITTED_FINANCE
    )


def test_documentation_does_not_turn_a_festival_selection_into_money():
    """However well evidenced, a selection is not cash."""
    assert (
        finance_bucket("festival_selection", has_documented_award=True)
        == STRATEGIC_ACCESS_OPPORTUNITIES
    )


def test_an_unknown_kind_falls_to_the_least_committal_bucket():
    assert finance_bucket("something_new") == STRATEGIC_ACCESS_OPPORTUNITIES


def test_assembly_sorts_finance_items_and_checks_the_committed_bucket():
    out = _assemble(
        [_result()],
        finance_items=[
            {"kind": "incentive", "label": "AVEC"},
            {"kind": "grant_match", "label": "BFI"},
            {"kind": "festival_selection", "label": "Sundance"},
            {"kind": "grant_match", "label": "Awarded", "has_documented_award": True},
        ],
    )
    readiness = out.financial_readiness
    assert len(readiness[DOCUMENTED_COMMITTED_FINANCE]) == 1
    assert len(readiness[CONDITIONAL_STATUTORY_BENEFITS]) == 1
    assert len(readiness[SELECTIVE_PIPELINE_OPPORTUNITIES]) == 1
    assert len(readiness[STRATEGIC_ACCESS_OPPORTUNITIES]) == 1
    assert out.qa["status"] == "PASS"


# ── Umbrella states ──────────────────────────────────────────────────────────


def test_each_engine_vocabulary_maps_to_a_report_safe_state():
    assert umbrella_state("ELIGIBLE_CONFIRMED") == ACTIONABLE_CONFIRMED
    assert umbrella_state("POTENTIALLY_ELIGIBLE") == ACTIONABLE_WITH_CONDITIONS
    assert umbrella_state("INELIGIBLE_CONFIRMED") == INELIGIBLE
    assert umbrella_state("UPCOMING") == WATCH_UPCOMING
    assert umbrella_state("STRATEGIC_MATCH") == ACTIONABLE_CONFIRMED
    assert umbrella_state("NOT_SUITABLE") == INELIGIBLE


def test_an_unmapped_state_is_never_actionable():
    """A state nobody mapped is a state nobody has reasoned about."""
    assert umbrella_state("SOMETHING_NEW") == UNKNOWN_OR_NEEDS_CONFIRMATION
    assert umbrella_state(None) == UNKNOWN_OR_NEEDS_CONFIRMATION
    assert umbrella_state("") == UNKNOWN_OR_NEEDS_CONFIRMATION


def test_every_mapped_state_is_one_of_the_six():
    for engine_state in ("ELIGIBLE_CONFIRMED", "UPCOMING", "eligible", "NOT_A_FIT"):
        assert umbrella_state(engine_state) in UMBRELLA_STATES


def test_an_access_route_unknown_does_not_read_as_actionable():
    assert umbrella_state("ACCESS_ROUTE_UNKNOWN") == UNKNOWN_OR_NEEDS_CONFIRMATION


# ── The run ──────────────────────────────────────────────────────────────────


def test_engine_versions_are_recorded_per_engine():
    out = _assemble([_result("grants"), _result("festivals")])
    assert set(out.engine_versions) == {"grants", "festivals"}


def test_next_steps_are_collected_and_attributed():
    out = _assemble(
        [
            _result("grants", next_steps=({"action": "Apply to BFI"},)),
            _result("festivals", next_steps=({"action": "Hold for premiere"},)),
        ]
    )
    assert {step["engine"] for step in out.next_steps} == {"grants", "festivals"}


def test_reason_codes_are_deduplicated_per_section():
    out = _assemble([_result("grants", reason_codes=("A", "B", "A"))])
    assert out.sections[7]["engine_reason_codes"] == ["A", "B"]


# ── Conformance to the frozen schemas ────────────────────────────────────────
#
# Checked against the vendored schema files rather than a restatement of them,
# so a contract change shows up as a failing test rather than as drift nobody
# noticed. Validated structurally, without a jsonschema dependency: the parts
# that matter here are the required keys, the enums and the section count.


def _schema(name: str) -> dict:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1] / "data" / "handoff_contracts" / name
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _as_payload(out) -> dict:
    return {
        "report_run_id": out.report_run_id,
        "projectfacts_snapshot_id": out.projectfacts_snapshot_id,
        "projectfacts_version": out.projectfacts_version,
        "generated_at": out.generated_at,
        "engine_versions": out.engine_versions,
        "sections": out.sections,
        "financial_readiness": out.financial_readiness,
        "cross_engine_conflicts": out.cross_engine_conflicts,
        "next_steps": out.next_steps,
        "qa": out.qa,
    }


def test_the_result_carries_every_required_top_level_field():
    schema = _schema("report_orchestration_result_v1.schema.json")
    payload = _as_payload(_assemble([_result()]))
    missing = [key for key in schema["required"] if key not in payload]
    assert missing == []


def test_the_result_holds_exactly_the_schema_s_section_count():
    schema = _schema("report_orchestration_result_v1.schema.json")
    spec = schema["properties"]["sections"]
    payload = _as_payload(_assemble([_result()]))
    assert spec["minItems"] <= len(payload["sections"]) <= spec["maxItems"]


def test_the_projectfacts_version_matches_the_schema_constant():
    schema = _schema("report_orchestration_result_v1.schema.json")
    const = schema["properties"]["projectfacts_version"]["const"]
    assert _assemble([_result()]).projectfacts_version == const


def test_financial_readiness_carries_every_required_bucket():
    schema = _schema("report_orchestration_result_v1.schema.json")
    required = schema["properties"]["financial_readiness"]["required"]
    readiness = _assemble([_result()]).financial_readiness
    assert sorted(readiness) == sorted(required)


def test_the_qa_status_is_one_the_schema_permits():
    schema = _schema("report_orchestration_result_v1.schema.json")
    allowed = schema["properties"]["qa"]["properties"]["status"]["enum"]
    out = _assemble([_result()])
    assert out.qa["status"] in allowed
    assert set(schema["properties"]["qa"]["required"]) <= set(out.qa)


def test_every_section_carries_the_required_payload_fields():
    schema = _schema("report_section_payload_v1.schema.json")
    required = schema["required"]
    for section in _assemble([_result()]).sections:
        missing = [key for key in required if key not in section]
        assert missing == [], f"{section['section_key']} is missing {missing}"


def test_section_numbers_stay_inside_the_schema_s_range():
    schema = _schema("report_section_payload_v1.schema.json")
    spec = schema["properties"]["section_number"]
    for section in _assemble([_result()]).sections:
        assert spec["minimum"] <= section["section_number"] <= spec["maximum"]


def test_every_block_carries_the_required_block_fields():
    schema = _schema("report_section_payload_v1.schema.json")
    required = schema["properties"]["blocks"]["items"]["required"]
    for section in _assemble([_result()]).sections:
        for block in section["blocks"]:
            assert all(key in block for key in required)


def test_source_engines_meets_the_schema_s_minimum():
    schema = _schema("report_section_payload_v1.schema.json")
    minimum = schema["properties"]["source_engines"]["minItems"]
    for section in _assemble([_result()]).sections:
        assert len(section["source_engines"]) >= minimum
