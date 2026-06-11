"""rename sendgrid data source to brevo

Migrates the seeded ``data_sources`` row from the legacy SendGrid integration to
Brevo, which now backs transactional email delivery. Keeps the row's id intact so
existing references and test-result history are preserved.

Revision ID: t5u6v7w8x9y0
Revises: s4t5u6v7w8x9
Create Date: 2026-06-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "t5u6v7w8x9y0"
down_revision = "s4t5u6v7w8x9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE data_sources "
            "SET slug = 'brevo', "
            "name = 'Brevo Email', "
            "description = 'Transactional email delivery for notifications and alerts' "
            "WHERE slug = 'sendgrid'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE data_sources "
            "SET slug = 'sendgrid', "
            "name = 'SendGrid Email', "
            "description = 'Transactional email delivery for notifications and alerts' "
            "WHERE slug = 'brevo'"
        )
    )
