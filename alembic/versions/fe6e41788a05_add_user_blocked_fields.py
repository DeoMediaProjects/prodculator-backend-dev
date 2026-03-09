"""add_user_blocked_fields

Revision ID: fe6e41788a05
Revises: d738ccebf985
Create Date: 2026-03-07 09:54:57.076524
"""
from alembic import op
import sqlalchemy as sa

revision = 'fe6e41788a05'
down_revision = 'd738ccebf985'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('is_blocked', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('users', sa.Column('blocked_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'blocked_at')
    op.drop_column('users', 'is_blocked')
