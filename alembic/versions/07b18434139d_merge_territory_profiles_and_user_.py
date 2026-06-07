"""merge territory_profiles and user_billing_geo heads

Revision ID: 07b18434139d
Revises: i2j3k4l5m6n7, z8b9c0d1e2f3
Create Date: 2026-06-07 16:47:10.033525
"""
from alembic import op
import sqlalchemy as sa


revision = '07b18434139d'
down_revision = ('i2j3k4l5m6n7', 'z8b9c0d1e2f3')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
