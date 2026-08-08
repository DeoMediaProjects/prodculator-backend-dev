"""Admin audit-trail reader (handoff §4.4/§4.5).

Read-only by design. The table is append-only in the application, so there is
deliberately no endpoint here that edits or deletes a row — the only removal
path is the retention purge in ``app.core.audit``, which runs on a schedule and
is itself not reachable over HTTP.

Reading the trail is gated on ``canManageAdmins``: it contains before/after
state for user, subscription and entitlement changes, which is a superset of
what any narrower admin role is entitled to see.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import ProgrammingError

from app.core.audit import AuditedAPIRoute
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.modules.admin.audit_service import AdminAuditService
from app.modules.admin.schemas import (
    AdminUser,
    AuditLogEntry,
    AuditLogFacets,
    AuditLogListResponse,
    AuditRetentionResponse,
)

logger = logging.getLogger(__name__)

# Raised by Postgres as UndefinedTable when admin_audit_logs is absent, i.e. the
# environment has not had the audit-log migration applied.
_MISSING_TABLE_DETAIL = (
    "The audit trail table does not exist in this environment. "
    "Apply the database migrations to create it."
)


def _reraise_read_failure(exc: Exception, what: str) -> None:
    """Turn a read failure into a response that says which problem it is."""
    logger.exception("Failed to read %s", what)
    if isinstance(exc, ProgrammingError) and "does not exist" in str(exc.orig or exc):
        # 503, not 500: the reader is fine and will work once the environment is
        # migrated, so this is unavailable rather than broken.
        raise HTTPException(status_code=503, detail=_MISSING_TABLE_DETAIL)
    raise HTTPException(status_code=500, detail=f"Failed to read {what}")

# route_class is set for consistency with every other admin router, so that a
# mutating endpoint added here later is audited by default. Today every endpoint
# is a GET, which the route class does not record.
router = APIRouter(
    prefix="/api/admin/audit-logs",
    tags=["Admin - Audit Trail"],
    route_class=AuditedAPIRoute,
)

MAX_RANGE_DAYS = 400


def get_audit_service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> AdminAuditService:
    return AdminAuditService(supabase)


def _parse_bound(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be an ISO date or datetime (e.g. 2026-08-01)",
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor_id: str | None = Query(None, description="Filter to one admin's actions"),
    actor_email: str | None = Query(None, description="Substring match on actor email"),
    action: str | None = Query(None, description="Exact action, e.g. update.incentive"),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    status: str | None = Query(
        None,
        description="'success' for 2xx/3xx, 'failed' for 4xx/5xx. Omit for both.",
    ),
    start_date: str | None = Query(None, description="Inclusive lower bound (ISO)"),
    end_date: str | None = Query(None, description="Inclusive upper bound (ISO)"),
    search: str | None = Query(
        None, description="Substring match across path, resource id and error"
    ),
    _: AdminUser = Depends(RequirePermission("canManageAdmins")),
    service: AdminAuditService = Depends(get_audit_service),
):
    """Newest first. Every filter is optional and they combine with AND."""
    if status is not None and status not in ("success", "failed"):
        raise HTTPException(
            status_code=400, detail="status must be 'success' or 'failed'"
        )

    start = _parse_bound(start_date, "start_date")
    end = _parse_bound(end_date, "end_date")
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start_date must not be after end_date")
    if start and end and (end - start) > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=400,
            detail=f"date range must not exceed {MAX_RANGE_DAYS} days",
        )

    try:
        rows, total = service.list_logs(
            limit=limit,
            offset=offset,
            actor_id=actor_id,
            actor_email=actor_email,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            start=start,
            end=end,
            search=search,
        )
    except Exception as exc:
        _reraise_read_failure(exc, "audit logs")

    return AuditLogListResponse(
        items=[AuditLogEntry(**row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/facets", response_model=AuditLogFacets)
async def get_audit_facets(
    _: AdminUser = Depends(RequirePermission("canManageAdmins")),
    service: AdminAuditService = Depends(get_audit_service),
):
    """The distinct actors, actions and resource types present in the trail, so
    the UI can offer real filter values instead of a free-text box."""
    try:
        return AuditLogFacets(**service.get_facets())
    except Exception as exc:
        _reraise_read_failure(exc, "audit log facets")


@router.get("/retention", response_model=AuditRetentionResponse)
async def get_audit_retention(
    _: AdminUser = Depends(RequirePermission("canManageAdmins")),
    service: AdminAuditService = Depends(get_audit_service),
):
    """The configured retention window and what the trail actually holds, so an
    admin can see how far back the record goes before relying on it."""
    from app.core.config import get_settings

    settings = get_settings()
    try:
        stats = service.get_retention_stats()
    except Exception as exc:
        _reraise_read_failure(exc, "audit retention stats")

    return AuditRetentionResponse(
        retention_days=settings.ADMIN_AUDIT_RETENTION_DAYS,
        retains_indefinitely=settings.ADMIN_AUDIT_RETENTION_DAYS <= 0,
        **stats,
    )


@router.get("/{log_id}", response_model=AuditLogEntry)
async def get_audit_log(
    log_id: str,
    _: AdminUser = Depends(RequirePermission("canManageAdmins")),
    service: AdminAuditService = Depends(get_audit_service),
):
    """One entry in full, including the before/after state."""
    row = service.get_log(log_id)
    if not row:
        raise HTTPException(status_code=404, detail="Audit log entry not found")
    return AuditLogEntry(**row)
