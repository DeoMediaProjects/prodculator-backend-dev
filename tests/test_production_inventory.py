"""The read-only production inventory.

A production command that a human runs once, under pressure, against the one
database that matters. What is tested is mostly that it cannot do damage and
cannot quietly report less than it found.
"""
from __future__ import annotations

import sqlalchemy as sa

from scripts.production_inventory import collect, render


def _db(**tables) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for name, (columns, rows) in tables.items():
            column_sql = ", ".join(f"{c} TEXT" for c in columns)
            conn.execute(sa.text(f"CREATE TABLE {name} ({column_sql})"))
            for row in rows:
                values = ", ".join(f":{c}" for c in columns)
                conn.execute(
                    sa.text(f"INSERT INTO {name} VALUES ({values})"),
                    dict(zip(columns, row)),
                )
    return engine


def test_an_empty_database_reports_every_table_as_absent():
    inventory = collect(sa.create_engine("sqlite://"))
    assert inventory["tables"] == {}
    assert "incentive_programs" in inventory["missing_tables"]
    assert all(count is None for count in inventory["staging"].values())


def test_a_missing_staging_table_is_reported_as_not_migrated():
    """Absence is a finding — it says which migrations this database lacks."""
    text = render(collect(sa.create_engine("sqlite://")))
    assert "NOT MIGRATED" in text
    assert "source_verifications" in text


def test_the_unclassified_incentive_count_is_the_gate_size():
    engine = _db(
        incentive_programs=(
            ["id", "status", "qs_engine_type"],
            [
                ("1", "active", "CORE_LOWER_OF"),
                ("2", "active", None),
                ("3", "active", None),
            ],
        )
    )
    inventory = collect(engine)
    assert inventory["tables"]["incentive_programs"]["rows"] == 3
    assert inventory["tables"]["incentive_programs"]["by_qs_engine_type"]["(null)"] == 2
    assert "2 of 3 programmes carry no statutory engine" in render(inventory)


def test_a_database_predating_the_engine_column_says_so():
    """Not an absence of data — an absence of the migration that records it."""
    engine = _db(incentive_programs=(["id", "status"], [("1", "active")]))
    inventory = collect(engine)
    assert inventory["tables"]["incentive_programs"]["missing_column_qs_engine_type"]
    assert "COLUMN ABSENT" in render(inventory)


def test_live_identifiers_are_collected_for_reconciliation():
    engine = _db(
        grant_opportunities=(["id", "title"], [("a", "One"), ("b", "Two")]),
        film_festivals=(["id", "name"], [("f1", "A Festival")]),
    )
    inventory = collect(engine)
    assert inventory["grant_ids"] == ["a", "b"]
    assert inventory["festival_ids"] == ["f1"]


def test_the_inventory_carries_identifiers_and_not_terms():
    """This file gets circulated for review; grant terms have no business in it."""
    import json

    engine = _db(
        grant_opportunities=(
            ["id", "title"],
            [("a", "A confidential grant title")],
        )
    )
    serialised = json.dumps(collect(engine), default=str)
    assert "A confidential grant title" not in serialised
    assert "a" in json.loads(serialised)["grant_ids"]


def test_the_alembic_head_is_reported_when_present():
    engine = _db(alembic_version=(["version_num"], [("abc123",)]))
    assert collect(engine)["alembic_head"] == "abc123"


def test_a_database_with_no_alembic_table_says_so_rather_than_failing():
    assert "no alembic_version table" in render(collect(sa.create_engine("sqlite://")))


def test_the_output_states_that_nothing_was_written():
    assert "nothing was written" in render(collect(sa.create_engine("sqlite://")))


def test_collecting_twice_reports_the_same_thing():
    """A read-only command is safe to re-run, which is how it gets re-run."""
    engine = _db(
        incentive_programs=(["id", "status", "qs_engine_type"], [("1", "active", None)])
    )
    first = collect(engine)
    second = collect(engine)
    assert first["tables"] == second["tables"]
