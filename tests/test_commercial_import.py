"""Staging the commercial freeze, and the review states it may and may not grant.

The importer's job is to be the only path from the frozen workbook into the
matching universe, and the thing that makes it safe is what it declines to
approve. Three behaviours carry that:

* a profile the freeze read off a directory does not enter until the ledger says
  the company's own site agrees;
* a relationship is approved only when both its endpoints are, because a
  dangling one makes the catalogue loader raise and takes the whole commercial
  section down with it;
* prose is never a claim.

The last test runs the real 101-company freeze through the importer and then
through ``parse_commercial_catalogue``, which is the only check that matters in
production: every row this writes as approved must be a row the engine can read.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import alembic
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.modules.reports.commercial_catalogue import (
    load_commercial_catalogue,
    parse_commercial_catalogue,
)
from app.modules.reports.commercial_freeze import (
    ACCESS_CONTACT_PUBLISHED,
    ACCESS_UNKNOWN,
)
from app.modules.reports.verification_ledger import GATE_COMMERCIAL_PROFILE, SourceClaim
from scripts.stage_commercial_catalogue import (
    APPROVED,
    PENDING,
    StageReport,
    _load_snapshot,
    build_rows,
    stage_commercial_catalogue,
)

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "alembic/versions"
MIGRATIONS = (
    "t9u0v1w2x3y4_commercial_staging.py",
    "y4z5a6b7c8d9_commercial_access_route_and_group.py",
)
TODAY = date(2026, 9, 20)
SITE = "https://example-films.com/about"


def _run(engine, filename: str, direction: str) -> None:
    spec = importlib.util.spec_from_file_location(f"_m_{filename}_{direction}", VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        previous = alembic.op
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            getattr(module, direction)()
        finally:
            alembic.op = previous


def _staged(tmp_path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'commercial.db'}")
    for filename in MIGRATIONS:
        _run(engine, filename, "upgrade")
    return engine


def _company(**overrides) -> dict:
    row = {
        "company_id": "SD-9001",
        "company_name": "Example Sales",
        "company_type": "INTERNATIONAL_SALES",
        "company_roles": "INTERNATIONAL_SALES",
        "territory_scope": "Worldwide",
        "formats": "Feature films",
        "genre_specialties": "Broad; auteur where slate supports",
        "acquisition_stage": "UNKNOWN",
        "source_url": SITE,
        "source_basis": "Official company About page",
        "verification_status": "VERIFIED_CURRENT_IDENTITY_PROFILE",
        "verification_date": "2026-09-16",
        "last_verified_at": "2026-09-16",
        "paid_match_eligible": "YES",
        "access_route_status": "PUBLIC_ACQUISITIONS_CONTACT",
        "brand_status": "CURRENT",
        "portfolio_group_key": "EXAMPLE",
        "canonical_company_id": "SD-9001",
    }
    row.update(overrides)
    return row


def _payload(companies=None, relationships=None) -> dict:
    return {
        "companies": companies if companies is not None else [_company()],
        "relationships": relationships if relationships is not None else [],
    }


def _title(**overrides) -> dict:
    row = {
        "film_title": "Example Film",
        "year": 2024,
        "company_name": "Example Sales",
        "relationship_type": "SALES_HANDLED",
        "territory": "Worldwide",
        "source_url": SITE,
        "verification_status": "VERIFIED",
    }
    row.update(overrides)
    return row


def _claim(subject: str, field_name: str, value: str) -> SourceClaim:
    return SourceClaim(
        gate=GATE_COMMERCIAL_PROFILE,
        subject_id=subject,
        field=field_name,
        value=value,
        source_url=SITE,
        source_basis="The company's own site states it.",
        verified_on=date(2026, 9, 18),
        verified_by="researcher",
        review_state="VERIFIED",
        reviewed_by="reviewer",
    )


def _ledger(*claims: SourceClaim) -> dict:
    by_subject: dict[str, dict] = {}
    for claim in claims:
        by_subject.setdefault(claim.subject_id, {})[claim.field] = claim
    return by_subject


# ── What the freeze alone supports ───────────────────────────────────────────


def test_a_fully_sourced_company_is_approved_with_its_typed_scopes():
    report = StageReport()
    companies, _, _ = build_rows(_payload(), {}, report)
    row = companies[0]
    assert row["review_state"] == APPROVED
    assert row["reviewed_on"] == date(2026, 9, 16)
    assert row["claims"]["role"]["value"] == ["sales_agent"]
    assert row["claims"]["active"]["value"] is True
    assert row["claims"]["rights_territories"]["value"] == ["worldwide"]
    assert row["claims"]["formats"]["value"] == ["feature"]
    assert row["access_route"] == ACCESS_CONTACT_PUBLISHED
    assert row["portfolio_group"] == "EXAMPLE"


def test_prose_never_becomes_a_claim():
    """Genres and acquisition stage are prose in the freeze and stay out of it."""
    report = StageReport()
    companies, _, _ = build_rows(_payload(), {}, report)
    assert "genres" not in companies[0]["claims"]
    assert "acquisition_stages" not in companies[0]["claims"]


def test_rules_are_never_recorded_as_complete():
    report = StageReport()
    companies, _, _ = build_rows(_payload(), {}, report)
    assert companies[0]["rules_complete"] is False


def test_an_untypable_scope_yields_no_claim_rather_than_an_empty_one():
    report = StageReport()
    companies, _, _ = build_rows(
        _payload([_company(territory_scope="Worldwide / major territories")]), {}, report
    )
    assert "rights_territories" not in companies[0]["claims"]


def test_an_unrecorded_access_route_is_unknown_not_absent():
    report = StageReport()
    companies, _, _ = build_rows(
        _payload([_company(access_route_status="UNKNOWN", submission_route="UNKNOWN")]),
        {},
        report,
    )
    assert companies[0]["access_route"] == ACCESS_UNKNOWN


# ── What it declines to approve ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        {"paid_match_eligible": "NO"},
        {"verification_status": "PARTIALLY_VERIFIED"},
        {"verification_status": "IDENTITY_PROFILE_NEEDS_SOURCE_REFRESH"},
        {"brand_status": "LEGACY_SUCCESSOR_ROUTE"},
        {"company_roles": "TALENT_AGENCY", "company_type": "TALENT_AGENCY"},
    ],
)
def test_the_freeze_hedging_a_row_keeps_it_out(overrides):
    report = StageReport()
    companies, _, _ = build_rows(_payload([_company(**overrides)]), {}, report)
    assert companies[0]["review_state"] == PENDING
    assert companies[0]["reviewed_on"] is None
    assert report.held_back


def test_a_retired_brand_records_active_false_rather_than_nothing():
    """Closed, not unresearched. The distinction is what a reviewer needs."""
    report = StageReport()
    companies, _, _ = build_rows(
        _payload([_company(brand_status="LEGACY_SUCCESSOR_ROUTE")]), {}, report
    )
    assert companies[0]["claims"]["active"]["value"] is False


def test_a_directory_profile_waits_for_the_ledger():
    directory = _company(verification_status="VERIFIED_CURRENT_DIRECTORY_PROFILE")
    report = StageReport()
    companies, _, _ = build_rows(_payload([directory]), {}, report)
    assert companies[0]["review_state"] == PENDING

    confirmed = _ledger(_claim("SD-9001", "primary_source_confirmation", "CONFIRMED"))
    companies, _, _ = build_rows(_payload([directory]), confirmed, StageReport())
    assert companies[0]["review_state"] == APPROVED


def test_an_unconfirmed_directory_profile_is_not_a_confirmed_one():
    """The researcher wrote UNKNOWN, which is not a yes."""
    directory = _company(verification_status="VERIFIED_CURRENT_DIRECTORY_PROFILE")
    unknown = _ledger(_claim("SD-9001", "primary_source_confirmation", "UNKNOWN"))
    companies, _, _ = build_rows(_payload([directory]), unknown, StageReport())
    assert companies[0]["review_state"] == PENDING


# ── Verified research on top ─────────────────────────────────────────────────


def test_a_verified_stage_claim_becomes_an_acquisition_stage():
    ledger = _ledger(_claim("SD-9001", "acquisition_stage", "completed"))
    report = StageReport()
    companies, _, _ = build_rows(_payload(), ledger, report)
    claim = companies[0]["claims"]["acquisition_stages"]
    assert claim["value"] == ["completed"]
    assert claim["verified_on"] == "2026-09-18"
    assert report.ledger_claims_used["acquisition_stage"] == 1


def test_a_stage_outside_the_vocabulary_is_dropped_and_counted():
    ledger = _ledger(_claim("SD-9001", "acquisition_stage", "whenever they feel like it"))
    report = StageReport()
    companies, _, _ = build_rows(_payload(), ledger, report)
    assert "acquisition_stages" not in companies[0]["claims"]
    assert report.ledger_claims_ignored["acquisition_stage"] == 1


def test_a_verified_genre_claim_splits_on_semicolons():
    ledger = _ledger(_claim("SD-9001", "genre_specialties", "Action; Thriller ; horror"))
    companies, _, _ = build_rows(_payload(), ledger, StageReport())
    assert companies[0]["claims"]["genres"]["value"] == ["action", "horror", "thriller"]


def test_a_signal_with_no_scored_field_is_not_invented_into_one():
    ledger = _ledger(_claim("SD-9001", "festival_market_signal", "Cannes; EFM"))
    companies, _, _ = build_rows(_payload(), ledger, StageReport())
    assert set(companies[0]["claims"]) == {"role", "active", "rights_territories", "formats"}


def test_an_unreviewed_ledger_claim_changes_nothing():
    pending = _claim("SD-9001", "acquisition_stage", "completed")
    pending = SourceClaim(**{**pending.__dict__, "review_state": "PENDING", "reviewed_by": None})
    # readable_claims withholds it, so the importer never sees it at all.
    from app.modules.reports.verification_ledger import readable_claims

    assert readable_claims([pending], today=TODAY) == ()


# ── Titles and the endpoints they depend on ──────────────────────────────────


def test_a_title_relationship_needs_both_endpoints_approved():
    directory = _company(verification_status="VERIFIED_CURRENT_DIRECTORY_PROFILE")
    report = StageReport()
    _, comparables, relationships = build_rows(
        _payload([directory], [_title()]), {}, report
    )
    assert comparables[0]["review_state"] == APPROVED
    assert relationships[0]["review_state"] == PENDING, (
        "the company is not approved, so approving the link would make the "
        "catalogue loader raise"
    )


def test_a_comparable_arrives_with_its_identity_sourced_and_nothing_else():
    _, comparables, _ = build_rows(_payload(None, [_title()]), {}, StageReport())
    assert comparables[0]["claims"] == {}
    assert comparables[0]["source_url"] == SITE


def test_one_comparable_per_title_however_many_relationships():
    payload = _payload(
        [_company(), _company(company_id="SD-9002", company_name="Other Sales", portfolio_group_key="OTHER")],
        [_title(), _title(company_name="Other Sales", relationship_type="DISTRIBUTOR")],
    )
    _, comparables, relationships = build_rows(payload, {}, StageReport())
    assert len(comparables) == 1
    assert len(relationships) == 2
    assert {row["comparable_id"] for row in relationships} == {comparables[0]["id"]}


def test_the_freeze_label_is_stored_rather_than_a_normalisation_of_it():
    _, _, relationships = build_rows(_payload(None, [_title()]), {}, StageReport())
    assert relationships[0]["relationship_type"] == "SALES_HANDLED"


# ── Against the database ─────────────────────────────────────────────────────


def test_apply_inserts_then_reruns_clean(tmp_path):
    engine = _staged(tmp_path)
    first = stage_commercial_catalogue(engine, apply=True, payload=_payload(None, [_title()]), today=TODAY)
    assert first.inserted["commercial_company_profiles"] == 1
    assert first.inserted["commercial_comparable_profiles"] == 1

    second = stage_commercial_catalogue(engine, apply=True, payload=_payload(None, [_title()]), today=TODAY)
    assert sum(second.inserted.values()) == 0
    assert sum(second.updated.values()) == 0, second.changes


def test_a_new_verified_claim_updates_the_row_and_says_what_moved(tmp_path):
    engine = _staged(tmp_path)
    payload = _payload()
    stage_commercial_catalogue(engine, apply=True, payload=payload, today=TODAY)

    from app.modules.reports import verification_store as store

    report = stage_commercial_catalogue(
        engine, apply=True, payload=payload, today=TODAY
    )
    assert sum(report.updated.values()) == 0

    # Recording research is the event that should change a staged row.
    monkey = _ledger(_claim("SD-9001", "acquisition_stage", "completed"))
    original = store.load_claims
    try:
        store.load_claims = lambda *a, **k: [  # type: ignore[assignment]
            claim for fields in monkey.values() for claim in fields.values()
        ]
        report = stage_commercial_catalogue(engine, apply=True, payload=payload, today=TODAY)
    finally:
        store.load_claims = original
    assert report.updated["commercial_company_profiles"] == 1
    assert any("claims" in line for line in report.changes)


def test_staging_without_the_column_migration_names_it(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    _run(engine, MIGRATIONS[0], "upgrade")
    with pytest.raises(RuntimeError, match="y4z5a6b7c8d9"):
        stage_commercial_catalogue(engine, apply=False, payload=_payload(), today=TODAY)


def test_staged_rows_load_back_through_the_catalogue(tmp_path):
    engine = _staged(tmp_path)
    stage_commercial_catalogue(engine, apply=True, payload=_payload(None, [_title()]), today=TODAY)
    catalogue = load_commercial_catalogue(engine, today=TODAY)
    assert [company.id for company in catalogue.companies] == ["SD-9001"]
    assert catalogue.companies[0].access_route == ACCESS_CONTACT_PUBLISHED
    assert catalogue.companies[0].portfolio_group == "EXAMPLE"
    assert catalogue.comparables[0].relationships[0].target_id == "SD-9001"


# ── Against the real freeze ──────────────────────────────────────────────────


def test_the_whole_freeze_stages_and_the_engine_can_read_every_approved_row():
    """The check that matters. Anything this importer approves, the loader parses.

    ``parse_commercial_catalogue`` raises on a malformed approved row, the
    builder catches that into a warning, and the commercial section returns
    nothing with no visible cause. So the contract is tested against all 101
    rows rather than a fixture that happens to be well formed.
    """
    payload = _load_snapshot()
    report = StageReport()
    companies, comparables, relationships = build_rows(payload, {}, report)
    assert report.companies_seen == 101
    assert report.relationships_seen == 205

    catalogue = parse_commercial_catalogue(
        companies, comparables, relationships, today=TODAY
    )
    approved = {row["id"] for row in companies if row["review_state"] == APPROVED}
    assert {company.id for company in catalogue.companies} == approved
    assert all(company.role and company.active for company in catalogue.companies)


def test_the_freeze_approves_what_its_own_sourcing_supports_and_no_more():
    """Fifty-eight of 101 without research; the other 39 wait on the ledger.

    A change in either number is a change in what a producer is shown, so it is
    asserted rather than described.
    """
    report = StageReport()
    companies, _, _ = build_rows(_load_snapshot(), {}, report)
    assert report.companies_approved == 58
    directory_held = report.held_back[
        "profile came from a directory and no verified confirmation says "
        "the company's own site agrees"
    ]
    assert directory_held == 39
    assert sum(report.held_back.values()) == 43


def test_no_relationship_in_the_real_freeze_points_at_an_unapproved_company():
    payload = _load_snapshot()
    companies, comparables, relationships = build_rows(payload, {}, StageReport())
    approved_companies = {row["id"] for row in companies if row["review_state"] == APPROVED}
    approved_titles = {row["id"] for row in comparables if row["review_state"] == APPROVED}
    for row in relationships:
        if row["review_state"] != APPROVED:
            continue
        assert row["target_id"] in approved_companies
        assert row["comparable_id"] in approved_titles
