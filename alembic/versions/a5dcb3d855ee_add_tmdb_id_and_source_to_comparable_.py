"""add_tmdb_id_and_source_to_comparable_productions

Revision ID: a5dcb3d855ee
Revises: fe6e41788a05
Create Date: 2026-03-07 10:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'a5dcb3d855ee'
down_revision = 'fe6e41788a05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('comparable_productions', sa.Column('tmdb_id', sa.String(), nullable=True))
    op.add_column('comparable_productions', sa.Column('source', sa.String(), nullable=False, server_default='Manual'))
    op.create_index('ix_comparable_productions_tmdb_id', 'comparable_productions', ['tmdb_id'])


def downgrade() -> None:
    op.drop_index('ix_comparable_productions_tmdb_id', table_name='comparable_productions')
    op.drop_column('comparable_productions', 'source')
    op.drop_column('comparable_productions', 'tmdb_id')
