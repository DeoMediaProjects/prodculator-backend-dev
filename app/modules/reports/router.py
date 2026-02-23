import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.core.database_client import DatabaseClient

from app.core.config import Settings, get_settings
from app.core.db import get_db_context
from app.core.dependencies import get_supabase, get_current_user
from app.modules.auth.schemas import AuthUser
from app.modules.email.service import EmailService
from app.modules.reports.pdf_service import PDFService
from app.modules.reports.schemas import CreateReportRequest, ReportResponse, ReportStatusResponse
from app.modules.reports.service import ReportService
from app.modules.scripts.service import ScriptAnalysisService

router = APIRouter(prefix="/api/reports", tags=["Reports"])
logger = logging.getLogger(__name__)


def get_report_service(supabase: DatabaseClient = Depends(get_supabase)) -> ReportService:
    return ReportService(supabase)


@router.post("", response_model=ReportStatusResponse)
async def create_report(
    body: CreateReportRequest,
    background_tasks: BackgroundTasks,
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """Create a new report (starts background generation)."""
    if not body.script_file_path:
        raise HTTPException(status_code=400, detail="script_file_path is required")

    try:
        report_id = service.create_report(
            user_id=user.id,
            script_title=body.script_title,
            report_type=body.report_type,
            script_file_path=body.script_file_path,
        )
        background_tasks.add_task(
            process_report_task,
            report_id,
            user.id,
            user.email,
            body.script_title,
            body.report_type,
            body.script_file_path,
            settings,
        )
        return ReportStatusResponse(
            status="processing",
            report_id=report_id,
            message="Report generation started",
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create report")


@router.get("", response_model=list[ReportResponse])
async def list_reports(
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
):
    """List all reports for the current user."""
    reports = service.get_user_reports(user.id)
    return reports


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
    return report


@router.get("/shared/{share_token}", response_model=ReportResponse)
async def get_shared_report(
    share_token: str,
    service: ReportService = Depends(get_report_service),
):
    """Get a publicly shared report (no auth required)."""
    report = service.get_report_by_share_token(share_token)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


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


def process_report_task(
    report_id: str,
    user_id: str,
    user_email: str,
    script_title: str,
    report_type: str,
    script_file_path: str,
    settings: Settings,
) -> None:
    """Background task: load script -> analyze -> generate report -> persist status."""
    with get_db_context() as db:
        supabase = DatabaseClient(db, settings)
        report_service = ReportService(supabase)
        script_service = ScriptAnalysisService(settings)
        email_service = EmailService(settings)
        pdf_service = PDFService()

        try:
            try:
                email_service.send(
                    user_email,
                    "processing_started",
                    {"script_title": script_title, "report_id": report_id},
                )
            except Exception:
                logger.warning("Unable to send processing_started email for report_id=%s", report_id)

            file_bytes = supabase.storage.from_("scripts").download(script_file_path)
            filename = script_file_path.rsplit("/", 1)[-1]
            script_text = script_service.extract_text(filename, file_bytes)
            if not script_text.strip():
                raise ValueError("Script file appears to be empty")

            analysis = script_service.analyze(script_text, script_title)
            if report_type == "b2b":
                report_data = report_service.generate_b2b_report(script_title, analysis, report_id)
            else:
                report_data = report_service.generate_b2c_report(script_title, analysis, report_id)

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
                        "script_title": script_title,
                        "report_id": report_id,
                        "error": str(exc),
                    },
                )
            except Exception:
                logger.warning("Unable to send failure email for report_id=%s", report_id)
