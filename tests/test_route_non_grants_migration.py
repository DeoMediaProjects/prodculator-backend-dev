"""a9b8c7d6e5f5 takes a market and an incentive out of the grants table.

Both were settled elsewhere. The regression report removes Film London
Production Finance Market from Grants because it is an industry finance
platform and listing it as soft money puts an introduction in the same column
as a cheque. The grants freeze routes Screen Tasmania's Island Screen
Incentive to INCENTIVE_ENGINE, and locked decision D.6 says a rebate does not
go through the grants engine.
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
    / "alembic" / "versions" / "a9b8c7d6e5f5_route_two_non_grants_out.py"
)


def _stub(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "grant_opportunities", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_title", sa.Text()),
        sa.Column("routing", sa.Text()),
    )
    metadata.create_all(engine)


def _run(engine, direction: str = "upgrade"):
    spec = importlib.util.spec_from_file_location(f"_run_a9b8_{direction}", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        saved = getattr(alembic, "op", None)
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            getattr(module, direction)()
        finally:
            if saved is not None:
                alembic.op = saved


def _seed(engine, rows):
    with engine.begin() as conn:
        for row_id, title, routing in rows:
            conn.execute(
                sa.text(
                    "INSERT INTO grant_opportunities (id, canonical_title, routing) "
                    "VALUES (:i, :t, :r)"
                ),
                {"i": row_id, "t": title, "r": routing},
            )


def _routing(engine) -> dict[str, str]:
    with engine.begin() as conn:
        return {
            r[0]: r[1]
            for r in conn.execute(
                sa.text("SELECT canonical_title, routing FROM grant_opportunities")
            )
        }


@pytest.fixture()
def engine():
    eng = sa.create_engine("sqlite://")
    _stub(eng)
    return eng


def test_the_finance_market_stops_being_a_grant(engine):
    _seed(engine, [("1", "Film London — Production Finance Market", "GRANTS_FUNDS")])
    _run(engine)
    assert _routing(engine)["Film London — Production Finance Market"] == "MARKETS_LABS_WIP"


def test_the_production_incentive_goes_to_the_incentive_engine(engine):
    _seed(engine, [("2", "Screen Tasmania — Island Screen Incentive", "GRANTS_FUNDS")])
    _run(engine)
    assert _routing(engine)["Screen Tasmania — Island Screen Incentive"] == "INCENTIVE_ENGINE"


def test_real_grants_are_untouched(engine):
    # Including one whose title contains "market". The migration reports those
    # for a human and re-routes none of them: the word is not proof.
    _seed(engine, [
        ("3", "BFI National Lottery Development Funding", "GRANTS_FUNDS"),
        ("4", "Finnish Film Foundation — Marketing & Distribution", "GRANTS_FUNDS"),
    ])
    _run(engine)
    assert set(_routing(engine).values()) == {"GRANTS_FUNDS"}


def test_running_it_twice_changes_nothing_further(engine):
    _seed(engine, [("1", "Film London — Production Finance Market", "GRANTS_FUNDS")])
    _run(engine)
    first = _routing(engine)
    _run(engine)
    assert _routing(engine) == first


def test_a_missing_row_is_not_an_error(engine):
    # The environments differ: the freeze does not carry Film London at all.
    _run(engine)
    assert _routing(engine) == {}


def test_downgrade_puts_them_back(engine):
    _seed(engine, [
        ("1", "Film London — Production Finance Market", "GRANTS_FUNDS"),
        ("2", "Screen Tasmania — Island Screen Incentive", "GRANTS_FUNDS"),
    ])
    _run(engine)
    _run(engine, "downgrade")
    assert set(_routing(engine).values()) == {"GRANTS_FUNDS"}
