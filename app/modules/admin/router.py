import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from app.core.audit import AuditedAPIRoute
from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient

from app.core.dependencies import get_current_admin, get_supabase
from app.core.permissions import RequirePermission
from app.core.schemas import SuccessResponse
from app.core.territories import resolve_territory
from app.modules.admin.business_metrics_service import BusinessMetricsDashboardService
from app.modules.admin.schemas import (
    ActivityItem,
    ActivityResponse,
    AdminListResponse,
    AdminUpsertRequest,
    AdminUser,
    BusinessMetricsDashboardResponse,
    BusinessMetricsResponse,
    ProductionSignalsResponse,
    ServiceStatusItem,
    SystemStatusResponse,
    TaskItem,
    TasksResponse,
)
from app.modules.admin.service import AdminService
from app.modules.reports.pdf_service import PDFService
from app.modules.reports.service import ReportService

logger = logging.getLogger(__name__)

_COMP_CAMEL_TO_SNAKE: dict[str, str] = {
    "budget": "budget_usd",
    "territory": "primary_territory",
    "incentiveUsed": "incentive_used",
    "tmdbId": "tmdb_id",
    "lastUpdated": "updated_at",
}
_COMP_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _COMP_CAMEL_TO_SNAKE.items()}


def _comp_payload_to_db(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in payload.items():
        db_key = _COMP_CAMEL_TO_SNAKE.get(k, k)
        result[db_key] = v
    result.pop("id", None)
    if "updated_at" not in result:
        result["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Normalise territory to canonical label
    pt = result.get("primary_territory")
    if pt:
        t = resolve_territory(pt)
        if t:
            result["primary_territory"] = t.label
    return result


def _comp_row_to_api(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in row.items():
        api_key = _COMP_SNAKE_TO_CAMEL.get(k, k)
        if k == "updated_at" and v is not None:
            result["lastUpdated"] = str(v)[:10] if v else v
            continue
        result[api_key] = v
    return result

router = APIRouter(prefix="/api/admin", tags=["Admin"], route_class=AuditedAPIRoute)

def get_admin_service(supabase: DatabaseClient = Depends(get_supabase)) -> AdminService:
    return AdminService(supabase)


def get_business_metrics_dashboard_service(
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> BusinessMetricsDashboardService:
    return BusinessMetricsDashboardService(supabase, settings)


def _list_resource(
    service: AdminService,
    *,
    table_name: str,
    limit: int,
    offset: int,
) -> AdminListResponse:
    items, total = service.list_table(table_name, limit=limit, offset=offset)
    return AdminListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/users", response_model=AdminListResponse)
async def list_users(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="users", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch users")


@router.get("/reports", response_model=AdminListResponse)
async def list_reports(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="reports", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch reports")


@router.get("/metrics", response_model=BusinessMetricsResponse)
async def get_metrics(
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return BusinessMetricsResponse(**service.get_business_metrics())
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch business metrics")


@router.get("/business-metrics", response_model=BusinessMetricsDashboardResponse)
async def get_business_metrics_dashboard(
    _: AdminUser = Depends(RequirePermission("canViewBusinessMetrics")),
    service: BusinessMetricsDashboardService = Depends(get_business_metrics_dashboard_service),
):
    try:
        return BusinessMetricsDashboardResponse(**service.get_dashboard())
    except Exception:
        logger.exception("Failed to fetch business metrics dashboard")
        raise HTTPException(status_code=500, detail="Failed to fetch business metrics dashboard")


@router.get("/activity", response_model=ActivityResponse)
async def get_recent_activity(
    limit: int = Query(10, ge=1, le=50),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        items = service.get_recent_activity(limit=limit)
        return ActivityResponse(items=[ActivityItem(**i) for i in items])
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch recent activity")


@router.get("/system-status", response_model=SystemStatusResponse)
async def get_system_status(
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
):
    """Report what was actually measured, and say which basis each row used.

    Previously three of the five rows were hardcoded "unknown" and stamped with
    ``last_checked = now``, which claimed a check had just run against Stripe,
    Brevo and "OpenAI API" when none ever had — and named the wrong AI provider
    at that (this platform is Anthropic-first). A status panel that invents its
    own freshness is worse than no panel: it is consulted precisely when
    something is wrong.

    Each row now carries ``check``:

    ``live``
        A probe ran against the dependency during this request. ``last_checked``
        is meaningful and ``status`` reflects reality.
    ``configuration``
        Only the presence of credentials was inspected — no network call was
        made. ``last_checked`` is null, because nothing was checked.

    Deliberately excluded: a live probe of the Anthropic/OpenAI, Stripe or Brevo
    APIs. Each would bill a request and add seconds of latency to a dashboard
    that loads on every admin page view. ``ScriptAnalysisService.assert_available``
    exists for the AI path and runs where it matters — before a paid report is
    charged, not on a dashboard.
    """
    from app.core.cache import get_redis

    now = datetime.now(timezone.utc).isoformat()
    services: list[dict] = []

    db_ok = service.check_db_health()
    services.append({
        "name": "Primary Database",
        "status": "operational" if db_ok else "down",
        "check": "live",
        "detail": "SELECT against users" if db_ok else "Database is unreachable",
        "last_checked": now,
    })

    try:
        await get_redis().ping()
        services.append({
            "name": "Redis Cache",
            "status": "operational",
            "check": "live",
            "detail": "PING acknowledged",
            "last_checked": now,
        })
    except Exception as exc:
        # Degraded, not down: the app runs without Redis (caching and token
        # revocation degrade), so this must not read as an outage.
        services.append({
            "name": "Redis Cache",
            "status": "degraded",
            "check": "live",
            "detail": f"PING failed — caching and token revocation are disabled ({type(exc).__name__})",
            "last_checked": now,
        })

    # Report storage. Worth a real probe here because a misconfiguration only
    # otherwise surfaces at the end of a multi-minute paid analysis.
    try:
        storage_ok, storage_detail = await run_in_threadpool(supabase.storage.preflight)
        services.append({
            "name": "Report Storage",
            "status": "operational" if storage_ok else "down",
            "check": "live",
            "detail": storage_detail,
            "last_checked": now,
        })
    except Exception as exc:
        services.append({
            "name": "Report Storage",
            "status": "down",
            "check": "live",
            "detail": f"Preflight raised {type(exc).__name__}",
            "last_checked": now,
        })

    # Configuration-only rows. "configured" means credentials are present, which
    # is not the same as reachable — the label says so rather than implying a
    # health check.
    ai_configured = bool(settings.ANTHROPIC_API_KEY)
    fallback_configured = bool(getattr(settings, "OPENAI_API_KEY", ""))
    services.append({
        "name": "Script Analysis (Claude)",
        "status": "configured" if ai_configured else "not_configured",
        "check": "configuration",
        "detail": (
            f"Anthropic key present; OpenAI fallback {'configured' if fallback_configured else 'not configured'}"
            if ai_configured
            else "ANTHROPIC_API_KEY is not set — report generation will refuse rather than charge"
        ),
        "last_checked": None,
    })

    for name, configured, missing_note in (
        (
            "Stripe Payments",
            bool(settings.STRIPE_SECRET_KEY),
            "STRIPE_SECRET_KEY is not set — checkout and webhooks are inert",
        ),
        (
            "Brevo Email Delivery",
            bool(settings.BREVO_API_KEY),
            "BREVO_API_KEY is not set — transactional email is skipped",
        ),
    ):
        services.append({
            "name": name,
            "status": "configured" if configured else "not_configured",
            "check": "configuration",
            "detail": "Credentials present" if configured else missing_note,
            "last_checked": None,
        })

    return SystemStatusResponse(
        services=[ServiceStatusItem(**s) for s in services],
        checked_at=now,
    )


@router.get("/tasks", response_model=TasksResponse)
async def get_derived_tasks(
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        items = service.get_derived_tasks()
        return TasksResponse(items=[TaskItem(**i) for i in items])
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch tasks")


@router.get("/production-signals", response_model=ProductionSignalsResponse)
async def get_production_signals(
    territory: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    _: AdminUser = Depends(RequirePermission("canViewPlatformEconomics")),
    service: AdminService = Depends(get_admin_service),
):
    try:
        # Normalise territory alias (e.g. "UK" → "United Kingdom")
        if territory:
            t = resolve_territory(territory)
            if t:
                territory = t.label
        items, total = service.get_production_signals(
            territory=territory,
            start_date=start_date,
            end_date=end_date,
        )
        return ProductionSignalsResponse(items=items, total=total)
    except Exception:
        logger.exception(
            "Failed to fetch production signals",
            extra={"territory": territory, "start_date": start_date, "end_date": end_date},
        )
        raise HTTPException(status_code=500, detail="Failed to fetch production signals")


@router.get("/comparables", response_model=AdminListResponse)
async def list_comparables(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(RequirePermission("canEditComparables")),
    service: AdminService = Depends(get_admin_service),
):
    try:
        items, total = service.list_table("comparable_productions", limit=limit, offset=offset)
        return AdminListResponse(
            items=[_comp_row_to_api(row) for row in items],
            total=total,
            limit=limit,
            offset=offset,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch comparables")


@router.post("/comparables", response_model=dict)
async def create_comparable(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditComparables")),
    service: AdminService = Depends(get_admin_service),
):
    try:
        db_payload = _comp_payload_to_db(body.payload)
        row = service.create_row("comparable_productions", db_payload)
        return _comp_row_to_api(row)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create comparables")


@router.patch("/comparables/{item_id}", response_model=dict)
async def update_comparable(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditComparables")),
    service: AdminService = Depends(get_admin_service),
):
    try:
        db_payload = _comp_payload_to_db(body.payload)
        row = service.update_row("comparable_productions", item_id, db_payload)
        return _comp_row_to_api(row)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update comparables")


@router.delete("/comparables/{item_id}", response_model=SuccessResponse)
async def delete_comparable(
    item_id: str,
    _: AdminUser = Depends(RequirePermission("canEditComparables")),
    service: AdminService = Depends(get_admin_service),
):
    try:
        service.delete_row("comparable_productions", item_id)
        return SuccessResponse(message="comparable item deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete comparables")


@router.post("/comparables/sync-tmdb", response_model=dict)
async def sync_comparables_from_tmdb(
    _: AdminUser = Depends(RequirePermission("canEditComparables")),
    supabase: DatabaseClient = Depends(get_supabase),
):
    from app.core.config import get_settings
    from app.modules.admin.tmdb_service import TMDBService

    settings = get_settings()
    if not settings.TMDB_API_KEY:
        raise HTTPException(status_code=400, detail="TMDB_API_KEY is not configured")

    try:
        tmdb = TMDBService(settings.TMDB_API_KEY)
        result = tmdb.sync_popular(supabase)
        return {"message": "Sync completed", **result}
    except Exception:
        logger.exception("TMDB sync failed")
        raise HTTPException(status_code=500, detail="TMDB sync failed")


@router.post("/reports/{report_id}/reissue-pdf", response_model=SuccessResponse)
async def reissue_report_pdf(
    report_id: str,
    background_tasks: BackgroundTasks,
    admin: AdminUser = Depends(RequirePermission("canManagePDFReports")),
    supabase: DatabaseClient = Depends(get_supabase),
):
    """Re-generate and re-upload the PDF for a completed report."""
    report_service = ReportService(supabase)
    report = report_service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Report is not completed")
    if not report.get("report_data"):
        raise HTTPException(status_code=400, detail="Report has no analysis data")

    logger.info(
        "Admin PDF re-issue requested: report_id=%s admin_id=%s",
        report_id, admin.id,
    )
    background_tasks.add_task(
        _reissue_pdf_task, report_id=report_id, report=report,
    )
    return SuccessResponse(message="PDF re-issue started")


def _reissue_pdf_task(*, report_id: str, report: dict) -> None:
    """Background task to regenerate and upload the PDF."""
    from app.core.db import get_db_context

    try:
        with get_db_context() as session:
            db = DatabaseClient(session)
            pdf_service = PDFService()
            report_service = ReportService(db)

            html = pdf_service.render_report_html(
                report["report_data"],
                script_title=report.get("script_title", "Untitled"),
                report_type=report.get("report_type", "paid"),
                created_at=str(report.get("created_at", "")),
            )
            pdf_bytes = pdf_service.generate_pdf_bytes(html)
            if not pdf_bytes:
                logger.error("PDF re-issue failed: generation returned None report_id=%s", report_id)
                return

            uploaded_url = pdf_service.upload_pdf(
                db,
                user_id=report["user_id"],
                report_id=report_id,
                pdf_bytes=pdf_bytes,
            )
            if uploaded_url:
                report_service.update_pdf_url(report_id, uploaded_url)
            logger.info(
                "PDF re-issue complete: report_id=%s pdf_url_set=%s",
                report_id, bool(uploaded_url),
            )
    except Exception:
        logger.exception("PDF re-issue failed: report_id=%s", report_id)
