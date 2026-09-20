"""Gate 8: what a comparable title is, and why the whole chain needs it.

The commercial freeze records which company handled each of 205 titles and not
one attribute of the titles themselves. ``match_comparables`` needs two sourced
similarities before it will offer a title as evidence, so with no attributes all
205 are dropped — and the 25-point comparable-evidence component of every
company score goes with them.

The last test is the one that matters: a title with a researched format and
genre becomes evidence, and the company that handled it scores for it.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.reports.commercial_catalogue import parse_commercial_catalogue
from app.modules.reports.commercial_strategy import (
    COMMERCIAL_COMPARABLE,
    build_sales_distribution_strategy,
    match_comparables,
)
from app.modules.reports.project_dna import build_project_dna
from app.modules.reports.verification_ledger import (
    GATE_COMPARABLE_TITLE,
    GATES,
    GATES_REQUIRING_INDEPENDENT_QA,
    SourceClaim,
)
from scripts.ingest_research_workbook import value_problems
from scripts.stage_commercial_catalogue import APPROVED, StageReport, build_rows

TODAY = date(2026, 9, 20)
SITE = "https://example-films.com/about"


def _claim(subject: str, field_name: str, value: str) -> SourceClaim:
    return SourceClaim(
        gate=GATE_COMPARABLE_TITLE,
        subject_id=subject,
        field=field_name,
        value=value,
        source_url=SITE,
        source_basis="The film's official page states it.",
        verified_on=date(2026, 9, 18),
        verified_by="researcher",
        review_state="VERIFIED",
        reviewed_by="reviewer",
    )


def _company(**overrides) -> dict:
    row = {
        "company_id": "SD-9001",
        "company_name": "Example Sales",
        "company_type": "INTERNATIONAL_SALES",
        "company_roles": "INTERNATIONAL_SALES",
        "territory_scope": "Worldwide",
        "formats": "Feature films",
        "source_url": SITE,
        "source_basis": "Official company About page",
        "verification_status": "VERIFIED_CURRENT_IDENTITY_PROFILE",
        "verification_date": "2026-09-16",
        "last_verified_at": "2026-09-16",
        "paid_match_eligible": "YES",
        "access_route_status": "DIRECT_OPEN",
        "brand_status": "CURRENT",
        "portfolio_group_key": "EXAMPLE",
        "canonical_company_id": "SD-9001",
    }
    row.update(overrides)
    return row


def _title() -> dict:
    return {
        "film_title": "Example Film",
        "year": 2024,
        "company_name": "Example Sales",
        "relationship_type": "SALES_HANDLED",
        "territory": "Worldwide",
        "source_url": SITE,
        "verification_status": "VERIFIED",
    }


def _payload() -> dict:
    return {"companies": [_company()], "relationships": [_title()]}


def _ledger(comparable_id: str, **fields) -> dict:
    return {comparable_id: {name: _claim(comparable_id, name, v) for name, v in fields.items()}}


def _project() -> object:
    return build_project_dna(
        {
            "format": "feature film",
            "genre": ["drama", "thriller"],
            "primary_languages": ["English"],
            "production_countries": ["United Kingdom"],
            "target_sales_territories": ["worldwide"],
            "project_stage": "development",
        },
        {"_budget_gbp": 3_500_000, "_runtime_minutes": 105},
    )


# ── The gate ─────────────────────────────────────────────────────────────────


def test_the_gate_is_registered_and_needs_only_one_reviewer():
    """Reading a film's format off its page is transcription, not judgement."""
    assert GATE_COMPARABLE_TITLE in GATES
    assert GATE_COMPARABLE_TITLE not in GATES_REQUIRING_INDEPENDENT_QA


# ── Shape checks on ingest ───────────────────────────────────────────────────


def _shape(field_name: str, value: str):
    return value_problems(_claim("film-1", field_name, value))


@pytest.mark.parametrize(
    "value", ["feature", "short", "documentary", "tv_series", "animation"]
)
def test_a_canonical_format_passes(value):
    assert _shape("format", value) == []


def test_a_format_phrase_is_refused():
    """"Documentary feature" matches neither token and reads as unresearched."""
    problems = _shape("format", "documentary feature")
    assert problems and "is not one of" in problems[0]


