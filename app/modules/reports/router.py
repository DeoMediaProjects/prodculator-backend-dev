import json
import logging
from time import perf_counter

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile

from app.core.database_client import DatabaseClient
from app.core.config import Settings, get_settings
from app.core.db import get_db_context
from app.core.dependencies import get_supabase, get_current_user, get_optional_user
from app.core.storage import StorageClient, S3StorageBucket
from app.modules.auth.schemas import AuthUser
from app.modules.email.service import EmailService
from app.modules.reports.pdf_service import PDFService
from app.modules.reports.schemas import (
    PreviewReportResponse,
    ReportResponse,
    ReportStatusResponse,
)
from app.modules.reports.service import ReportService
from app.modules.scripts.service import ScriptAnalysisService
from app.modules.email_gating.service import EmailGatingService

router = APIRouter(prefix="/api/reports", tags=["Reports"])
logger = logging.getLogger(__name__)


def get_report_service(supabase: DatabaseClient = Depends(get_supabase)) -> ReportService:
    return ReportService(supabase)


def get_email_gating_service(supabase: DatabaseClient = Depends(get_supabase)) -> EmailGatingService:
    return EmailGatingService(supabase)


def _resolve_pdf_url(s3_key: str | None, settings: Settings) -> str | None:
    """
    Given an S3 key stored in the DB, generate a fresh presigned URL.
    Falls back to the raw value if S3 is not configured (local dev).
    Returns None if no key is stored.
    """
    if not s3_key:
        return None
    storage_client = StorageClient(settings)
    if not storage_client._use_s3:
        # Local dev: the stored value is already a usable URL/path — return as-is
        return s3_key
    try:
        bucket = storage_client.from_("reports")
        if not isinstance(bucket, S3StorageBucket):
            return s3_key
        url = bucket._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_S3_BUCKET_NAME, "Key": s3_key},
            ExpiresIn=settings.AWS_S3_PRESIGNED_URL_EXPIRY,
        )
        return url
    except Exception:
        logger.warning("Failed to generate presigned URL for key=%s", s3_key)
        return None


