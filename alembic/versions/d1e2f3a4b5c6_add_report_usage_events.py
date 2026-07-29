"""add report_usage_events so deleting a report cannot refund quota

Report quota was counted by querying surviving rows in ``reports``. Deleting a
report therefore returned the slot, so a user could generate a report, download
the PDF, delete it, and repeat indefinitely on a one-report plan.

Quota consumption is now recorded in an append-only ledger. Deletion stays a
real deletion of the user's script-derived content (soft-deleting the report
would retain that content indefinitely, which contradicts what deletion means
here), while a minimal, non-identifying usage record survives.

There is deliberately NO foreign key to ``reports``: an ON DELETE CASCADE would
reintroduce exactly the bug this fixes, and ON DELETE SET NULL would still make
the ledger's survival depend on a constraint being declared correctly forever.

Identifier columns are String, not UUID, to match the rest of this schema —
``reports.id``, ``reports.user_id`` and ``users.id`` are all character varying.
A UUID column here fails the backfill with a datatype mismatch.

Revision ID: d1e2f3a4b5c6
Revises: e5f6a7b8c9d0
Create Date: 2026-07-29 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd1e2f3a4b5c6'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


# Backfill one event per existing chargeable report so the migration is quota
# neutral. Without this every current subscriber would wake up with a full
# allowance again, which is the same leak from the other direction.
BACKFILL_SQL = """
INSERT INTO report_usage_events (id, user_id, report_id, report_type, created_at, voided_at)
SELECT gen_random_uuid()::text, r.user_id, r.id, COALESCE(r.report_type, 'paid'), r.created_at, NULL
FROM reports r
WHERE r.status IS DISTINCT FROM 'failed'
  AND r.user_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM report_usage_events e WHERE e.report_id = r.id
  )
"""


def upgrade() -> None:
    # Guarded like the neighbouring b2b migrations so a re-run after a partial
    # failure is safe. Alembic did not roll back the CREATE TABLE when the
    # original backfill failed, which left the table present while the version
    # stayed behind, and an unguarded re-run then died on "already exists".
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())

    if 'report_usage_events' not in existing:
        op.create_table(
            'report_usage_events',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('user_id', sa.String(), nullable=False),
            # Kept for support and reconciliation only. The row stays valid, and
            # keeps counting, after the report it refers to is gone.
            sa.Column('report_id', sa.String(), nullable=True),
            # 'free' distinguishes the lifetime trial from period-limited usage.
            sa.Column('report_type', sa.String(), nullable=False, server_default='paid'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text('now()')),
            # Set when a report fails, so an outage does not consume a user's slot.
            sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        )

    indexes = {ix['name'] for ix in inspector.get_indexes('report_usage_events')} \
        if 'report_usage_events' in existing else set()

    # Period counting filters by user and created_at; the trial check filters by
    # user and report_type.
    if 'ix_report_usage_events_user_created' not in indexes:
        op.create_index('ix_report_usage_events_user_created',
                        'report_usage_events', ['user_id', 'created_at'])
    if 'ix_report_usage_events_user_type' not in indexes:
        op.create_index('ix_report_usage_events_user_type',
                        'report_usage_events', ['user_id', 'report_type'])
    # One ledger row per report, so a retried write cannot double charge.
    if 'ix_report_usage_events_report_id' not in indexes:
        op.create_index('ix_report_usage_events_report_id',
                        'report_usage_events', ['report_id'], unique=True,
                        postgresql_where=sa.text('report_id IS NOT NULL'))

    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'report_usage_events' in set(inspector.get_table_names()):
        op.drop_table('report_usage_events')
