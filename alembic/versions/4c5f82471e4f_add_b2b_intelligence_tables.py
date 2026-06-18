"""add b2b intelligence tables

Revision ID: 4c5f82471e4f
Revises: u6v7w8x9y0z1
Create Date: 2026-06-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "4c5f82471e4f"
down_revision = "u6v7w8x9y0z1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "b2b_subscriptions" not in tables:
        op.create_table(
            "b2b_subscriptions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("product_type", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("source", sa.String(), nullable=False, server_default="stripe"),
            sa.Column("stripe_customer_id", sa.String(), nullable=True),
            sa.Column("stripe_subscription_id", sa.String(), nullable=True),
            sa.Column("price_id", sa.String(), nullable=True),
            sa.Column("amount_cents", sa.Integer(), nullable=True),
            sa.Column("currency", sa.String(), nullable=True),
            sa.Column("delivery_frequency", sa.String(), nullable=False, server_default="monthly"),
            sa.Column("extra_recipient_email", sa.String(), nullable=True),
            sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_delivery_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("company_name", sa.String(), nullable=True),
            sa.Column("admin_notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if "b2b_intelligence_requests" not in tables:
        op.create_table(
            "b2b_intelligence_requests",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("b2b_subscription_id", sa.String(), nullable=True),
            sa.Column("product_type", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="processing"),
            sa.Column("request_type", sa.String(), nullable=False, server_default="on_demand"),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("recipient_email", sa.String(), nullable=False),
            sa.Column("extra_recipient_email", sa.String(), nullable=True),
            sa.Column("pdf_url", sa.String(), nullable=True),
            sa.Column("metrics", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("b2b_subscriptions")}
    if "ix_b2b_subscriptions_user_id" not in indexes:
        op.create_index("ix_b2b_subscriptions_user_id", "b2b_subscriptions", ["user_id"])
    if "ix_b2b_subscriptions_product_type" not in indexes:
        op.create_index("ix_b2b_subscriptions_product_type", "b2b_subscriptions", ["product_type"])
    if "ix_b2b_subscriptions_status" not in indexes:
        op.create_index("ix_b2b_subscriptions_status", "b2b_subscriptions", ["status"])
    if "ix_b2b_subscriptions_stripe_subscription_id" not in indexes:
        op.create_index(
            "ix_b2b_subscriptions_stripe_subscription_id",
            "b2b_subscriptions",
            ["stripe_subscription_id"],
            unique=True,
        )
    if "ix_b2b_subscriptions_next_delivery_at" not in indexes:
        op.create_index("ix_b2b_subscriptions_next_delivery_at", "b2b_subscriptions", ["next_delivery_at"])

    request_indexes = {idx["name"] for idx in inspector.get_indexes("b2b_intelligence_requests")}
    if "ix_b2b_intelligence_requests_user_id" not in request_indexes:
        op.create_index("ix_b2b_intelligence_requests_user_id", "b2b_intelligence_requests", ["user_id"])
    if "ix_b2b_intelligence_requests_b2b_subscription_id" not in request_indexes:
        op.create_index(
            "ix_b2b_intelligence_requests_b2b_subscription_id",
            "b2b_intelligence_requests",
            ["b2b_subscription_id"],
        )
    if "ix_b2b_intelligence_requests_product_type" not in request_indexes:
        op.create_index("ix_b2b_intelligence_requests_product_type", "b2b_intelligence_requests", ["product_type"])
    if "ix_b2b_intelligence_requests_status" not in request_indexes:
        op.create_index("ix_b2b_intelligence_requests_status", "b2b_intelligence_requests", ["status"])
    if "ix_b2b_intelligence_requests_period_start" not in request_indexes:
        op.create_index("ix_b2b_intelligence_requests_period_start", "b2b_intelligence_requests", ["period_start"])
    if "ix_b2b_intelligence_requests_period_end" not in request_indexes:
        op.create_index("ix_b2b_intelligence_requests_period_end", "b2b_intelligence_requests", ["period_end"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "b2b_intelligence_requests" in inspector.get_table_names():
        for idx in inspector.get_indexes("b2b_intelligence_requests"):
            op.drop_index(idx["name"], table_name="b2b_intelligence_requests")
        op.drop_table("b2b_intelligence_requests")

    if "b2b_subscriptions" in inspector.get_table_names():
        for idx in inspector.get_indexes("b2b_subscriptions"):
            op.drop_index(idx["name"], table_name="b2b_subscriptions")
        op.drop_table("b2b_subscriptions")

