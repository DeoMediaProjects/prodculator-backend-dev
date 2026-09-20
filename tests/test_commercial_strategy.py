"""Commercial matching: hard gates first, then the frozen 100-point score.

This module used to carry its own scorer — a hand-tuned tally of two points for
genre, three per comparable, capped at nine — alongside its own status
vocabulary. Both have been replaced by the frozen contract, so what these tests
now guard is that there is exactly one of each: one scale out of 100, and one set
of canonical match states shared with every other section of the report.
"""

from dataclasses import replace
from datetime import date

from app.modules.reports.commercial_freeze import (
    ACCESS_CONTACT_PUBLISHED,
    ACCESS_DIRECT,
    ACCESS_NO_UNSOLICITED,
    ACCESS_UNKNOWN,
)
from app.modules.reports.commercial_scoring import (
    ACCESS_ROUTE_UNKNOWN,
    MAX_SCORE,
    NOT_SUITABLE,
    POTENTIAL_MATCH_NEEDS_CONFIRMATION,
    STRATEGIC_MATCH,
)
from app.modules.reports.commercial_strategy import (
    CompanyProfile,
    ComparableProfile,
    ComparableRelationship,
    SourcedValue,
    build_sales_distribution_strategy,
    match_comparables,
    match_company,
)
from app.modules.reports.project_dna import build_project_dna

TODAY = date(2026, 9, 17)
SOURCE = "https://example.org/official-catalogue"


def claim(value, *, source=SOURCE, verified_on=TODAY):
    return SourcedValue(value, source, verified_on)


def project(**metadata):
    return build_project_dna({"format": "feature film", "genre": ["Drama"], **metadata}, {})


def company(name="Buyer", **fields):
    defaults = dict(
        id=name,
        name=name,
        role=claim("distributor"),
        active=claim(True),
        formats=claim(["feature"]),
        genres=claim(["drama"]),
    )
    defaults.update(fields)
    return CompanyProfile(**defaults)


def comparable(title="A Real Film", **fields):
    defaults = dict(
        id=title,
        title=title,
        source_url=SOURCE,
        verified_on=TODAY,
        format=claim("feature"),
        genres=claim(["drama"]),
    )
    defaults.update(fields)
    return ComparableProfile(**defaults)


def fully_sourced(name="Complete", **fields):
    """A company with every scored dimension established and a way in.

    Assembled once because reaching STRATEGIC_MATCH deliberately requires all
    eight components: it is the report's strongest claim about a company, and a
    fixture that reached it by accident would not be testing much.
    """
    defaults = dict(
        acquisition_stages=claim(["development"]),
        rights_territories=claim(["United Kingdom"]),
        rules_complete=True,
        access_route=ACCESS_DIRECT,
    )
    defaults.update(fields)
    return company(name, **defaults)


def complete_project(**metadata):
    return project(
        project_stage="development",
        target_sales_territories=["United Kingdom"],
        **metadata,
    )


def sourced_history(company_id="Complete", *, titles=3, festival="f1"):
    """A comparable layer giving one company a full verified evidence base.

    Three linked titles is what the contract treats as complete, so this is what
    it takes to reach 100: the remaining 45 points are comparable history, slate
    similarity and a festival intersection, and none of them can be reached
    without sourced, typed relationships.
    """
    films = []
    for index in range(titles):
        relationships = [
            ComparableRelationship("COMPANY", company_id, "distribution", SOURCE, TODAY)
        ]
        if index == 0 and festival:
            relationships.append(
                ComparableRelationship("FESTIVAL", festival, "screened", SOURCE, TODAY)
            )
        films.append(comparable(f"Film {index}", relationships=tuple(relationships)))
    return match_comparables(
        films, complete_project(), today=TODAY, festival_ids={festival} if festival else set()
    )


# ── Comparables are unchanged ────────────────────────────────────────────────


def test_comparable_needs_two_sourced_similarity_reasons_and_excludes_wrong_format():
    dna = project()
    strategy = match_comparables(
        [
            comparable("Match"),
            comparable("Wrong format", format=claim("short")),
            comparable("One reason", genres=None),
            comparable("No source", source_url="http://example.org/title"),
        ],
        dna,
        today=TODAY,
    )
    assert strategy.universe_count == 4
    assert [item.profile.title for item in strategy.recommendations] == ["Match"]
    assert strategy.recommendations[0].reasons == (
        "Shared format: feature", "Shared genre: drama"
    )


