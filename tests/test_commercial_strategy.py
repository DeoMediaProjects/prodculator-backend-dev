"""Conservative commercial matching acceptance checks."""

from dataclasses import replace
from datetime import date

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
    strategy = match_comparables([film], dna, today=TODAY, festival_ids={"f1"}, market_ids={"m1"})
    item = strategy.recommendations[0]
    assert item.score == 8
    assert len(item.verified_relationships) == 2
    assert not any("market pathway" in reason for reason in item.reasons)
    buyer = match_company(company(), dna, today=TODAY, comparables=strategy)
    assert any("A Real Film" in reason for reason in buyer.reasons)
    assert buyer.status == "POTENTIAL_FIT"


def test_known_company_hard_failure_beats_comparable_signal():
    dna = project(format="short film")
    film = comparable("Short", format=claim("short"), genres=claim(["drama"]))
    linked = replace(
        film,
        relationships=(ComparableRelationship("COMPANY", "Buyer", "distribution", SOURCE, TODAY),),
    )
    comps = match_comparables([linked], dna, today=TODAY)
    buyer = match_company(company(), dna, today=TODAY, comparables=comps)
    assert buyer.status == "NOT_A_FIT"
    assert ("acquisition format", "FAIL") in buyer.gate_results
    assert buyer.score == 0


def test_unknown_rights_territory_is_not_a_pass_or_fail():
    dna = project()
    buyer = company(rights_territories=claim(["United Kingdom"]))
    result = match_company(buyer, dna, today=TODAY)
    assert result.status == "POTENTIAL_FIT"
    assert ("rights territory", "UNKNOWN") in result.gate_results
    assert "rights territory" in result.conditions_to_confirm
    declared = project(target_sales_territories=["United States"])
    assert match_company(buyer, declared, today=TODAY).status == "NOT_A_FIT"


def test_unsourced_inactive_or_bare_name_is_not_actionable():
    dna = project()
    assert match_company(company(active=claim(False)), dna, today=TODAY).status == "NOT_ACTIONABLE"
    assert match_company(
        company(role=claim("distributor", source="")), dna, today=TODAY
    ).status == "NOT_ACTIONABLE"
    assert match_company(
        company(formats=None, genres=None), dna, today=TODAY
    ).status == "NOT_ACTIONABLE"


def test_full_universe_is_ranked_before_package_depth():
    dna = project()
    rows = [company(f"buyer-{i:02}", genres=claim(["drama"] if i % 2 else ["horror"])) for i in range(12)]
    rows.append(company("wrong", formats=claim(["short"])))
    basic = build_sales_distribution_strategy(rows, dna, package="single", today=TODAY)
    premium = build_sales_distribution_strategy(rows, dna, package="producer", today=TODAY)
    assert basic.universe_count == premium.universe_count == 13
    assert len(basic.recommendations) == 5
    assert len(premium.recommendations) == 10
    assert all(item.profile.name != "wrong" for item in premium.recommendations)
    assert all(item.score == 2 for item in basic.recommendations)


def test_incomplete_rules_cannot_be_confirmed_even_when_known_gates_pass():
    dna = project()
    potential = match_company(company(), dna, today=TODAY)
    incomplete_claims = match_company(company(rules_complete=True), dna, today=TODAY)
    complete_dna = project(
        project_stage="development", target_sales_territories=["United Kingdom"]
    )
    confirmed = match_company(
        company(
            rules_complete=True,
            acquisition_stages=claim(["development"]),
            rights_territories=claim(["United Kingdom"]),
        ),
        complete_dna,
        today=TODAY,
    )
    assert potential.status == "POTENTIAL_FIT"
    assert incomplete_claims.status == "POTENTIAL_FIT"
    assert confirmed.status == "FIT_CONFIRMED"


def test_sourced_global_scope_does_not_exclude_known_territory():
    dna = project(target_sales_territories=["Kenya"])
    buyer = company(rights_territories=claim(["worldwide"]))
    result = match_company(buyer, dna, today=TODAY)
    assert result.status == "POTENTIAL_FIT"
    assert ("rights territory", "PASS") in result.gate_results
