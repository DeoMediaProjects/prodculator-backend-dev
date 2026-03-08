"""add_downloaded_to_reports

Revision ID: ea9a4518f4d3
Revises: 1a3a25b27633
Create Date: 2026-03-08 10:25:43.400014
"""
from alembic import op
import sqlalchemy as sa

revision = 'ea9a4518f4d3'
down_revision = '1a3a25b27633'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('reports', sa.Column('downloaded', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    op.drop_column('reports', 'downloaded')