def test_comparable_relationships_require_verified_sources():
    dna = project()
    film = comparable(
        relationships=(
            ComparableRelationship("COMPANY", "Buyer", "distribution", SOURCE, TODAY),
            ComparableRelationship("FESTIVAL", "f1", "screened", SOURCE, TODAY),
            ComparableRelationship("MARKET_LAB_WIP", "m1", "participated", "", TODAY),
        ),
    )
    strategy = match_comparables(
        [film], dna, today=TODAY, festival_ids={"f1"}, market_ids={"m1"}
    )
    item = strategy.recommendations[0]
    assert item.score == 8
    assert len(item.verified_relationships) == 2
    assert not any("market pathway" in reason for reason in item.reasons)


# ── One scale, out of 100 ────────────────────────────────────────────────────


def test_a_company_is_scored_out_of_one_hundred():
    result = match_company(
        fully_sourced(),
        complete_project(),
        today=TODAY,
        comparables=sourced_history(),
        festival_ids={"f1"},
    )
    assert result.score == MAX_SCORE
    assert result.fit.components_known == 8


def test_the_score_property_and_the_fit_total_are_the_same_number():
    """There is one scale. A reader of either cannot see a different figure."""
    result = match_company(company(), project(), today=TODAY)
    assert result.score == result.fit.score


def test_an_unknown_component_costs_points_rather_than_being_averaged_away():
    sparse = match_company(company(), project(), today=TODAY)
    complete = match_company(
        fully_sourced(),
        complete_project(),
        today=TODAY,
        comparables=sourced_history(),
        festival_ids={"f1"},
    )
    assert sparse.score < complete.score
    assert sparse.fit.components_known < complete.fit.components_known
    assert sparse.fit.unknown_components


def test_verified_comparable_history_is_the_heaviest_single_component():
    dna = project()
    film = comparable(
        relationships=(
            ComparableRelationship("COMPANY", "Buyer", "distribution", SOURCE, TODAY),
        ),
    )
    comps = match_comparables([film], dna, today=TODAY)
    with_history = match_company(company(), dna, today=TODAY, comparables=comps)
    without = match_company(company(), dna, today=TODAY, comparables=comps.__class__(1, ()))
    assert with_history.score > without.score
    assert any("A Real Film" in reason for reason in with_history.reasons)


def test_no_comparable_layer_leaves_that_evidence_unknown_not_zero():
    """'We did not look' is not the same claim as 'no history found'."""
    result = match_company(company(), project(), today=TODAY, comparables=None)
    assert "commercial_comparable_evidence" in result.fit.unknown_components


# ── Hard gates still come first ──────────────────────────────────────────────


def test_known_company_hard_failure_beats_comparable_signal():
    dna = project(format="short film")
    film = comparable("Short", format=claim("short"), genres=claim(["drama"]))
    linked = replace(
        film,
        relationships=(
            ComparableRelationship("COMPANY", "Buyer", "distribution", SOURCE, TODAY),
        ),
    )
    comps = match_comparables([linked], dna, today=TODAY)
    buyer = match_company(company(), dna, today=TODAY, comparables=comps)
    assert buyer.status == NOT_SUITABLE
    assert ("acquisition format", "FAIL") in buyer.gate_results


def test_unknown_rights_territory_is_not_a_pass_or_fail():
    dna = project()
    buyer = company(rights_territories=claim(["United Kingdom"]))
    result = match_company(buyer, dna, today=TODAY)
    assert ("rights territory", "UNKNOWN") in result.gate_results
    assert "rights territory" in result.conditions_to_confirm
    declared = project(target_sales_territories=["United States"])
    assert match_company(buyer, declared, today=TODAY).status == NOT_SUITABLE


def test_unsourced_inactive_or_bare_name_is_not_suitable():
    dna = project()
    assert match_company(company(active=claim(False)), dna, today=TODAY).status == NOT_SUITABLE
    assert (
        match_company(company(role=claim("distributor", source="")), dna, today=TODAY).status
        == NOT_SUITABLE
    )
    assert (
        match_company(company(formats=None, genres=None), dna, today=TODAY).status
        == NOT_SUITABLE
    )


def test_sourced_global_scope_does_not_exclude_known_territory():
    dna = project(target_sales_territories=["Kenya"])
    buyer = company(rights_territories=claim(["worldwide"]))
    result = match_company(buyer, dna, today=TODAY)
    assert ("rights territory", "PASS") in result.gate_results


def test_a_worldwide_scope_scores_as_a_full_territory_fit():
    """The broadest company in a catalogue is not penalised for being broad."""
    dna = project(target_sales_territories=["Kenya"])
    worldwide = match_company(
        company(rights_territories=claim(["worldwide"])), dna, today=TODAY
    )
    elsewhere = match_company(
        company(rights_territories=claim(["Norway"])), dna, today=TODAY
    )
    assert worldwide.score > elsewhere.score


# ── Canonical match states ───────────────────────────────────────────────────


