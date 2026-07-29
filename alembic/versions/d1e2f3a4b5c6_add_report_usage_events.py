"""add report_usage_events so deleting a report cannot refund quota

Report quota was counted by querying surviving rows in ``reports``. Deleting a
report therefore returned the slot, so a user could generate a report, download
the PDF, delete it, and repeat indefinitely on a one-report plan.

Quota consumption is now recorded in its own append-only ledger. Deletion stays
a real deletion of the user's script-derived content (soft-deleting the report
would retain that content indefinitely, which contradicts what deletion means
here), while a minimal, non-identifying usage record survives.

There is deliberately NO foreign key to ``reports``: an ON DELETE CASCADE would
reintroduce exactly the bug this fixes, and ON DELETE SET NULL would still make
the ledger's survival depend on a constraint being declared correctly forever.

Revision ID: d1e2f3a4b5c6
Revises: e5f6a7b8c9d0
Create Date: 2026-07-29 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd1e2f3a4b5c6'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


# Backfill one event per existing chargeable report so the migration is quota
# neutral. Without this every current subscriber would wake up with a full
# allowance again, which is the same leak from the other direction.
BACKFILL_SQL = """
INSERT INTO report_usage_events (id, user_id, report_id, report_type, created_at, voided_at)
SELECT gen_random_uuid(), r.user_id, r.id, COALESCE(r.report_type, 'paid'), r.created_at, NULL
FROM reports r
WHERE r.status IS DISTINCT FROM 'failed'
  AND r.user_id IS NOT NULL
"""


def upgrade() -> None:
    op.create_table(
        'report_usage_events',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=False), nullable=False),
        # Kept for support and reconciliation only. The row stays valid, and keeps
        # counting, after the report it refers to is gone.
        sa.Column('report_id', postgresql.UUID(as_uuid=False), nullable=True),
        # 'free' distinguishes the lifetime trial from period-limited usage.
        sa.Column('report_type', sa.Text(), nullable=False, server_default='paid'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        # Set when a report fails, so an outage does not consume a user's slot.
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Period counting filters by user and created_at; the trial check filters by
    # user and report_type.
    op.create_index('ix_report_usage_events_user_created',
                    'report_usage_events', ['user_id', 'created_at'])
    op.create_index('ix_report_usage_events_user_type',
                    'report_usage_events', ['user_id', 'report_type'])
    # One ledger row per report, so a retried write cannot double charge.
    op.create_index('ix_report_usage_events_report_id',
                    'report_usage_events', ['report_id'], unique=True,
                    postgresql_where=sa.text('report_id IS NOT NULL'))

    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_index('ix_report_usage_events_report_id', table_name='report_usage_events')
    op.drop_index('ix_report_usage_events_user_type', table_name='report_usage_events')
    op.drop_index('ix_report_usage_events_user_created', table_name='report_usage_events')
    op.drop_table('report_usage_events')
