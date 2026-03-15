"""create_territory_weather_table

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-03-13 10:10:00.000000

Creates the territory_weather table storing per-territory per-month climate
data so the report can score weather risk against actual shoot dates.
"""

from alembic import op
import sqlalchemy as sa

revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "territory_weather" in inspector.get_table_names():
        return

    op.create_table(
        "territory_weather",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("territory", sa.Text(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("avg_temp_high_c", sa.Float(), nullable=True),
        sa.Column("avg_temp_low_c", sa.Float(), nullable=True),
        sa.Column("avg_rainfall_mm", sa.Float(), nullable=True),
        sa.Column("avg_daylight_hours", sa.Float(), nullable=True),
        sa.Column("storm_risk", sa.Text(), nullable=True),
        sa.Column("weather_notes", sa.Text(), nullable=True),
        sa.Column("exterior_shoot_score", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column(
            "last_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Unique constraint: one row per territory + month
    op.create_index(
        "ix_territory_weather_territory_month",
        "territory_weather",
        ["territory", "month"],
        unique=True,
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "territory_weather" not in inspector.get_table_names():
        return

    op.drop_index(
        "ix_territory_weather_territory_month",
        table_name="territory_weather",
    )
    op.drop_table("territory_weather")
