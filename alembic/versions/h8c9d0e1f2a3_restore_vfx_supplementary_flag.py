"""restore_vfx_supplementary_flag

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-08-04

PROD-FIX-007 — root cause of the Lion King UK rate mismatch.

Migration j1k2l3m4n5o6 set is_supplementary = true on the UK VFX Expenditure
Credit so it could never be selected as a territory's primary incentive:

    "Without this flag, when IFTC is capped out (budget > GBP23.5M), the
     cap-switching logic selected VFX Credit (39% > AVEC 34%) as the primary
     incentive and applied it to the full production budget — producing an
     overstated rebate figure and a mislabelled report section."

Migration ab2c3d4e5f61 (incentives v4 refresh) then did

    conn.execute(table.delete())
    conn.execute(table.insert(), rows)

and its row builder never sets is_supplementary, so the flag was silently
reset. The guard in ReportValidator._compute_corrected_rebate

    and not r.get("is_supplementary")

became dead code, and the exact failure j1k2l3m4n5o6 describes reappeared —
which is what the Lion King report shows: a 39% / 29.25% VFX rate presented as
the whole-budget UK recommendation.

This restores that single flag. It is safe because the UK carries three
programmes, two of which remain primary (AVEC and Enhanced/IFTC), so excluding
the VFX credit from primary selection leaves the territory fully rankable.

──────────────────────────────────────────────────────────────────────────────
DELIBERATELY NOT RESTORED HERE — see docs, needs data-team review

The v4 refresh dropped 18 engine columns in total, not just this one:

    is_supplementary          cap_basis            rate_tier_json
    qualifying_spend_labour_pct                    applicable_formats
    payee_note                filing_note          eligibility_rules_json
    nationality_requirements  co_production_eligible
    co_production_treaties    spv_eligible         parent_territory
    stacking_group            stackable_with       scope
    cultural_test_required    admin_complexity

Replaying the migration chain to the revision immediately before the refresh
recovers the prior values, but they cannot be mapped back mechanically: the
refresh also changed the programme SET, not only the columns. Six territories
were dropped (Scotland, Wales, Northern Ireland, Bavaria, New South Wales,
South Africa-national), roughly 24 programmes were renamed, and British
Columbia's two rows (FIBC + PSTC) were merged into one.

Ten further rows were is_supplementary = true before the refresh. None can be
restored safely:

  * Scotland, Wales, Northern Ireland, Bavaria, New South Wales — territory no
    longer exists in the dataset; nothing to restore onto.
  * Ontario, Quebec, Alberta, British Columbia, Western Cape — each now has
    exactly ONE programme. ReportBuilder._is_supplementary_only_territory drops
    a territory from the report entirely when all its programmes are
    supplementary, so setting the flag would silently remove five territories
    from every report. That is a product decision, not a data repair.

rate_tier_json is the highest-value remaining gap: 23 rows carried tier data,
and without it ReportValidator cannot blend tiered rates (Spain, Canary
Islands, Portugal, Ireland, UK IFTC taper), so those territories are modelled
at their first-tier rate only. That warrants its own reviewed restoration pass.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h8c9d0e1f2a3"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None

_TERRITORY = "United Kingdom"
_PROGRAM = "UK VFX Expenditure Credit (Uplift)"


def upgrade() -> None:
    conn = op.get_bind()

    # The v4 refresh renamed this row from "VFX Expenditure Credit (Uplift)" to
    # "UK VFX Expenditure Credit (Uplift)", which is why re-running
    # j1k2l3m4n5o6 would no longer match it.
    result = conn.execute(
        sa.text("""
            UPDATE incentive_programs
            SET is_supplementary = true
            WHERE territory = :territory
              AND program = :program
        """),
        {"territory": _TERRITORY, "program": _PROGRAM},
    )

    assert result.rowcount == 1, (
        f"expected exactly 1 row for {_TERRITORY} / {_PROGRAM}, "
        f"updated {result.rowcount} — has the programme been renamed again?"
    )

    # The territory must retain at least one selectable primary programme,
    # otherwise this flag would remove the UK from reports altogether.
    primary = conn.execute(
        sa.text("""
            SELECT COUNT(*) FROM incentive_programs
            WHERE territory = :territory
              AND status = 'active'
              AND (is_supplementary IS NULL OR is_supplementary = false)
        """),
        {"territory": _TERRITORY},
    ).scalar()
    assert primary and primary >= 1, (
        f"{_TERRITORY} has no primary programme left after flagging "
        f"{_PROGRAM} as supplementary"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            UPDATE incentive_programs
            SET is_supplementary = false
            WHERE territory = :territory
              AND program = :program
        """),
        {"territory": _TERRITORY, "program": _PROGRAM},
    )