@router.post("")
async def create_report(
    background_tasks: BackgroundTasks,
    # Script file — required for paid/b2b, omitted for preview
    script_file: UploadFile | None = File(default=None),
    # All report metadata sent as a JSON string in a form field
    body: str = Form(...),
    user: AuthUser | None = Depends(get_optional_user),
    service: ReportService = Depends(get_report_service),
    email_gating_service: EmailGatingService = Depends(get_email_gating_service),
    settings: Settings = Depends(get_settings),
):
    """Create a new report. Previews return synchronously; paid/b2b are async.

    The request must be submitted as **multipart/form-data** with:
    - ``body``: JSON string of report metadata (see CreateReportRequest schema).
    - ``script_file``: the script file (PDF/txt/fountain/fdx) — required for paid/b2b.

    Scripts are **never persisted** to storage; they are read into memory, analysed,
    then discarded.
    """
    # Parse the JSON body from the form field
    try:
        from app.modules.reports.schemas import CreateReportRequest
        body_data = CreateReportRequest.model_validate(json.loads(body))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}")

    logger.info(
        "Create report request received: report_type=%s title=%s has_user=%s",
        body_data.report_type,
        body_data.script_title,
        bool(user),
    )

    if body_data.report_type == "preview":
        # Email gating: always use the authenticated user's email; fall back to provided email
        # for anonymous requests only. This prevents DoS via spoofed email.
        gate_email = user.email if user else body_data.email
        if gate_email:
            if email_gating_service.is_blocked(gate_email):
                raise HTTPException(
                    status_code=403,
                    detail="This email address has been blocked from generating free reports",
                )

        # Preview: synchronous, no DB row, no auth required
        started = perf_counter()
        try:
            script_service = ScriptAnalysisService(settings)
            metadata = body_data.model_dump(exclude={"script_file_path"})
            analysis = service.generate_preview_report(
                request_metadata=metadata,
                script_service=script_service,
            )
            elapsed_ms = int((perf_counter() - started) * 1000)
            logger.info(
                "Preview report generated: title=%s elapsed_ms=%s location_rankings=%s",
                body_data.script_title,
                elapsed_ms,
                len(analysis.get("locationRankings", [])),
            )

            # Record email gating usage after successful generation
            if gate_email:
                try:
                    email_gating_service.create_record(gate_email, report_generated=True)
                except Exception:
                    logger.warning("Failed to record email gating for %s", gate_email)

            return PreviewReportResponse(analysis=analysis)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Preview report generation failed: title=%s", body_data.script_title)
            raise HTTPException(status_code=500, detail="Failed to generate preview report")

    # --- Paid / B2B flow ---

    if not user:
        raise HTTPException(status_code=401, detail="Authentication required for paid reports")

    if not script_file:
        raise HTTPException(status_code=400, detail="script_file is required for paid/b2b reports")

    # Validate and read the file into memory — it will NOT be stored anywhere
    script_service = ScriptAnalysisService(settings)
    filename = script_file.filename or "script.txt"
    file_size = script_file.size or 0
    valid, error = script_service.validate_file(filename, file_size)
    if not valid:
        raise HTTPException(status_code=400, detail=error)

    file_bytes = await script_file.read()

    # Extract text immediately so the background task receives plain text only
    try:
        script_text = script_service.extract_text(filename, file_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to extract text from script file")

    if not script_text.strip():
        raise HTTPException(status_code=400, detail="Script file appears to be empty")

    # Explicitly delete the bytes so they are not referenced beyond this scope
    del file_bytes

    try:
        started = perf_counter()
        metadata = body_data.model_dump(exclude={"script_file_path"})
        report_id = service.create_report(
            user_id=user.id,
            script_title=body_data.script_title,
            report_type=body_data.report_type,
            script_file_path=None,  # never stored
            request_metadata=metadata,
        )
        background_tasks.add_task(
            process_report_task,
            report_id,
            user.id,
            user.email,
            script_text,
            filename,
            settings,
        )
        elapsed_ms = int((perf_counter() - started) * 1000)
        logger.info(
            "Queued paid report processing: report_id=%s user_id=%s report_type=%s elapsed_ms=%s",
            report_id,
            user.id,
            body_data.report_type,
            elapsed_ms,
        )
        return ReportStatusResponse(
            status="processing",
            report_id=report_id,
            message="Report generation started",
        )
    except Exception:
        logger.exception(
            "Failed to create report: user_id=%s report_type=%s title=%s",
            user.id,
            body_data.report_type,
            body_data.script_title,
        )
        raise HTTPException(status_code=500, detail="Failed to create report")


@router.get("", response_model=list[ReportResponse])
async def list_reports(
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """List all reports for the current user (excludes previews)."""
    reports = service.get_user_reports(user.id)
    return [_format_report_response(r, settings) for r in reports]


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: str,
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """Get a single report by ID."""
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return _format_report_response(report, settings)


@router.get("/shared/{share_token}", response_model=ReportResponse)
async def get_shared_report(
    share_token: str,
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """Get a publicly shared report (no auth required)."""
    report = service.get_report_by_share_token(share_token)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _format_report_response(report, settings)


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
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
):
    """Stream the PDF for a report directly from S3 as raw bytes."""
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    s3_key = report.get("pdf_url")
    if not s3_key:
        raise HTTPException(status_code=404, detail="PDF not found")

    # pdf_url stores the S3 key — download the bytes directly from S3
    storage_path = f"{report['user_id']}/{report_id}.pdf"
    try:
        pdf_bytes = supabase.storage.from_("reports").download(storage_path)
    except Exception:
        logger.warning("PDF download from storage failed: report_id=%s path=%s", report_id, storage_path)
        raise HTTPException(status_code=404, detail="PDF not found")

    script_title = report.get("script_title", "Report")
    filename = f"Report - {script_title}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


def _format_report_response(report: dict, settings: Settings) -> dict:
    """Format a DB report row into the API response shape.

    ``pdf_url`` in the DB holds the raw S3 object key.  A fresh presigned URL is
    generated here so the frontend always receives a non-expired link.
    """
    return {
        "id": report["id"],
        "title": report.get("script_title", ""),
        "reportType": report.get("report_type", "paid"),
        "createdAt": str(report.get("created_at", "")),
        "analysis": report.get("report_data"),
        "pdfUrl": _resolve_pdf_url(report.get("pdf_url"), settings),
    }


def _compact_script_analysis_meta(meta: dict | None) -> dict:
    if not isinstance(meta, dict):
        return {}

    compact: dict = {}
    for key in ("mode", "fallbackUsed", "reason", "chunkedFailed", "chunkedError", "fallbackToSinglePass"):
        value = meta.get(key)
        if value is not None:
            if isinstance(value, str):
                compact[key] = ReportService.redact_sensitive_text(value)
            else:
                compact[key] = value

    chunk_telemetry = meta.get("chunkTelemetry")
    if isinstance(chunk_telemetry, dict):
        compact["chunkTelemetry"] = {
            "totalChunks": chunk_telemetry.get("totalChunks"),
            "generatedChunks": chunk_telemetry.get("generatedChunks"),
            "usedChunks": chunk_telemetry.get("usedChunks"),
            "failedChunks": chunk_telemetry.get("failedChunks"),
            "droppedChunks": chunk_telemetry.get("droppedChunks"),
            "successRatio": chunk_telemetry.get("successRatio"),
            "stopReasons": chunk_telemetry.get("stopReasons"),
        }

    overall_confidence = meta.get("overallConfidence")
    if isinstance(overall_confidence, (int, float)):
        compact["overallConfidence"] = overall_confidence

    section_confidence = meta.get("sectionConfidence")
    if isinstance(section_confidence, dict):
        compact["sectionConfidence"] = section_confidence

    return compact



def process_report_task(
    report_id: str,
    user_id: str,
    user_email: str,
    script_text: str,
    script_filename: str,
    settings: Settings,
) -> None:
    """Background task: analyse pre-extracted script text -> generate report -> upload PDF to S3.

    The script file is **never written to disk or S3**. The caller extracts text in the
    request handler and passes only the plain-text string here.
    """
    with get_db_context() as db:
        started = perf_counter()
        current_step = "init"
        report_row: dict | None = None
        script_analysis_meta: dict = {}
        logger.info("Report background task started: report_id=%s user_id=%s", report_id, user_id)

        supabase = DatabaseClient(db, settings)
        report_service = ReportService(supabase)
        script_service = ScriptAnalysisService(settings)
        email_service = EmailService(settings)
        pdf_service = PDFService()

        try:
            # Fetch the report row to get metadata
            current_step = "load_report_row"
            report_row = report_service.get_report(report_id)
            if not report_row:
                logger.error("Report not found in background task: %s", report_id)
                return

            script_title = report_row["script_title"]
            report_type = report_row["report_type"]
            request_metadata = report_row.get("request_metadata") or {}
            logger.info(
                "Loaded report row: report_id=%s report_type=%s metadata_keys=%s",
                report_id,
                report_type,
                sorted(request_metadata.keys()),
            )

            try:
                current_step = "email_processing_started"
                email_service.send(
                    user_email,
                    "processing_started",
                    {"script_title": script_title, "report_id": report_id},
                )
                logger.debug("Sent processing_started email: report_id=%s to=%s", report_id, user_email)
            except Exception:
                logger.warning("Unable to send processing_started email for report_id=%s", report_id)

            # Step 1: Script text was already extracted in the request handler — log and validate
            current_step = "extract_script_text"
            logger.info(
                "Script text received in background task: report_id=%s filename=%s chars=%s",
                report_id,
                script_filename,
                len(script_text),
            )
            if not script_text.strip():
                raise ValueError("Script file appears to be empty")

            # Step 2: Script analysis
            current_step = "script_analysis"
            step_started = perf_counter()
            analysis, script_analysis_meta = script_service.analyze_with_meta(script_text, script_title)
            compact_meta = _compact_script_analysis_meta(script_analysis_meta)
            failed_chunks = ((compact_meta.get("chunkTelemetry") or {}).get("failedChunks") or 0)
            logger.info(
                "Script analysis complete: report_id=%s locations=%s budget_estimate=%s elapsed_ms=%s meta=%s",
                report_id,
                len(analysis.locations),
                analysis.budgetEstimate.range,  # AI-estimated range from script content
                int((perf_counter() - step_started) * 1000),
                compact_meta,
            )
            if compact_meta.get("fallbackUsed") or failed_chunks:
                logger.warning(
                    "Script analysis quality warning: report_id=%s fallback=%s failed_chunks=%s mode=%s",
                    report_id,
                    bool(compact_meta.get("fallbackUsed")),
                    failed_chunks,
                    compact_meta.get("mode"),
                )

            # Non-blocking write into production_signals for admin analytics.
            try:
                signal_row = report_service.upsert_production_signal(
                    report_id=report_id,
                    report_row=report_row,
                    request_metadata=request_metadata,
                    script_analysis=analysis,
                )
                logger.info(
                    "Production signal upserted: report_id=%s row_written=%s",
                    report_id,
                    bool(signal_row),
                )
            except Exception:
                logger.warning(
                    "Production signal upsert failed: report_id=%s",
                    report_id,
                    exc_info=True,
                )

            # Step 3: Full production analysis
            current_step = "production_analysis"
            step_started = perf_counter()
            is_b2b = report_type == "b2b"
            report_data = report_service.generate_analysis_report(
                script_analysis=analysis,
                request_metadata=request_metadata,
                report_id=report_id,
                script_service=script_service,
                is_b2b=is_b2b,
            )
            logger.info(
                "Production analysis complete: report_id=%s is_b2b=%s location_rankings=%s elapsed_ms=%s",
                report_id,
                is_b2b,
                len(report_data.get("locationRankings", [])),
                int((perf_counter() - step_started) * 1000),
            )

            # Step 4: PDF generation
            current_step = "pdf_render"
            pdf_s3_key = ""
            step_started = perf_counter()
            html = pdf_service.render_report_html(
                report_data,
                script_title=script_title,
                report_type=report_type,
                created_at=str(report_row.get("created_at", "")),
                request_config=request_metadata,
            )
            logger.debug(
                "Rendered PDF HTML: report_id=%s html_chars=%s elapsed_ms=%s",
                report_id,
                len(html),
                int((perf_counter() - step_started) * 1000),
            )

            current_step = "pdf_generate"
            step_started = perf_counter()
            pdf_bytes = pdf_service.generate_pdf_bytes(html)
            logger.info(
                "PDF generation step complete: report_id=%s has_pdf=%s elapsed_ms=%s",
                report_id,
                bool(pdf_bytes),
                int((perf_counter() - step_started) * 1000),
            )
            if pdf_bytes:
                current_step = "pdf_upload"
                step_started = perf_counter()
                # upload_pdf now returns the raw S3 key (not a presigned URL)
                uploaded_key = pdf_service.upload_pdf(
                    supabase,
                    user_id=user_id,
                    report_id=report_id,
                    pdf_bytes=pdf_bytes,
                )
                pdf_s3_key = uploaded_key or ""
                logger.info(
                    "PDF upload complete: report_id=%s s3_key_set=%s elapsed_ms=%s",
                    report_id,
                    bool(pdf_s3_key),
                    int((perf_counter() - step_started) * 1000),
                )

            # pdf_url column stores the S3 key — presigned URL generated at serve time
            current_step = "persist_report"
            report_service.complete_report(report_id, report_data, pdf_url=pdf_s3_key)
            logger.info(
                "Report marked completed: report_id=%s total_elapsed_ms=%s",
                report_id,
                int((perf_counter() - started) * 1000),
            )

            try:
                current_step = "email_report_ready"
                email_service.send(
                    user_email,
                    "report_ready",
                    {"script_title": script_title, "report_id": report_id},
                )
                logger.debug("Sent report_ready email: report_id=%s to=%s", report_id, user_email)
            except Exception:
                logger.warning("Unable to send report_ready email for report_id=%s", report_id)
        except Exception as exc:
            logger.exception(
                "Report background processing failed: report_id=%s step=%s elapsed_ms=%s",
                report_id,
                current_step,
                int((perf_counter() - started) * 1000),
            )
            report_service.fail_report(
                report_id,
                str(exc),
                error_context={
                    "step": current_step,
                    "scriptAnalysisMeta": _compact_script_analysis_meta(script_analysis_meta),
                },
            )
            logger.info("Report marked failed: report_id=%s", report_id)
            try:
                email_service.send(
                    user_email,
                    "report_ready",
                    {
                        "script_title": (report_row or {}).get("script_title", "Unknown"),
                        "report_id": report_id,
                        "error": str(exc),
                    },
                )
                logger.debug("Sent failure email: report_id=%s to=%s", report_id, user_email)
            except Exception:
                logger.warning("Unable to send failure email for report_id=%s", report_id)
