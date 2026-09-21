"""Two rows in the grants table that the grants engine should never return.

Both are named by documents that already settled them, so neither is a
judgement made here.

FILM LONDON PRODUCTION FINANCE MARKET

The regression report's routing correction, verbatim:

    Film London Production Finance Market is removed from Grants. It is an
    industry finance/market platform and belongs in Section 10 under
    Markets/Labs/WIP. This prevents market access from being presented as soft
    money.

It is the distinction locked decision A.7 turns on. A market is a room where a
producer may meet financiers; a grant is money. Listing the first among the
second puts an introduction in the same column as a cheque.

It is absent from the 253-record freeze entirely, which is why
`reconcile_grants_v2` reports that the freeze "does not say what becomes of
this row" — the mapping covers what the freeze knew about, and this was never
in it. The regression report is the instruction that covers it.

SCREEN TASMANIA — ISLAND SCREEN INCENTIVE

The freeze does settle this one: `routing: INCENTIVE_ENGINE`,
`opportunity_type: production_incentive`. A production incentive is the
Incentive Engine's, and locked decision D.6 says a rebate must not be routed
through the grants engine. Production still has it as GRANTS_FUNDS.

WHAT THIS DOES NOT TOUCH

`MARKETS_LABS_WIP` is already in the routing vocabulary and the grants engine
already gates on it, so re-routing removes the row from grant results without
any new code. It does NOT create a Markets/Labs/WIP record: that needs a
staged track and a verified cycle, and inventing one here would replace a row
in the wrong section with a row backed by nothing. Until that exists, Film
London is correctly absent from both sections rather than wrongly present in
one.

Matched on title rather than id. The ids are environment-specific — the
reconcile run names Film London as 7d0f8613 in production and the freeze does
not carry it at all — and a hardcoded id is a migration that silently does
nothing wherever the data differs.

Revision ID: a9b8c7d6e5f5
Revises: d0e1f2a3b4c5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9b8c7d6e5f5"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None

_TABLE = "grant_opportunities"

#: Title fragment -> the routing it should carry, with why.
_REROUTE: tuple[tuple[str, str, str], ...] = (
    (
        "production finance market",
        "MARKETS_LABS_WIP",
        "an industry finance market, not soft money (regression report, §08)",
    ),
    (
        "island screen incentive",
        "INCENTIVE_ENGINE",
        "a production incentive (grants freeze: routing INCENTIVE_ENGINE)",
    ),
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "routing" not in columns or "canonical_title" not in columns:
        return

    for fragment, routing, why in _REROUTE:
        rows = conn.execute(
            sa.text(
                f"SELECT canonical_title, routing FROM {_TABLE} "
                f"WHERE lower(canonical_title) LIKE :pattern"
            ),
            {"pattern": f"%{fragment}%"},
        ).fetchall()
        if not rows:
            print(f"[{revision}] no row matching {fragment!r}; nothing to re-route")
            continue
        for title, current in rows:
            if current == routing:
                print(f"[{revision}] already {routing}: {title}")
                continue
            print(f"[{revision}] {current} -> {routing}: {title} — {why}")
        result = conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET routing = :routing "
                f"WHERE lower(canonical_title) LIKE :pattern AND routing <> :routing"
            ),
            {"routing": routing, "pattern": f"%{fragment}%"},
        )
        print(f"[{revision}]   rows changed: {result.rowcount or 0}")

    # Report anything else still filed as a grant whose title says otherwise.
    # Reported, never re-routed: "lab" in a title is not proof, and the seven
    # this finds on the dev copy include genuine production funds whose names
    # happen to contain the word.
    suspects = conn.execute(
        sa.text(
            f"SELECT canonical_title FROM {_TABLE} WHERE routing = 'GRANTS_FUNDS' "
            f"AND (lower(canonical_title) LIKE '%market%' "
            f"OR lower(canonical_title) LIKE '%residency%' "
            f"OR lower(canonical_title) LIKE '%work-in-progress%')"
        )
    ).fetchall()
    if suspects:
        print(
            f"[{revision}] still routed to grants and worth a human look "
            f"({len(suspects)}):"
        )
        for (title,) in suspects:
            print(f"[{revision}]   {title}")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in set(inspector.get_table_names()):
        return
    if "routing" not in {c["name"] for c in inspector.get_columns(_TABLE)}:
        return
    for fragment, _routing, _why in _REROUTE:
        conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET routing = 'GRANTS_FUNDS' "
                f"WHERE lower(canonical_title) LIKE :pattern"
            ),
            {"pattern": f"%{fragment}%"},
        )
    print(f"[{revision}] both rows returned to GRANTS_FUNDS")
