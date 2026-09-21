"""Settle the four findings reconcile_grants_v2 could not settle itself.

`reconcile_grants_v2` is a read-only rehearsal with no --apply, deliberately:
the handoff requires a reviewed diff before anything is imported, and a script
that can both report and import invites the second step to be taken on the
strength of the first. It reported; these are the decisions taken on that
report, each one recorded here rather than run by hand against production.

1 — THREE TITLES APPEAR TWICE, IN THE FREEZE ITSELF

Not production drift. The 253-record master carries both variants:

    Telefilm Canada — Development Program        public_fund / development_fund
    Telefilm Canada — Talent to Watch Program    public_fund / debut_feature_fund
    Netherlands Film Fund — Minority Co-production Feature Film & Documentary
                                     co_production_fund / minority_coproduction_fund

The specific type survives in each. `public_fund` is a catch-all that gates on
almost nothing, while `development_fund`, `debut_feature_fund` and
`minority_coproduction_fund` are what the engine's stage and co-production
gates read — so keeping the general one would leave the record matching
producers it should not.

The survivor inherits an `official_source` the loser had and it lacked. A
duplicate row is still a row someone sourced, and dropping a verified URL to
tidy up would lose evidence.

2 — FOUR MAPPINGS POINT AT NOTHING

KOFIC International Co-Production Fund, Busan Asian Cinema Fund, IDFA Bertha
Fund and IFFR Hubert Bals Production each resolve to a live id that does not
exist, so a legacy reference following one would dangle. The mapping documents
a move that never completed; it is retired rather than repointed, because
repointing means matching by name and a wrong pairing silently sends a legacy
reference to the wrong fund.

3 — SIX MAPPINGS NAME A LIVE ROW THAT IS GONE

Same shape, other direction, and the same treatment.

4 — THREE REJECTED SPLITS

A reviewer turned down a proposal to split three parents into children. The
children are removed and the parents stand as single opportunities. A REJECTED
claim is a decision that was made, not a question still open, and leaving the
children in place would apply a split nobody approved.

Revision ID: b1c2d3e4f5a7
Revises: a9b8c7d6e5f5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a7"
down_revision = "a9b8c7d6e5f5"
branch_labels = None
depends_on = None

#: Title -> the opportunity_type that survives. Both variants exist in the
#: frozen master; the specific one is what the stage and co-production gates
#: actually read.
_KEEP_TYPE: dict[str, str] = {
    "Telefilm Canada — Development Program": "development_fund",
    "Telefilm Canada — Talent to Watch Program": "debut_feature_fund",
    "Netherlands Film Fund — Minority Co-production Feature Film & Documentary":
        "minority_coproduction_fund",
}

#: Mappings whose target no longer exists, by legacy_id.
_DEAD_TARGETS: tuple[str, ...] = (
    "2516878e-27cb-524d-9f8d-1b8520922435",  # KOFIC International Co-Production Fund
    "39b9b071-6f7b-5458-b7dc-07dc74e1013b",  # Busan Asian Cinema Fund
    "264e67c0-2807-597e-af13-69e48d3c58a9",  # IDFA Bertha Fund
    "2728adc5-5744-5c2d-8f7f-03778572cf8a",  # IFFR Hubert Bals Fund — Production
)

#: Mappings naming a live id that has left the table, by resolved_live_id.
_STALE_LIVE_IDS: tuple[str, ...] = (
    "87b941bc-5136-5503-9910-b42887ba08ee",
    "90f19637-a5b0-5e1e-b1e2-8989a7d6e3f8",
    "c3041514-1b51-5684-8ec2-4c69fd5a2dc8",
    "cd08490e-86d2-5876-94b7-268d24c1f173",
    "f38ed385-e7e2-5ad2-a1ae-2a72bdcd112e",
    "f7d2c098-4575-5834-8a62-81ddf0d5720f",
)


def _has(conn, table: str) -> bool:
    return table in set(sa.inspect(conn).get_table_names())


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Duplicates ────────────────────────────────────────────────────────
    if _has(conn, "grant_opportunities"):
        columns = {
            c["name"] for c in sa.inspect(conn).get_columns("grant_opportunities")
        }
        if {"canonical_title", "opportunity_type"} <= columns:
            for title, keep in _KEEP_TYPE.items():
                rows = conn.execute(
                    sa.text(
                        "SELECT id, opportunity_type, official_source "
                        "FROM grant_opportunities WHERE canonical_title = :t"
                    ),
                    {"t": title},
                ).fetchall()
                if len(rows) < 2:
                    print(f"[{revision}] {title}: {len(rows)} row(s), nothing to merge")
                    continue

                survivors = [r for r in rows if r[1] == keep]
                losers = [r for r in rows if r[1] != keep]
                if not survivors:
                    # The type to keep is not present. Reported, not forced:
                    # picking a different survivor would be a decision nobody
                    # took.
                    print(
                        f"[{revision}] {title}: no row typed {keep!r}; "
                        f"found {[r[1] for r in rows]}. Left alone."
                    )
                    continue

                survivor = survivors[0]
                # Inherit a source the survivor lacks. A duplicate is still a
                # row someone sourced.
                if "official_source" in columns and not survivor[2]:
                    donor = next((r[2] for r in losers if r[2]), None)
                    if donor:
                        conn.execute(
                            sa.text(
                                "UPDATE grant_opportunities SET official_source = :s "
                                "WHERE id = :i"
                            ),
                            {"s": donor, "i": survivor[0]},
                        )
                        print(f"[{revision}] {title}: inherited official_source")

                removed = conn.execute(
                    sa.text(
                        "DELETE FROM grant_opportunities "
                        "WHERE canonical_title = :t AND opportunity_type <> :keep"
                    ),
                    {"t": title, "keep": keep},
                )
                print(
                    f"[{revision}] {title}: kept {keep}, removed "
                    f"{removed.rowcount or 0} duplicate(s)"
                )

    # ── 2 & 3. Mappings that cannot resolve ─────────────────────────────────
    if _has(conn, "grant_legacy_id_map"):
        # Expanding bindparam rather than ANY(:ids): ANY is Postgres-only and
        # every migration test in this suite runs the revision against SQLite.
        dead = conn.execute(
            sa.text("DELETE FROM grant_legacy_id_map WHERE legacy_id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": list(_DEAD_TARGETS)},
        )
        print(f"[{revision}] mappings with no live target retired: {dead.rowcount or 0}")
        stale = conn.execute(
            sa.text(
                "DELETE FROM grant_legacy_id_map WHERE resolved_live_id IN :ids"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": list(_STALE_LIVE_IDS)},
        )
        print(f"[{revision}] mappings naming a departed row deleted: {stale.rowcount or 0}")

    # ── 4. Rejected splits ───────────────────────────────────────────────────
    #
    # Driven off the ledger rather than a hardcoded list, so this settles
    # exactly the claims a reviewer rejected and nothing else.
    if _has(conn, "grant_split_children") and _has(conn, "source_verifications"):
        rejected = [
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT DISTINCT subject_id FROM source_verifications "
                    "WHERE gate = 'GRANTS_SPLIT_PARENT' AND review_state = 'REJECTED'"
                )
            )
        ]
        if rejected:
            removed = conn.execute(
                sa.text(
                    "DELETE FROM grant_split_children WHERE parent_id IN :ids"
                ).bindparams(sa.bindparam("ids", expanding=True)),
                {"ids": rejected},
            )
            print(
                f"[{revision}] rejected splits: {len(rejected)} parent(s), "
                f"{removed.rowcount or 0} child row(s) removed"
            )
        else:
            print(f"[{revision}] no rejected split claims in the ledger")


def downgrade() -> None:
    # Not reversible. The duplicate rows and mapping entries are deleted, and
    # recreating them would mean inventing the columns this migration did not
    # read. Their content is in the frozen master and in the reconcile output,
    # which is where a restore would come from.
    pass
