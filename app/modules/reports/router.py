import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.core.database_client import DatabaseClient
from app.core.config import Settings, get_settings
from app.core.db import get_db_context
from app.core.dependencies import get_supabase, get_current_user, get_optional_user
from app.modules.auth.schemas import AuthUser
from app.modules.email.service import EmailService
from app.modules.reports.pdf_service import PDFService
from app.modules.reports.schemas import (
    CreateReportRequest,
    PreviewReportResponse,
    ReportResponse,
    ReportStatusResponse,
)
from app.modules.reports.service import ReportService
from app.modules.scripts.service import ScriptAnalysisService

router = APIRouter(prefix="/api/reports", tags=["Reports"])
logger = logging.getLogger(__name__)


def get_report_service(supabase: DatabaseClient = Depends(get_supabase)) -> ReportService:
    return ReportService(supabase)


@router.post("")
async def create_report(
    body: CreateReportRequest,
    background_tasks: BackgroundTasks,
    user: AuthUser | None = Depends(get_optional_user),
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """Create a new report. Previews return synchronously; paid/b2b are async."""
    if body.report_type == "preview":
        # Preview: synchronous, no DB row, no auth required
        script_service = ScriptAnalysisService(settings)
        metadata = body.model_dump(exclude={"script_file_path"})
        analysis = service.generate_preview_report(
            request_metadata=metadata,
            script_service=script_service,
        )
        return PreviewReportResponse(analysis=analysis)

    # Paid/B2B: requires auth
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required for paid reports")

    if not body.script_file_path:
        raise HTTPException(status_code=400, detail="script_file_path is required for paid reports")

    try:
        metadata = body.model_dump()
        report_id = service.create_report(
            user_id=user.id,
            script_title=body.script_title,
            report_type=body.report_type,
            script_file_path=body.script_file_path,
            request_metadata=metadata,
        )
        background_tasks.add_task(
            process_report_task,
            report_id,
            user.id,
            user.email,
            settings,
        )
        return ReportStatusResponse(
            status="processing",
            report_id=report_id,
            message="Report generation started",
        )
    except Exception:
        logger.exception("Failed to create report")
        raise HTTPException(status_code=500, detail="Failed to create report")


@router.get("", response_model=list[ReportResponse])
async def list_reports(
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
):
    """List all reports for the current user (excludes previews)."""
    reports = service.get_user_reports(user.id)
    return [_format_report_response(r) for r in reports]


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: str,
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
):
    """Get a single report by ID."""
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return _format_report_response(report)


@router.get("/shared/{share_token}", response_model=ReportResponse)
async def get_shared_report(
    share_token: str,
    service: ReportService = Depends(get_report_service),
):
    """Get a publicly shared report (no auth required)."""
    report = service.get_report_by_share_token(share_token)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _format_report_response(report)


@router.get("/{report_id}/status", response_model=ReportStatusResponse)
async def get_report_status(
    report_id: str,
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
):
    """Poll report generation status."""
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    status = report["status"]
    if status == "failed":
        error = (report.get("report_data") or {}).get("error")
        return ReportStatusResponse(
            status=status,
            report_id=report_id,
            message="Report generation failed",
            error=error,
        )
    if status == "completed":
        return ReportStatusResponse(
            status=status,
            report_id=report_id,
            message="Report generation completed",
        )
    return ReportStatusResponse(
        status=status,
        report_id=report_id,
        message="Report generation in progress",
    )


@router.get("/{report_id}/pdf")
async def download_pdf(
    report_id: str,
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
):
    """Get PDF download URL for a report."""
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    if not report.get("pdf_url"):
        raise HTTPException(status_code=404, detail="PDF not yet generated")
    return {"pdf_url": report["pdf_url"]}


def _format_report_response(report: dict) -> dict:
    """Format a DB report row into the API response shape."""
    return {
        "id": report["id"],
        "title": report.get("script_title", ""),
        "reportType": report.get("report_type", "paid"),
        "createdAt": str(report.get("created_at", "")),
        "analysis": report.get("report_data"),
        "pdfUrl": report.get("pdf_url"),
    }


def process_report_task(
    report_id: str,
    user_id: str,
    user_email: str,
    settings: Settings,
) -> None:
    """Background task: load script -> analyze -> generate report -> persist."""
    with get_db_context() as db:
        supabase = DatabaseClient(db, settings)
        report_service = ReportService(supabase)
        script_service = ScriptAnalysisService(settings)
        email_service = EmailService(settings)
        pdf_service = PDFService()

        try:
            # Fetch the report row to get metadata
            report_row = report_service.get_report(report_id)
            if not report_row:
                logger.error("Report not found in background task: %s", report_id)
                return

            script_title = report_row["script_title"]
            report_type = report_row["report_type"]
            script_file_path = report_row.get("script_file_path")
            request_metadata = report_row.get("request_metadata") or {}

            try:
                email_service.send(
                    user_email,
                    "processing_started",
                    {"script_title": script_title, "report_id": report_id},
                )
            except Exception:
                logger.warning("Unable to send processing_started email for report_id=%s", report_id)

            # Step 1: Download and parse script
            file_bytes = supabase.storage.from_("scripts").download(script_file_path)
            filename = script_file_path.rsplit("/", 1)[-1]
            script_text = script_service.extract_text(filename, file_bytes)
            if not script_text.strip():
                raise ValueError("Script file appears to be empty")

            # Step 2: Script analysis (Call 1 — existing method)
            analysis = script_service.analyze(script_text, script_title)

            # Step 3: Full production analysis (Call 2 — new method)
            is_b2b = report_type == "b2b"
            report_data = report_service.generate_analysis_report(
                script_analysis=analysis,
                request_metadata=request_metadata,
                report_id=report_id,
                script_service=script_service,
                is_b2b=is_b2b,
            )

            # Step 4: PDF generation
            pdf_url = ""
            html = pdf_service.render_report_html(report_data)
            pdf_bytes = pdf_service.generate_pdf_bytes(html)
            if pdf_bytes:
                uploaded_pdf_url = pdf_service.upload_pdf(
                    supabase,
                    user_id=user_id,
                    report_id=report_id,
                    pdf_bytes=pdf_bytes,
                )
                pdf_url = uploaded_pdf_url or ""

            report_service.complete_report(report_id, report_data, pdf_url=pdf_url)

            try:
                email_service.send(
                    user_email,
                    "report_ready",
                    {"script_title": script_title, "report_id": report_id, "pdf_url": pdf_url},
                )
            except Exception:
                logger.warning("Unable to send report_ready email for report_id=%s", report_id)
        except Exception as exc:
            logger.exception("Report background processing failed for report_id=%s", report_id)
            report_service.fail_report(report_id, str(exc))
            try:
                email_service.send(
                    user_email,
                    "report_ready",
                    {
                        "script_title": report_row.get("script_title", "Unknown"),
                        "report_id": report_id,
                        "error": str(exc),
                    },
                )
            except Exception:
                logger.warning("Unable to send failure email for report_id=%s", report_id)
