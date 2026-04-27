"""add_role_and_last_login_to_admins

Revision ID: b7e2f1a9c3d4
Revises: ea9a4518f4d3
Create Date: 2026-03-08 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7e2f1a9c3d4'
down_revision = 'ea9a4518f4d3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_cols = {c["name"] for c in inspector.get_columns("admins")}
    if "role" not in existing_cols:
        op.add_column('admins', sa.Column('role', sa.String(), nullable=False, server_default='master_admin'))
    if "last_login" not in existing_cols:
        op.add_column('admins', sa.Column('last_login', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('admins', 'last_login')
    op.drop_column('admins', 'role')
