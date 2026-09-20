"""Isolated commercial schema and fail-closed review loader checks."""

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

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/t9u0v1w2x3y4_commercial_staging.py"
)
TODAY = date(2026, 9, 17)
SOURCE = "https://example.org/official-catalogue"


def _run_migration(engine, direction):
    spec = importlib.util.spec_from_file_location(f"_commercial_{direction}", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        previous = alembic.op
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            getattr(module, direction)()
        finally:
            alembic.op = previous


def _claim(value):
    return {"value": value, "source_url": SOURCE, "verified_on": "2026-09-17"}


def _rows():
    companies = [{
        "id": "buyer-1", "name": "Example Buyer", "review_state": "APPROVED_SOURCE_REVIEW",
        "reviewed_on": "2026-09-17", "rules_complete": False,
        "claims": {"role": _claim("distributor"), "active": _claim(True),
                   "formats": _claim(["feature"]), "genres": _claim(["drama"])},
    }]
    comparables = [{
        "id": "film-1", "title": "Example Film", "source_url": SOURCE,
        "verified_on": "2026-09-17", "review_state": "APPROVED_SOURCE_REVIEW",
        "reviewed_on": "2026-09-17",
        "claims": {"format": _claim("feature"), "genres": _claim(["drama"])},
    }]
    relationships = [{
        "id": "link-1", "comparable_id": "film-1", "target_kind": "COMPANY",
        "target_id": "buyer-1", "relationship_type": "distribution", "source_url": SOURCE,
        "verified_on": "2026-09-17", "review_state": "APPROVED_SOURCE_REVIEW",
    }]
    return companies, comparables, relationships


def test_migration_replays_and_loader_starts_empty(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'commercial.db'}")
    with pytest.raises(RuntimeError, match="commercial staging migration"):
        load_commercial_catalogue(engine, today=TODAY)
    _run_migration(engine, "upgrade")
    _run_migration(engine, "upgrade")
    loaded = load_commercial_catalogue(engine, today=TODAY)
    assert loaded.companies == ()
    assert loaded.comparables == ()


def test_only_approved_sourced_rows_load_and_relationships_resolve():
    companies, comparables, relationships = _rows()
    companies.append({"id": "draft", "review_state": "DRAFT", "claims": {}})
    catalogue = parse_commercial_catalogue(companies, comparables, relationships, today=TODAY)
    assert [company.id for company in catalogue.companies] == ["buyer-1"]
    assert catalogue.comparables[0].relationships[0].target_id == "buyer-1"


def test_claim_and_relationship_evidence_fail_closed():
    companies, comparables, relationships = _rows()
    companies[0]["claims"]["formats"]["source_url"] = ""
    with pytest.raises(ValueError, match="usable source evidence"):
        parse_commercial_catalogue(companies, comparables, relationships, today=TODAY)

    companies, comparables, relationships = _rows()
    relationships[0]["target_id"] = "unreviewed-buyer"
    with pytest.raises(ValueError, match="verified endpoints"):
        parse_commercial_catalogue(companies, comparables, relationships, today=TODAY)

    companies, comparables, relationships = _rows()
    companies[0]["claims"]["active"]["value"] = "yes"
    with pytest.raises(ValueError, match="usable source evidence"):
        parse_commercial_catalogue(companies, comparables, relationships, today=TODAY)

    companies, comparables, relationships = _rows()
    comparables[0]["claims"]["genres"]["verified_on"] = "2026-09-18"
    with pytest.raises(ValueError, match="usable source evidence"):
        parse_commercial_catalogue(companies, comparables, relationships, today=TODAY)


def test_database_loader_reads_only_approved_profiles(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'commercial.db'}")
    _run_migration(engine, "upgrade")
    companies, comparables, relationships = _rows()
    companies[0]["reviewed_on"] = TODAY
    comparables[0]["reviewed_on"] = TODAY
    comparables[0]["verified_on"] = TODAY
    relationships[0]["verified_on"] = TODAY
    with engine.begin() as conn:
        metadata = sa.MetaData()
        company = sa.Table("commercial_company_profiles", metadata, autoload_with=conn)
        comparable = sa.Table("commercial_comparable_profiles", metadata, autoload_with=conn)
        relationship = sa.Table("commercial_comparable_relationships", metadata, autoload_with=conn)
        conn.execute(company.insert(), companies)
        conn.execute(comparable.insert(), comparables)
        conn.execute(relationship.insert(), relationships)
    catalogue = load_commercial_catalogue(engine, today=TODAY)
    assert len(catalogue.companies) == len(catalogue.comparables) == 1
    assert catalogue.comparables[0].relationships[0].target_id == catalogue.companies[0].id

def test_downgrade_refuses_populated_commercial_tables(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'commercial.db'}")
    _run_migration(engine, "upgrade")
    with engine.begin() as conn:
        table = sa.Table("commercial_company_profiles", sa.MetaData(), autoload_with=conn)
        row = _rows()[0][0]
        row["reviewed_on"] = TODAY
        conn.execute(table.insert(), row)
    with pytest.raises(RuntimeError, match="Refusing to drop populated"):
        _run_migration(engine, "downgrade")
