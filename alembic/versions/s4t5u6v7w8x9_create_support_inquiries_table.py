"""create support inquiries table

Revision ID: s4t5u6v7w8x9
Revises: 07b18434139d
Create Date: 2026-06-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "s4t5u6v7w8x9"
down_revision = "07b18434139d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_inquiries",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("company", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("plan", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("category", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("selected_faq_question", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("selected_faq_answer", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("page_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("internal_email_sent", sa.Boolean(), nullable=False),
        sa.Column("auto_reply_sent", sa.Boolean(), nullable=False),
        sa.Column("email_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_support_inquiries_user_id"), "support_inquiries", ["user_id"], unique=False)
    op.create_index(op.f("ix_support_inquiries_user_email"), "support_inquiries", ["user_email"], unique=False)
    op.create_index(op.f("ix_support_inquiries_category"), "support_inquiries", ["category"], unique=False)
    op.create_index(op.f("ix_support_inquiries_status"), "support_inquiries", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_support_inquiries_status"), table_name="support_inquiries")
    op.drop_index(op.f("ix_support_inquiries_category"), table_name="support_inquiries")
    op.drop_index(op.f("ix_support_inquiries_user_email"), table_name="support_inquiries")
    op.drop_index(op.f("ix_support_inquiries_user_id"), table_name="support_inquiries")
    op.drop_table("support_inquiries")
