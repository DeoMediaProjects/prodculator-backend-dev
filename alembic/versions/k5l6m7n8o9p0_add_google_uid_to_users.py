"""add_google_uid_to_users

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-03-11 21:36:02.000000

Adds a nullable ``google_uid`` column to the ``users`` table so that
Google / Firebase OAuth sign-ins can be tracked per user.
A unique partial index is created so that no two users share the same
Google UID (while still allowing NULL for password-only accounts).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "k5l6m7n8o9p0"
down_revision = "j4k5l6m7n8o9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_cols = {c["name"] for c in inspector.get_columns("users")}
    if "google_uid" not in existing_cols:
        op.add_column(
            "users",
            sa.Column("google_uid", sa.String(), nullable=True),
        )

    # Unique index that ignores NULLs (only enforced when google_uid IS NOT NULL).
    # SQLite and PostgreSQL both support this pattern.
    existing_idx = {i["name"] for i in inspector.get_indexes("users")}
    if "ix_users_google_uid" not in existing_idx:
        op.create_index(
            "ix_users_google_uid",
            "users",
            ["google_uid"],
            unique=True,
            postgresql_where=sa.text("google_uid IS NOT NULL"),
            sqlite_where=sa.text("google_uid IS NOT NULL"),
        )


def downgrade() -> None:
    op.drop_index("ix_users_google_uid", table_name="users")
    op.drop_column("users", "google_uid")
