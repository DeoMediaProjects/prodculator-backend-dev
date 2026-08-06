"""add b2b_contract_invites table

Token-minted invitations to claim a manually-contracted Business Intelligence
subscription (handoff §4.3/§4.4). Only the SHA-256 of the token is stored, so a
database leak yields no usable invite links.

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-08-06 00:00:00.000000
"""
from alembic import op

revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS b2b_contract_invites (
            id TEXT NOT NULL,
            email TEXT NOT NULL,
            product_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            token_hash TEXT NOT NULL,
            token_prefix TEXT,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            delivery_frequency TEXT NOT NULL DEFAULT 'monthly',
            extra_recipient_email TEXT,
            company_name TEXT,
            admin_notes TEXT,
            created_by TEXT,
            sent_count INTEGER NOT NULL DEFAULT 0,
            last_sent_at TIMESTAMP WITH TIME ZONE,
            accepted_at TIMESTAMP WITH TIME ZONE,
            accepted_by_user_id TEXT,
            b2b_subscription_id TEXT,
            revoked_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (id)
        )
    """)
    # Unique: the token hash is the lookup key on the public accept path, and a
    # collision must never resolve to two invites.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_b2b_contract_invites_token_hash "
        "ON b2b_contract_invites (token_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_b2b_contract_invites_email "
        "ON b2b_contract_invites (email)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_b2b_contract_invites_status "
        "ON b2b_contract_invites (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_b2b_contract_invites_subscription "
        "ON b2b_contract_invites (b2b_subscription_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_b2b_contract_invites_subscription")
    op.execute("DROP INDEX IF EXISTS ix_b2b_contract_invites_status")
    op.execute("DROP INDEX IF EXISTS ix_b2b_contract_invites_email")
    op.execute("DROP INDEX IF EXISTS ix_b2b_contract_invites_token_hash")
    op.drop_table("b2b_contract_invites")
