"""add b2b client entitlements

The entitlement registry records what each B2B client is contractually owed and,
critically, which modules are licensed to them EXCLUSIVELY and until when.

Example from the contract pack: Grey Consortium UK holds the "AI Usage Module"
exclusively with a reversion date of 2028-06-30. Until that date no other
client's package may include the sections that module covers; after it, the
module becomes generally available.

`section_keys` maps a module onto concrete package_service SECTION_LIBRARY keys.
It may be empty for a module that is contracted but not yet built -- the row
still records the obligation, it just has nothing to enforce against yet.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "b2b_client_entitlements" not in set(inspector.get_table_names()):
        op.create_table(
            "b2b_client_entitlements",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("b2b_subscription_id", sa.String(), nullable=False),
            sa.Column("module_key", sa.String(), nullable=False),
            sa.Column("module_label", sa.String(), nullable=True),
            sa.Column("section_keys", sa.JSON(), nullable=True),
            sa.Column("is_exclusive", sa.Boolean(), nullable=False, server_default=sa.false()),
            # NULL reverts_at on an exclusive module means perpetual exclusivity.
            sa.Column("reverts_at", sa.Date(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("b2b_client_entitlements")}
    if "uq_b2b_client_entitlements_subscription_module" not in indexes:
        op.create_index(
            "uq_b2b_client_entitlements_subscription_module",
            "b2b_client_entitlements",
            ["b2b_subscription_id", "module_key"],
            unique=True,
        )
    if "ix_b2b_client_entitlements_module_key" not in indexes:
        op.create_index(
            "ix_b2b_client_entitlements_module_key",
            "b2b_client_entitlements",
            ["module_key"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "b2b_client_entitlements" in set(inspector.get_table_names()):
        for idx in inspector.get_indexes("b2b_client_entitlements"):
            op.drop_index(idx["name"], table_name="b2b_client_entitlements")
        op.drop_table("b2b_client_entitlements")
