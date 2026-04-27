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
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = {c["name"] for c in inspector.get_columns("comparable_productions")}
    existing_idx = {i["name"] for i in inspector.get_indexes("comparable_productions")}
    if "tmdb_id" not in existing_cols:
        op.add_column('comparable_productions', sa.Column('tmdb_id', sa.String(), nullable=True))
    if "source" not in existing_cols:
        op.add_column('comparable_productions', sa.Column('source', sa.String(), nullable=False, server_default='Manual'))
    if "ix_comparable_productions_tmdb_id" not in existing_idx:
        op.create_index('ix_comparable_productions_tmdb_id', 'comparable_productions', ['tmdb_id'])


def downgrade() -> None:
    op.drop_index('ix_comparable_productions_tmdb_id', table_name='comparable_productions')
    op.drop_column('comparable_productions', 'source')
    op.drop_column('comparable_productions', 'tmdb_id')
