"""b1c2d3e4f5a7 settles what reconcile_grants_v2 could only report.

The reconcile script has no --apply on purpose. These are the decisions taken
on its output, recorded as a migration rather than typed into production.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import alembic
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "b1c2d3e4f5a7_grants_reconciliation_cleanup.py"
)

TELEFILM = "Telefilm Canada — Development Program"


def _stub(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "grant_opportunities", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_title", sa.Text()),
        sa.Column("opportunity_type", sa.Text()),
        sa.Column("official_source", sa.Text()),
    )
    sa.Table(
        "grant_legacy_id_map", metadata,
        sa.Column("legacy_id", sa.String(64), primary_key=True),
        sa.Column("resolved_live_id", sa.String(64)),
    )
    sa.Table(
        "grant_split_children", metadata,
        sa.Column("parent_id", sa.String(64)),
        sa.Column("child_id", sa.String(64), primary_key=True),
    )
    sa.Table(
        "source_verifications", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("gate", sa.Text()),
        sa.Column("subject_id", sa.Text()),
        sa.Column("review_state", sa.Text()),
    )
    metadata.create_all(engine)


def _run(engine):
    spec = importlib.util.spec_from_file_location("_run_b1c2", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        saved = getattr(alembic, "op", None)
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            module.upgrade()
        finally:
            if saved is not None:
                alembic.op = saved


def _exec(engine, sql, **params):
    """Run a statement and return its rows, or nothing for a write."""
    with engine.begin() as conn:
        result = conn.execute(sa.text(sql), params)
        return result.fetchall() if result.returns_rows else []


class TestDuplicates:
    def test_the_specific_type_survives(self):
        eng = sa.create_engine("sqlite://")
        _stub(eng)
        _exec(
            eng,
            "INSERT INTO grant_opportunities VALUES "
            "('a', :t, 'public_fund', 'https://telefilm.ca/a'),"
            "('b', :t, 'development_fund', 'https://telefilm.ca/b')",
            t=TELEFILM,
        )
        _run(eng)
        rows = _exec(eng, "SELECT id, opportunity_type FROM grant_opportunities")
        # public_fund gates on almost nothing; development_fund is what the
        # stage gate reads.
        assert rows == [("b", "development_fund")]

    def test_the_survivor_inherits_a_source_it_lacked(self):
        eng = sa.create_engine("sqlite://")
        _stub(eng)
        _exec(
            eng,
            "INSERT INTO grant_opportunities VALUES "
            "('a', :t, 'public_fund', 'https://telefilm.ca/sourced'),"
            "('b', :t, 'development_fund', NULL)",
            t=TELEFILM,
        )
        _run(eng)
        # A duplicate is still a row someone sourced. Dropping a verified URL
        # to tidy up would lose evidence.
        assert _exec(eng, "SELECT official_source FROM grant_opportunities") == [
            ("https://telefilm.ca/sourced",)
        ]

    def test_it_will_not_pick_a_survivor_nobody_chose(self):
        # Neither row carries the type the decision named. Deleting one anyway
        # would be a choice made here rather than by a reviewer.
        eng = sa.create_engine("sqlite://")
        _stub(eng)
        _exec(
            eng,
            "INSERT INTO grant_opportunities VALUES "
            "('a', :t, 'public_fund', NULL),('b', :t, 'other_fund', NULL)",
            t=TELEFILM,
        )
        _run(eng)
        assert len(_exec(eng, "SELECT id FROM grant_opportunities")) == 2

    def test_a_single_row_is_left_alone(self):
        eng = sa.create_engine("sqlite://")
        _stub(eng)
        _exec(
            eng,
            "INSERT INTO grant_opportunities VALUES ('a', :t, 'public_fund', NULL)",
            t=TELEFILM,
        )
        _run(eng)
        assert len(_exec(eng, "SELECT id FROM grant_opportunities")) == 1


class TestRejectedSplits:
    def test_a_rejected_split_loses_its_children(self):
        eng = sa.create_engine("sqlite://")
        _stub(eng)
        _exec(
            eng,
            "INSERT INTO source_verifications VALUES "
            "('v1','GRANTS_SPLIT_PARENT','parent-1','REJECTED')",
        )
        _exec(
            eng,
            "INSERT INTO grant_split_children VALUES "
            "('parent-1','child-1'),('parent-1','child-2'),('parent-2','child-3')",
        )
        _run(eng)
        remaining = _exec(eng, "SELECT child_id FROM grant_split_children")
        # The approved split is untouched; only the rejected parent's children go.
        assert remaining == [("child-3",)]

    def test_a_verified_split_is_kept(self):
        eng = sa.create_engine("sqlite://")
        _stub(eng)
        _exec(
            eng,
            "INSERT INTO source_verifications VALUES "
            "('v1','GRANTS_SPLIT_PARENT','parent-1','VERIFIED')",
        )
        _exec(eng, "INSERT INTO grant_split_children VALUES ('parent-1','child-1')")
        _run(eng)
        assert _exec(eng, "SELECT child_id FROM grant_split_children") == [("child-1",)]

    def test_it_reads_the_ledger_rather_than_a_hardcoded_list(self):
        # So it settles exactly the claims a reviewer rejected, and a fourth
        # rejection next week is handled without editing this file.
        eng = sa.create_engine("sqlite://")
        _stub(eng)
        _exec(
            eng,
            "INSERT INTO source_verifications VALUES "
            "('v9','GRANTS_SPLIT_PARENT','parent-9','REJECTED')",
        )
        _exec(eng, "INSERT INTO grant_split_children VALUES ('parent-9','child-9')")
        _run(eng)
        assert _exec(eng, "SELECT child_id FROM grant_split_children") == []
