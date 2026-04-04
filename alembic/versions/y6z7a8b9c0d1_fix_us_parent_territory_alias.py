"""fix_us_parent_territory_alias

Revision ID: y6z7a8b9c0d1
Revises: x5y6z7a8b9c0
Create Date: 2026-03-23

The US state incentive rows (California, New York, Louisiana, Illinois) were
seeded with parent_territory = 'USA' (an alias) rather than 'United States'
(the canonical label from the Territory enum). This caused the builder's
child-territory expansion to fail silently when a user submitted
"United States" — the parent lookup compared 'USA' != 'United States' and
found no children, so the US was dropped from the report entirely.

The builder code has been updated to resolve aliases, but the DB should also
use the canonical label for consistency with all other territories (Australia,
Canada, Spain, Germany all use their canonical labels).

Also fixes Georgia (USA) which was seeded with territory 'Georgia' in the
earlier migration o9p0q1r2s3t4 but the canonical label is 'Georgia (USA)'.
"""
from alembic import op
import sqlalchemy as sa

revision = "y6z7a8b9c0d1"
down_revision = "x5y6z7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Fix parent_territory 'USA' → 'United States' for all US state rows
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET parent_territory = 'United States' "
            "WHERE parent_territory = 'USA'"
        )
    )

    # Also normalise any 'Georgia' territory to 'Georgia (USA)' to match
    # the canonical Territory enum label and avoid confusion with the
    # country Georgia (Caucasus).
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET territory = 'Georgia (USA)' "
            "WHERE territory = 'Georgia' "
            "  AND parent_territory = 'United States'"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET parent_territory = 'USA' "
            "WHERE parent_territory = 'United States' "
            "  AND scope = 'regional' "
            "  AND territory IN ('California', 'New York', 'Louisiana', 'Illinois', 'Georgia (USA)', 'New Mexico')"
        )
    )

    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET territory = 'Georgia' "
            "WHERE territory = 'Georgia (USA)'"
        )
    )
