import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from app.core.database_client import DatabaseClient

from app.core.dependencies import get_current_admin, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import (
    AdminListResponse,
    AdminUpsertRequest,
    AdminUser,
    BusinessMetricsResponse,
    ProductionSignalsResponse,
)
from app.modules.admin.service import AdminService
from app.modules.reports.pdf_service import PDFService
from app.modules.reports.service import ReportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin"])

def get_admin_service(supabase: DatabaseClient = Depends(get_supabase)) -> AdminService:
    return AdminService(supabase)


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


@router.get("/production-signals", response_model=ProductionSignalsResponse)
async def get_production_signals(
    territory: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        items, total = service.get_production_signals(
            territory=territory,
            start_date=start_date,
            end_date=end_date,
        )
        return ProductionSignalsResponse(items=items, total=total)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch production signals")


@router.get("/comparables", response_model=AdminListResponse)
async def list_comparables(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="comparable_productions", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch comparables")


@router.post("/comparables", response_model=dict)
async def create_comparable(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.create_row("comparable_productions", body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create comparables")


@router.patch("/comparables/{item_id}", response_model=dict)
async def update_comparable(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.update_row("comparable_productions", item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update comparables")


@router.delete("/comparables/{item_id}", response_model=SuccessResponse)
async def delete_comparable(
    item_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        service.delete_row("comparable_productions", item_id)
        return SuccessResponse(message="comparable item deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete comparables")


@router.post("/reports/{report_id}/reissue-pdf", response_model=SuccessResponse)
async def reissue_report_pdf(
    report_id: str,
    background_tasks: BackgroundTasks,
    admin: AdminUser = Depends(get_current_admin),
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
        with get_db_context() as supabase:
            pdf_service = PDFService()
            report_service = ReportService(supabase)

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
                supabase,
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
