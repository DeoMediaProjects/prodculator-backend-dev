"""add b2b package templates

Saved section compositions for the admin package composer (SOW 4.4: "mix and
match sections ... save as template"). PRODUCT_TEMPLATES in package_service.py
stays the canonical layout for the five standard products; this table holds the
bespoke compositions an admin builds by hand so they can be reloaded later.

section_keys is an ordered JSON list of SECTION_LIBRARY keys. Keys are NOT
foreign-keyed to anything: the library is code, not data, so a saved template
can reference a key that a later deploy removes. The composer resolves keys
against the live library on load and reports unknown ones rather than failing,
which is why validation lives in the app and not in a constraint here.

Revision ID: f6a7b8c9d0e1
Revises: d1e2f3a4b5c6
Create Date: 2026-07-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "b2b_package_templates" not in set(inspector.get_table_names()):
        op.create_table(
            "b2b_package_templates",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            # Ordered list of SECTION_LIBRARY keys. Order is the render order.
            sa.Column("section_keys", sa.JSON(), nullable=False),
            # Optional: pins the template to one standard product for filtering.
            sa.Column("product_type", sa.String(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        # Names are how an admin identifies a template in the composer dropdown,
        # so they must be unique or the list becomes ambiguous.
        op.create_index(
            "ix_b2b_package_templates_name",
            "b2b_package_templates",
            ["name"],
            unique=True,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "b2b_package_templates" in set(inspector.get_table_names()):
        existing = {ix["name"] for ix in inspector.get_indexes("b2b_package_templates")}
        if "ix_b2b_package_templates_name" in existing:
            op.drop_index("ix_b2b_package_templates_name", table_name="b2b_package_templates")
        op.drop_table("b2b_package_templates")
