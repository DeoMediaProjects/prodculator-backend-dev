import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminUser
from app.modules.email.service import EmailService
from app.modules.pdf_reports.schemas import (
    PdfReportItem,
    PdfReportListResponse,
    PdfReportPreviewResponse,
    ResendRequest,
)
from app.modules.pdf_reports.service import PdfReportsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/pdf-reports", tags=["Admin - PDF Reports"])


def get_pdf_reports_service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> PdfReportsService:
    return PdfReportsService(supabase)


def _sanitize_filename(title: str) -> str:
    safe = re.sub(r'[^\w\s\-.]', '', title).strip()
    return safe or "report"


@router.get("", response_model=PdfReportListResponse)
async def list_pdf_reports(
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(RequirePermission("canManagePDFReports")),
    service: PdfReportsService = Depends(get_pdf_reports_service),
    settings: Settings = Depends(get_settings),
):
    try:
        rows, total = service.list_reports(limit=limit, offset=offset)
        items = [
            PdfReportItem(
                id=row["id"],
                title=row["script_title"],
                email=row.get("email") or "",
                generated=row["created_at"],
                downloaded=row["downloaded"],
                size=service.get_file_size(row["user_id"], row["id"], settings),
            )
            for row in rows
        ]
        return PdfReportListResponse(items=items, total=total, limit=limit, offset=offset)
    except Exception:
        logger.exception("Failed to fetch PDF reports")
        raise HTTPException(status_code=500, detail="Failed to fetch PDF reports")


@router.get("/{report_id}/preview", response_model=PdfReportPreviewResponse)
async def preview_pdf_report(
    report_id: str,
    _: AdminUser = Depends(RequirePermission("canManagePDFReports")),
    service: PdfReportsService = Depends(get_pdf_reports_service),
):
    try:
        report = service.get_report(report_id)
        if not report or report.get("status") != "completed" or not report.get("pdf_url"):
            raise HTTPException(status_code=404, detail="Report not found")
        return PdfReportPreviewResponse(url=report["pdf_url"])
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to preview report %s", report_id)
        raise HTTPException(status_code=500, detail="Failed to preview report")


@router.get("/{report_id}/download")
async def download_pdf_report(
    report_id: str,
    _: AdminUser = Depends(RequirePermission("canManagePDFReports")),
    service: PdfReportsService = Depends(get_pdf_reports_service),
    settings: Settings = Depends(get_settings),
):
    try:
        report = service.get_report(report_id)
        if not report or report.get("status") != "completed" or not report.get("pdf_url"):
            raise HTTPException(status_code=404, detail="Report not found")

        pdf_bytes = service.download_pdf(report["user_id"], report["id"], settings)
        service.mark_downloaded(report_id)

        filename = _sanitize_filename(report.get("script_title", "report"))
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
        )
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="PDF file not found on disk")
    except Exception:
        logger.exception("Failed to download report %s", report_id)
        raise HTTPException(status_code=500, detail="Failed to download report")


@router.post("/{report_id}/resend", response_model=SuccessResponse)
async def resend_pdf_report(
    report_id: str,
    body: ResendRequest,
    _: AdminUser = Depends(RequirePermission("canManagePDFReports")),
    service: PdfReportsService = Depends(get_pdf_reports_service),
    settings: Settings = Depends(get_settings),
):
    try:
        report = service.get_report(report_id)
        if not report or report.get("status") != "completed" or not report.get("pdf_url"):
            raise HTTPException(status_code=404, detail="Report not found")

        email = body.payload.email
        if not email:
            email = service.get_user_email(report["user_id"])
        if not email:
            raise HTTPException(status_code=400, detail="No email address available")

        email_service = EmailService(settings)
        email_service.send(
            email,
            "report_ready",
            {
                "script_title": report.get("script_title", ""),
                "report_id": report["id"],
                "pdf_url": report["pdf_url"],
            },
        )
        return SuccessResponse(message=f"Report re-sent to {email}")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to resend report %s", report_id)
        raise HTTPException(status_code=500, detail="Failed to resend report")