def test_a_semicolon_list_passes_and_a_blank_one_does_not():
    assert _shape("genres", "drama; thriller") == []
    assert _shape("production_countries", " ; ") != []


def test_a_field_outside_the_gate_is_refused():
    problems = _shape("box_office", "a lot")
    assert problems and "not a comparable title field" in problems[0]


# ── Verified attributes reaching the catalogue ───────────────────────────────


def _staged(ledger: dict | None = None):
    report = StageReport()
    companies, comparables, relationships = build_rows(
        _payload(), {}, report, ledger or {}
    )
    return companies, comparables, relationships, report


def test_without_the_gate_a_title_carries_nothing():
    _, comparables, _, _ = _staged()
    assert comparables[0]["claims"] == {}


def test_verified_attributes_become_catalogue_claims():
    _, comparables, _, _ = _staged()
    identity = comparables[0]["id"]
    ledger = _ledger(identity, format="feature", genres="Drama; Thriller")

    companies, comparables, relationships, report = _staged(ledger)
    claims = comparables[0]["claims"]
    assert claims["format"]["value"] == "feature"
    assert claims["genres"]["value"] == ["drama", "thriller"]
    assert report.ledger_claims_used == {"format": 1, "genres": 1}

    # And the catalogue accepts them, which is the only test that counts.
    catalogue = parse_commercial_catalogue(
        companies, comparables, relationships, today=TODAY
    )
    assert catalogue.comparables[0].format.value == "feature"


def test_a_blank_verified_value_is_dropped_and_counted():
    _, comparables, _, _ = _staged()
    identity = comparables[0]["id"]
    _, comparables, _, report = _staged(_ledger(identity, genres="  ;  "))
    assert "genres" not in comparables[0]["claims"]
    assert report.ledger_claims_ignored["genres"] == 1


# ── The whole chain ──────────────────────────────────────────────────────────


def test_an_unresearched_title_is_never_offered_as_evidence():
    companies, comparables, relationships, _ = _staged()
    catalogue = parse_commercial_catalogue(
        companies, comparables, relationships, today=TODAY
    )
    result = match_comparables(catalogue.comparables, _project(), today=TODAY)
    assert result.universe_count == 1
    assert result.recommendations == (), (
        "a title with no sourced similarity is not a defensible comparable"
    )


def test_a_researched_title_becomes_evidence_and_the_company_scores_for_it():
    """The point of gate 8, end to end."""
    _, comparables, _, _ = _staged()
    identity = comparables[0]["id"]
    ledger = _ledger(
        identity,
        format="feature",
        genres="Drama; Thriller",
        production_countries="United Kingdom",
        primary_languages="English",
    )
    companies, comparables, relationships, _ = _staged(ledger)
    catalogue = parse_commercial_catalogue(
        companies, comparables, relationships, today=TODAY
    )
    project = _project()

    matched = match_comparables(catalogue.comparables, project, today=TODAY)
    assert len(matched.recommendations) == 1
    offered = matched.recommendations[0]
    assert COMMERCIAL_COMPARABLE in offered.roles

    without = build_sales_distribution_strategy(
        catalogue.companies, project, package="producer", today=TODAY
    )
    with_evidence = build_sales_distribution_strategy(
        catalogue.companies, project, package="producer", today=TODAY,
        comparables=matched,
    )
    assert with_evidence.recommendations[0].score > without.recommendations[0].score
    assert any(
        "comparable title history" in reason
        for reason in with_evidence.recommendations[0].reasons
    )


def test_production_similarity_alone_still_cannot_become_buyer_evidence():
    """Gate 8 must not become a back door to the guardrail it sits behind."""
    _, comparables, _, _ = _staged()
    identity = comparables[0]["id"]
    # Production dimensions only: no genre, tone, audience or release profile,
    # and the relationship stripped out.
    ledger = _ledger(identity, format="feature", production_countries="United Kingdom")
    companies, comparables, relationships, _ = _staged(ledger)
    catalogue = parse_commercial_catalogue(companies, comparables, [], today=TODAY)
    matched = match_comparables(catalogue.comparables, _project(), today=TODAY)
    for item in matched.recommendations:
        assert not item.supports_commercial_evidence
