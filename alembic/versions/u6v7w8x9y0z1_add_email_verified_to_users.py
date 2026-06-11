"""add email_verified to users

Adds an ``email_verified`` flag to the ``users`` table so the email-verification
magic link is actually enforced at sign-in. New accounts default to ``False`` and
are flipped to ``True`` only when the verification link is used (or immediately for
OAuth sign-ups, where the provider has already verified the address).

Existing accounts are backfilled to ``True`` so this change does not lock out users
who registered before verification was enforced.

Revision ID: u6v7w8x9y0z1
Revises: t5u6v7w8x9y0
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "u6v7w8x9y0z1"
down_revision = "t5u6v7w8x9y0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Grandfather every pre-existing account as verified so enforcement does not
    # lock out users created before this column existed.
    op.execute(sa.text("UPDATE users SET email_verified = TRUE"))


def downgrade() -> None:
    op.drop_column("users", "email_verified")