def test_a_company_with_no_established_route_reports_as_access_unknown():
    """Its own state: the producer researches a route, not the company."""
    result = match_company(fully_sourced(access_route=ACCESS_UNKNOWN), complete_project(), today=TODAY)
    assert result.status == ACCESS_ROUTE_UNKNOWN


def test_a_stated_refusal_also_reports_as_an_access_state():
    result = match_company(
        fully_sourced(access_route=ACCESS_NO_UNSOLICITED), complete_project(), today=TODAY
    )
    assert result.status == ACCESS_ROUTE_UNKNOWN


def test_an_outstanding_condition_downgrades_a_strategic_match():
    result = match_company(
        fully_sourced(rules_complete=False), complete_project(), today=TODAY
    )
    assert result.status == POTENTIAL_MATCH_NEEDS_CONFIRMATION
    assert "Material acquisition rules require source review" in result.conditions_to_confirm


def test_a_fully_sourced_company_with_a_route_is_a_strategic_match():
    result = match_company(
        fully_sourced(),
        complete_project(),
        today=TODAY,
        comparables=sourced_history(),
        festival_ids={"f1"},
    )
    assert result.status == STRATEGIC_MATCH


def test_incomplete_rules_cannot_be_confirmed_even_when_known_gates_pass():
    assert (
        match_company(company(rules_complete=True), project(), today=TODAY).status
        != STRATEGIC_MATCH
    )


def test_no_status_implies_an_acquisition():
    """The vocabulary the implementation note permits, and nothing else."""
    for result in (
        match_company(fully_sourced(), complete_project(), today=TODAY),
        match_company(company(), project(), today=TODAY),
        match_company(company(active=claim(False)), project(), today=TODAY),
    ):
        assert result.status in {
            STRATEGIC_MATCH,
            POTENTIAL_MATCH_NEEDS_CONFIRMATION,
            ACCESS_ROUTE_UNKNOWN,
            NOT_SUITABLE,
        }


# ── Portfolio selection ──────────────────────────────────────────────────────


def _universe(count=12):
    return [
        fully_sourced(f"buyer-{i:02}", access_route=ACCESS_DIRECT) for i in range(count)
    ]


def test_full_universe_is_ranked_before_package_depth():
    dna = complete_project()
    rows = _universe()
    rows.append(fully_sourced("wrong", formats=claim(["short"])))
    basic = build_sales_distribution_strategy(rows, dna, package="single", today=TODAY)
    premium = build_sales_distribution_strategy(rows, dna, package="producer", today=TODAY)
    assert basic.universe_count == premium.universe_count == 13
    assert len(basic.recommendations) == 5
    assert len(premium.recommendations) == 10
    assert all(item.profile.name != "wrong" for item in premium.recommendations)


def test_a_smaller_package_gets_fewer_names_not_different_ones():
    dna = complete_project()
    rows = _universe()
    basic = build_sales_distribution_strategy(rows, dna, package="single", today=TODAY)
    premium = build_sales_distribution_strategy(rows, dna, package="producer", today=TODAY)
    assert [item.profile.name for item in basic.recommendations] == [
        item.profile.name for item in premium.recommendations[:5]
    ]


def test_a_parent_and_its_label_take_one_package_slot():
    dna = complete_project()
    rows = [
        fully_sourced("Parent", portfolio_group="MUBI_GROUP"),
        fully_sourced("Label", portfolio_group="MUBI_GROUP"),
        fully_sourced("Unrelated"),
    ]
    result = build_sales_distribution_strategy(rows, dna, package="single", today=TODAY)
    groups = [item.profile.portfolio_group or item.profile.id for item in result.recommendations]
    assert len(groups) == len(set(groups))


def test_selection_diversifies_across_access_routes():
    """Five names a producer cannot approach is five names and no options."""
    dna = complete_project()
    rows = [
        fully_sourced("Unreachable-A", access_route=ACCESS_UNKNOWN),
        fully_sourced("Unreachable-B", access_route=ACCESS_UNKNOWN),
        fully_sourced("Reachable", access_route=ACCESS_DIRECT),
        fully_sourced("Contactable", access_route=ACCESS_CONTACT_PUBLISHED),
    ]
    result = build_sales_distribution_strategy(rows, dna, package="single", today=TODAY)
    routes = {item.profile.access_route for item in result.recommendations}
    assert len(routes) >= 2


def test_an_unsuitable_company_never_occupies_a_slot():
    dna = complete_project()
    rows = [fully_sourced("Good"), fully_sourced("Bad", formats=claim(["short"]))]
    result = build_sales_distribution_strategy(rows, dna, package="producer", today=TODAY)
    assert [item.profile.name for item in result.recommendations] == ["Good"]
    assert result.actionable_count == 1
