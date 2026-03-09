"""create email_gating_records table

Revision ID: 1a3a25b27633
Revises: f1a2b3c4d5e6
Create Date: 2026-03-08 09:48:04.213611
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

revision = '1a3a25b27633'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('email_gating_records',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('report_generated', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('blocked', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_email_gating_records_email'), 'email_gating_records', ['email'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_gating_records_email'), table_name='email_gating_records')
    op.drop_table('email_gating_records')
