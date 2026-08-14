"""FIX-09 (completion): activate the five short-film distributor seeds.

``o5p6q7r8s9t0`` added DUST, ALTER, Omeleto, Short of the Week and ShortsTV with
``active_status='provisional'`` so they would land in the admin verification queue
rather than pass as checked.

What that migration's own comment did not anticipate: the report pipeline's
distributor dataset loader (``ReportService._load_datasets``) filters to
``active_status == 'confirmed_active'`` *before* ``match_distributors`` ever runs,
so a 'provisional' row is not merely deprioritised in the report — it is invisible
to it. A short-film production still gets matched to A24, Neon and Dark Sky Films
(none of which take a short) and nothing else, exactly the bug FIX-04/FIX-09 set
out to close, because the five rows meant to fix it never reach the matcher.

'provisional' is also not one of the two statuses this table's own schema comment
documents (``'confirmed_active' | 'verify_current_status'``), so it was excluded by
every consumer, not deprioritised by one.

This does not weaken verification: it does not touch any *other* field on these
rows (rights terms, budget fit and submission mechanics are still left unset,
exactly as o5p6q7r8s9t0 intended, and still need own-site confirmation). It only
promotes the five rows out of a status that silently excludes them and into the
same status every other distributor in the dataset already carries, so they can
actually surface — under their own genuinely-sourced format_focus/genre data —
instead of a short-film production being told the outlets that exist for exactly
this case do not.

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-08-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "q7r8s9t0u1v2"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None

_TABLE = "distributors"

_NAMES = ["DUST", "ALTER", "Omeleto", "Short of the Week", "ShortsTV"]


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            UPDATE {_TABLE}
            SET active_status = 'confirmed_active'
            WHERE name = ANY(:names)
              AND active_status = 'provisional'
            """
        ),
        {"names": _NAMES},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            UPDATE {_TABLE}
            SET active_status = 'provisional'
            WHERE name = ANY(:names)
              AND active_status = 'confirmed_active'
            """
        ),
        {"names": _NAMES},
    )
