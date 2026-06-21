"""create contact messages table

Revision ID: c1d2e3f4a5b6
Revises: 4c5f82471e4f
Create Date: 2026-06-20 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "c1d2e3f4a5b6"
down_revision = "4c5f82471e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_messages",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("company", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("category", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("subject", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("page_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("internal_email_sent", sa.Boolean(), nullable=False),
        sa.Column("auto_reply_sent", sa.Boolean(), nullable=False),
        sa.Column("email_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contact_messages_email"), "contact_messages", ["email"], unique=False)
    op.create_index(op.f("ix_contact_messages_category"), "contact_messages", ["category"], unique=False)
    op.create_index(op.f("ix_contact_messages_status"), "contact_messages", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_contact_messages_status"), table_name="contact_messages")
    op.drop_index(op.f("ix_contact_messages_category"), table_name="contact_messages")
    op.drop_index(op.f("ix_contact_messages_email"), table_name="contact_messages")
    op.drop_table("contact_messages")
