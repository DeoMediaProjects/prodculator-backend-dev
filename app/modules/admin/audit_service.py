"""Reads over the admin audit trail (handoff §4.4/§4.5).

Queries go through SQLAlchemy directly rather than ``DatabaseClient``: the
filters this reader needs (OR across three columns for search, a status-class
range, DISTINCT facets) are outside the small table-query surface that client
exposes, and building them there would widen it for one caller.

Nothing in this module writes. The trail is append-only.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import distinct, func, or_, select

from app.core.database_client import DatabaseClient
from app.models.sql_models import AdminAuditLog

logger = logging.getLogger(__name__)

# Cap on facet values returned per dimension. A trail with thousands of distinct
# actions means the filter UI should be a search box, not a dropdown, so
# truncating here is the right failure mode.
FACET_LIMIT = 200


class AdminAuditService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase
        self.session = supabase.session

    # ── Serialisation ────────────────────────────────────────────────────────

    @staticmethod
    def _to_dict(row: AdminAuditLog) -> dict[str, Any]:
        return {
            "id": row.id,
            "actor_id": row.actor_id,
            "actor_email": row.actor_email,
            "actor_role": row.actor_role,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "before_json": row.before_json,
            "after_json": row.after_json,
            "method": row.method,
            "path": row.path,
            "status_code": row.status_code,
            "ip_address": row.ip_address,
            "user_agent": row.user_agent,
            "error_message": row.error_message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            # Derived for the UI so the classification is decided in one place.
            "succeeded": (
                None if row.status_code is None else row.status_code < 400
            ),
        }

    # ── Queries ──────────────────────────────────────────────────────────────

    def _filters(
        self,
        *,
        actor_id: str | None,
        actor_email: str | None,
        action: str | None,
        resource_type: str | None,
        resource_id: str | None,
        status: str | None,
        start: datetime | None,
        end: datetime | None,
        search: str | None,
    ) -> list[Any]:
        clauses: list[Any] = []
        if actor_id:
            clauses.append(AdminAuditLog.actor_id == actor_id)
        if actor_email:
            clauses.append(AdminAuditLog.actor_email.ilike(f"%{actor_email}%"))
        if action:
            clauses.append(AdminAuditLog.action == action)
        if resource_type:
            clauses.append(AdminAuditLog.resource_type == resource_type)
        if resource_id:
            clauses.append(AdminAuditLog.resource_id == resource_id)
        if status == "success":
            # A row with no status never completed a response; it is neither a
            # success nor a recorded failure, so it is excluded from both.
            clauses.append(AdminAuditLog.status_code < 400)
        elif status == "failed":
            clauses.append(AdminAuditLog.status_code >= 400)
        if start:
            clauses.append(AdminAuditLog.created_at >= start)
        if end:
            clauses.append(AdminAuditLog.created_at <= end)
        if search:
            pattern = f"%{search}%"
            clauses.append(or_(
                AdminAuditLog.path.ilike(pattern),
                AdminAuditLog.resource_id.ilike(pattern),
                AdminAuditLog.error_message.ilike(pattern),
                AdminAuditLog.action.ilike(pattern),
            ))
        return clauses

    def list_logs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        actor_id: str | None = None,
        actor_email: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        status: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        search: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = self._filters(
            actor_id=actor_id, actor_email=actor_email, action=action,
            resource_type=resource_type, resource_id=resource_id, status=status,
            start=start, end=end, search=search,
        )

        count_stmt = select(func.count()).select_from(AdminAuditLog)
        rows_stmt = select(AdminAuditLog)
        for clause in clauses:
            count_stmt = count_stmt.where(clause)
            rows_stmt = rows_stmt.where(clause)

        total = self.session.execute(count_stmt).scalar() or 0
        # id is the tiebreaker so paging is stable when rows share a timestamp.
        rows = self.session.execute(
            rows_stmt.order_by(
                AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc()
            ).offset(offset).limit(limit)
        ).scalars().all()

        return [self._to_dict(row) for row in rows], total

    def get_log(self, log_id: str) -> dict[str, Any] | None:
        row = self.session.execute(
            select(AdminAuditLog).where(AdminAuditLog.id == log_id)
        ).scalars().first()
        return self._to_dict(row) if row else None

    def get_facets(self) -> dict[str, list[Any]]:
        """Distinct filter values actually present in the trail."""

        def _distinct(column) -> list[str]:
            rows = self.session.execute(
                select(distinct(column))
                .where(column.is_not(None))
                .order_by(column)
                .limit(FACET_LIMIT)
            ).scalars().all()
            return [r for r in rows if r]

        actors_rows = self.session.execute(
            select(
                AdminAuditLog.actor_id,
                AdminAuditLog.actor_email,
                func.count().label("count"),
            )
            .where(AdminAuditLog.actor_id.is_not(None))
            .group_by(AdminAuditLog.actor_id, AdminAuditLog.actor_email)
            .order_by(func.count().desc())
            .limit(FACET_LIMIT)
        ).all()

        return {
            "actors": [
                {"actor_id": r[0], "actor_email": r[1], "count": r[2]}
                for r in actors_rows
            ],
            "actions": _distinct(AdminAuditLog.action),
            "resource_types": _distinct(AdminAuditLog.resource_type),
        }

    def get_retention_stats(self) -> dict[str, Any]:
        """What the trail currently holds — total rows and the span covered."""
        total, oldest, newest = self.session.execute(
            select(
                func.count(),
                func.min(AdminAuditLog.created_at),
                func.max(AdminAuditLog.created_at),
            ).select_from(AdminAuditLog)
        ).one()

        failed_count = self.session.execute(
            select(func.count()).select_from(AdminAuditLog)
            .where(AdminAuditLog.status_code >= 400)
        ).scalar() or 0

        return {
            "total_entries": total or 0,
            "failed_entries": failed_count,
            "oldest_entry_at": oldest.isoformat() if oldest else None,
            "newest_entry_at": newest.isoformat() if newest else None,
        }
