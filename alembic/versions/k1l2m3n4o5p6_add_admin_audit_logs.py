"""add admin_audit_logs table

Every admin mutation is recorded here by AuditedAPIRoute (app/core/audit.py).
The table is append-only in the application: nothing updates or deletes a row
except the retention purge, which drops rows older than
ADMIN_AUDIT_RETENTION_DAYS.

Indexes are chosen for the admin audit reader's filters: actor, action,
resource (type + id) and the time-ordered default listing.

Revision ID: k1l2m3n4o5p6
Revises: j0e1f2a3b4c5
Create Date: 2026-08-06 00:00:00.000000
"""
from alembic import op

revision = "k1l2m3n4o5p6"
down_revision = "j0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_logs (
            id TEXT NOT NULL,
            actor_id TEXT,
            actor_email TEXT,
            actor_role TEXT,
            action TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT,
            before_json JSONB,
            after_json JSONB,
            method TEXT,
            path TEXT,
            status_code INTEGER,
            ip_address TEXT,
            user_agent TEXT,
            error_message TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_created_at "
        "ON admin_audit_logs (created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_actor_id "
        "ON admin_audit_logs (actor_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_action "
        "ON admin_audit_logs (action)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_resource "
        "ON admin_audit_logs (resource_type, resource_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_status_code "
        "ON admin_audit_logs (status_code)"
    )


def downgrade() -> None:
    # Dropping this table destroys the only record of who changed what. Kept as
    # a real downgrade because Alembic requires one, but note that the data is
    # not recoverable afterwards.
    op.execute("DROP INDEX IF EXISTS ix_admin_audit_logs_status_code")
    op.execute("DROP INDEX IF EXISTS ix_admin_audit_logs_resource")
    op.execute("DROP INDEX IF EXISTS ix_admin_audit_logs_action")
    op.execute("DROP INDEX IF EXISTS ix_admin_audit_logs_actor_id")
    op.execute("DROP INDEX IF EXISTS ix_admin_audit_logs_created_at")
    op.drop_table("admin_audit_logs")
