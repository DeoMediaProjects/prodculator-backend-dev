import json
import logging
from time import perf_counter

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile

from app.core.database_client import DatabaseClient
from app.core.config import Settings, get_settings
from app.core.db import get_db_context
from app.core.dependencies import get_supabase, get_current_user, get_optional_user, RequirePlan
from app.core.queue import get_report_queue
from app.core.storage import StorageClient, S3StorageBucket
from app.modules.auth.schemas import AuthUser
from app.modules.email.service import EmailService
from app.modules.reports.pdf_service import PDFService, strip_em_dashes
from app.modules.reports.schemas import (
    CreateReportRequest,
    PreviewReportResponse,
    ReportResponse,
    ReportStatusResponse,
    UpdateProjectDetailsRequest,
)
from app.modules.reports.service import ReportService
from app.modules.scripts.service import ClaudeUnavailableError, ScriptAnalysisService
from app.modules.email_gating.service import EmailGatingService
from app.modules.subscriptions.service import SubscriptionService

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


def _generate_preview_response(
    *,
    body_data: CreateReportRequest,
    user: AuthUser | None,
    service: ReportService,
    email_gating_service: EmailGatingService,
    settings: Settings,
) -> PreviewReportResponse:
    # Email gating: always use the authenticated user's email; fall back to provided email
    # for anonymous requests only. This prevents DoS via spoofed email.
    gate_email = user.email if user else body_data.email
    if gate_email and email_gating_service.is_blocked(gate_email):
        raise HTTPException(
            status_code=403,
            detail="This email address has been blocked from generating free reports",
        )

    started = perf_counter()
    try:
        script_service = ScriptAnalysisService(settings)
        metadata = body_data.model_dump(exclude={"script_file_path"})
        analysis = service.generate_preview_report(
            request_metadata=metadata,
            script_service=script_service,
        )
        analysis = _build_free_tier_report_data(analysis)
        elapsed_ms = int((perf_counter() - started) * 1000)
        logger.info(
            "Preview report generated: title=%s elapsed_ms=%s location_rankings=%s",
            body_data.script_title,
            elapsed_ms,
            len(analysis.get("locationRankings", [])),
        )

        # Record email gating usage after successful generation.
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


@router.post("/preview", response_model=PreviewReportResponse)
async def create_preview_report(
    body: CreateReportRequest,
    user: AuthUser | None = Depends(get_optional_user),
    service: ReportService = Depends(get_report_service),
    email_gating_service: EmailGatingService = Depends(get_email_gating_service),
    settings: Settings = Depends(get_settings),
):
    """Create a synchronous preview report from a JSON body.

    Full paid reports still use the multipart ``POST /api/reports`` endpoint,
    because they upload a script file. Preview has no file upload, so JSON is
    the simpler and less error-prone contract for the frontend.
    """
    body_data = body.model_copy(update={"report_type": "preview"})
    return _generate_preview_response(
        body_data=body_data,
        user=user,
        service=service,
        email_gating_service=email_gating_service,
        settings=settings,
    )


@router.post("")
async def create_report(
    background_tasks: BackgroundTasks,
    # Script file — required for paid/b2b, omitted for preview
    script_file: UploadFile | None = File(default=None),
    # All report metadata sent as a JSON string in a form field
    body: str = Form(...),
    user: AuthUser | None = Depends(get_optional_user),
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """Create a paid/B2B report (async). Returns a ``report_id`` to poll for status.

    Preview reports use the dedicated JSON endpoint ``POST /api/reports/preview``;
    this multipart endpoint handles only paid/B2B reports, which upload a script.

    The request must be submitted as **multipart/form-data** with:
    - ``body``: JSON string of report metadata (see CreateReportRequest schema).
    - ``script_file``: the script file (PDF/txt/fountain/fdx) — required.

    Scripts are **never persisted** to storage; they are read into memory, analysed,
    then discarded.
    """
    # Parse the JSON body from the form field
    try:
        body_data = CreateReportRequest.model_validate(json.loads(body))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}")

    logger.info(
        "Create report request received: report_type=%s title=%s has_user=%s",
        body_data.report_type,
        body_data.script_title,
        bool(user),
    )

    # Previews are served by the dedicated JSON endpoint POST /api/reports/preview.
    if body_data.report_type == "preview":
        raise HTTPException(
            status_code=400,
            detail="Preview reports use POST /api/reports/preview (JSON body, no file upload).",
        )

    # --- Paid / B2B flow ---

    if not user:
        raise HTTPException(status_code=401, detail="Authentication required for paid reports")

    # Subscription / usage check — enforced on every non-preview report.
    sub_service = SubscriptionService(service.supabase)
    can_generate, reason = sub_service.can_generate_report(user.id)
    if not can_generate:
        raise HTTPException(status_code=403, detail=reason)

    # Non-subscribed users get their first free full report stored as
    # report_type="free" so the limit is enforced on subsequent attempts.
    # Credit buyers (pay-per-report) have no subscription but have paid — store as "paid".
    # A subscriber at their monthly limit can also use a credit (reason will say "pay-per-report").
    using_credit = "pay-per-report" in reason
    has_subscription = sub_service.get_active_subscription(user.id) is not None
    has_credits = sub_service.get_credits_remaining(user.id) > 0
    if has_subscription and not using_credit:
        effective_report_type = body_data.report_type
    elif has_credits:
        effective_report_type = "paid"
    else:
        effective_report_type = "free"

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

    # Pre-flight: confirm Scriptelligence (Claude) is reachable BEFORE we charge
    # for the report (create the row / consume a credit). If it's down, the user
    # is never charged — they get a clear, retryable error to display instead.
    try:
        script_service.check_available()
    except ClaudeUnavailableError as exc:
        logger.warning(
            "Report creation blocked — Scriptelligence unavailable: user_id=%s reason=%s",
            user.id,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail="Scriptelligence is currently not available",
        )

    try:
        started = perf_counter()
        metadata = body_data.model_dump(exclude={"script_file_path"})
        # Record whether this report consumed a pay-per-report credit so the
        # background task can refund it if generation ultimately fails.
        metadata["_credit_consumed"] = using_credit
        report_id = service.create_report(
            user_id=user.id,
            script_title=body_data.script_title,
            report_type=effective_report_type,
            script_file_path=None,  # never stored
            request_metadata=metadata,
        )
        _dispatch_report_job(
            background_tasks=background_tasks,
            report_id=report_id,
            user_id=user.id,
            user_email=user.email,
            script_text=script_text,
            filename=filename,
            settings=settings,
        )

        # Consume a pay-per-report credit whenever the report was credit-funded.
        # This applies both to non-subscribers (primary credit path) and to
        # subscribers who hit their monthly limit and fall back to a credit.
        if using_credit:
            sub_service.consume_report_credit(user.id)

        elapsed_ms = int((perf_counter() - started) * 1000)
        logger.info(
            "Queued paid report processing: report_id=%s user_id=%s report_type=%s elapsed_ms=%s",
            report_id,
            user.id,
            effective_report_type,
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
            effective_report_type,
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


@router.get("/sample/html")
async def get_sample_report_html() -> Response:
    """Public marketing sample: renders the real report template with a canned
    dataset, so the website's /sample always matches live report output.
    Declared before /{report_id} so the two-segment path can't be captured by it."""
    from app.modules.reports.sample_report import (
        SAMPLE_REPORT_DATA,
        SAMPLE_TITLE,
        SAMPLE_CREATED_AT,
    )

    pdf_service = PDFService()
    html = pdf_service.render_report_html(
        SAMPLE_REPORT_DATA,
        script_title=SAMPLE_TITLE,
        created_at=SAMPLE_CREATED_AT,
        is_preview=False,
    )
    return Response(content=html, media_type="text/html")


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: str,
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """Get a single report by ID."""
    from app.models.enums import normalize_plan

    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    report_plan = normalize_plan(user.plan)
    # Credit buyers (pay-per-report) have plan="free" but paid for a full report.
    # Honour what they purchased: all territories, full sections, clean PDF.
    if report_plan == "free" and report.get("report_type") == "paid":
        report_plan = "producer"
    return _format_report_response(report, settings, user_plan=report_plan)


@router.get("/shared/{share_token}", response_model=ReportResponse)
async def get_shared_report(
    share_token: str,
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """Get a publicly shared report (no auth required).

    Shared reports are served at full producer-level fidelity — all territories,
    all sections, no investorSummary (that remains gated behind auth).
    """
    report = service.get_report_by_share_token(share_token)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _format_report_response(report, settings, user_plan="producer")


@router.post("/{report_id}/share")
async def create_share_link(
    report_id: str,
    user: AuthUser = Depends(RequirePlan("studio")),
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """Create (or retrieve) a permanent public share link for a report (Studio only).

    Returns the share token and the full shareable URL.
    Idempotent — calling this more than once returns the same token.
    """
    try:
        token = service.create_share_link(report_id, user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        logger.exception("create_share_link failed: report_id=%s", report_id)
        raise HTTPException(status_code=500, detail="Failed to create share link")

    share_url = f"{settings.FRONTEND_URL}/report/shared/{token}"
    return {"share_token": token, "share_url": share_url}


@router.delete("/{report_id}/share", status_code=204)
async def revoke_share_link(
    report_id: str,
    user: AuthUser = Depends(RequirePlan("studio")),
    service: ReportService = Depends(get_report_service),
):
    """Revoke the share link for a report, making it private again (Studio only)."""
    try:
        service.revoke_share_link(report_id, user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        logger.exception("revoke_share_link failed: report_id=%s", report_id)
        raise HTTPException(status_code=500, detail="Failed to revoke share link")


@router.delete("/{report_id}", status_code=204)
async def delete_report(
    report_id: str,
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
):
    """Delete a report the authenticated user owns."""
    try:
        service.delete_report(report_id, user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        logger.exception("delete_report failed: report_id=%s", report_id)
        raise HTTPException(status_code=500, detail="Failed to delete report")


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


# Territory limits per plan — None means all territories included
_TERRITORY_LIMITS: dict[str, int | None] = {
    "free": 3,
    "professional": 5,
    "producer": None,
    "studio": None,
}


@router.get("/{report_id}/pdf")
async def download_pdf(
    report_id: str,
    user: AuthUser = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
):
    """Stream the PDF for a report.

    Free-tier users receive an on-the-fly generated PDF showing the incentive
    and location sections, with locked placeholder pages for premium sections,
    plus a trial watermark. Paid users (or users who have upgraded) receive the
    full clean PDF stored in S3.
    """
    from app.models.enums import normalize_plan

    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    plan = normalize_plan(user.plan)
    # Credit buyers (pay-per-report) have plan="free" but paid for a full report.
    is_free = plan == "free" and report.get("report_type") != "paid"

    script_title = report.get("script_title", "Report")
    filename = f"Report - {script_title}.pdf"

    if is_free:
        # Generate a free-tier PDF on-the-fly: visible sections are shown cleanly,
        # premium sections render as locked upgrade-prompt pages.
        report_data = report.get("report_data") or {}
        if not report_data:
            raise HTTPException(status_code=404, detail="Report data not yet available")
        free_data = _build_free_tier_report_data(report_data)
        pdf_service = PDFService()
        html = pdf_service.render_report_html(
            free_data,
            script_title=script_title,
            report_type="preview",
            created_at=str(report.get("created_at", "")),
            is_preview=True,
        )
        pdf_bytes = pdf_service.generate_pdf_bytes(html)
        if not pdf_bytes:
            raise HTTPException(status_code=503, detail="PDF generation temporarily unavailable")
    else:
        # Paid / upgraded users: serve the full report PDF stored in S3.
        s3_key = report.get("pdf_url")
        if not s3_key:
            raise HTTPException(status_code=404, detail="PDF not found")
        storage_path = f"{report['user_id']}/{report_id}.pdf"
        try:
            pdf_bytes = supabase.storage.from_("reports").download(storage_path)
        except Exception:
            logger.warning("PDF download from storage failed: report_id=%s path=%s", report_id, storage_path)
            raise HTTPException(status_code=404, detail="PDF not found")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


def _build_free_tier_report_data(report_data: dict) -> dict:
    """Return a filtered copy of report_data for the free-tier preview PDF.

    This is also used for free-tier API responses, so sensitive values must
    be removed from the payload rather than hidden only by the frontend.
    """
    import copy

    data = copy.deepcopy(report_data)

    def dict_rows(value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    rankings_raw = dict_rows(data.get("locationRankings"))
    urgent_count = _preview_urgent_action_count(data)
    complexity_count = _preview_complexity_factor_count(data)
    data["previewUrgentActionCount"] = urgent_count
    data["previewComplexityFactorCount"] = complexity_count

    summary = data.get("executiveSummary")
    if isinstance(summary, dict):
        summary["keyInsights"] = _build_preview_key_insights(
            data, rankings_raw, urgent_count, complexity_count,
        )
        for key in (
            "headlineNetBudget",
            "recommendedTerritoryRebate",
            "recommendedTerritoryPaymentSpeed",
            "actionTimeline",
            "keyFlags",
        ):
            summary.pop(key, None)

    data.pop("alternativeStrategy", None)
    data.pop("scriptIntelligence", None)
    data.pop("scriptStats", None)
    data.pop("dimensionVerdicts", None)
    data["nextSteps"] = []

    # Remove completely — no useful structural preview for free users
    data.pop("investorSummary", None)
    data.pop("territoryDeepDives", None)
    data.pop("comparables", None)
    data.pop("fundingOpportunities", None)
    data.pop("festivalRecommendations", None)
    data.pop("distributorRecommendations", None)
    data.pop("scriptOriginCallout", None)

    # locationRankings: show the top territory details; show locked placeholders
    # for additional ranked territories without leaking their names or scores.
    if "locationRankings" in data:
        stripped = []
        for idx, loc in enumerate(rankings_raw[:3]):
            if idx > 0:
                stripped.append({
                    "name": f"Territory #{idx + 1}",
                    "country": "Locked",
                    "score": None,
                    "lockedPreview": True,
                    "isAssessmentOnly": True,
                })
                continue
            loc_copy = dict(loc)
            loc_copy["isAssessmentOnly"] = True
            for key in (
                "reasoning",
                "keyAdvantages",
                "keyRisks",
                "rebatePercent",
                "rebateAmount",
                "paymentSpeed",
                "culturalTestLikelihood",
                "adminComplexity",
                "financialReturnScore",
                "financialReturnVerdict",
                "scheduleViabilityScore",
                "contingencyDaysEstimate",
                "costEfficiencyWeatherPenalty",
            ):
                loc_copy.pop(key, None)
            stripped.append(loc_copy)
        data["locationRankings"] = stripped

    # incentiveEstimates: keep territory + program, strip all financial values
    incentive_estimates = dict_rows(data.get("incentiveEstimates"))
    if incentive_estimates:
        data["incentiveEstimates"] = [
            {"territory": inc.get("territory", ""), "program": inc.get("program", "")}
            for inc in incentive_estimates
        ]
    else:
        data.pop("incentiveEstimates", None)

    # financialAnalysis: keep territory + programme per scenario, strip values
    fa = data.get("financialAnalysis") or {}
    scenarios = dict_rows(fa.get("budgetScenarios")) if isinstance(fa, dict) else []
    if scenarios:
        data["financialAnalysis"] = {
            "budgetScenarios": [
                {"territory": s.get("territory", ""), "programme": s.get("programme", "")}
                for s in scenarios
            ]
        }
    else:
        data.pop("financialAnalysis", None)

    # Premium operational sections are removed entirely for free-tier users.
    # (crewInsights only exists on legacy stored reports — the section was
    # removed platform-wide 2026-07; the pop stays as defence for old data.)
    data.pop("crewInsights", None)
    data.pop("weatherLogistics", None)

    return data


def _preview_urgent_action_count(data: dict) -> int:
    steps = data.get("nextSteps")
    if isinstance(steps, list):
        count = sum(
            1 for step in steps
            if isinstance(step, dict)
            and str(step.get("priority") or "").upper() == "URGENT"
        )
        if count:
            return count

    timeline = (data.get("executiveSummary") or {}).get("actionTimeline")
    if isinstance(timeline, list):
        return len([item for item in timeline if isinstance(item, dict)])
    return 0


def _preview_complexity_factor_count(data: dict) -> int:
    script_intel = data.get("scriptIntelligence")
    if isinstance(script_intel, dict):
        drivers = script_intel.get("complexityDrivers")
        if isinstance(drivers, list) and drivers:
            return len(drivers)

    summary = data.get("executiveSummary")
    if isinstance(summary, dict):
        flags = summary.get("keyFlags")
        if isinstance(flags, list) and flags:
            return len(flags)
    return 0


def _preview_verdict(label: str | None) -> str:
    value = (label or "").strip().upper()
    if value == "BANKABLE":
        return "Bankable"
    if value == "VERIFY FIRST":
        return "Verify First"
    if value in {"NOT BANKABLE", "CAUTION"}:
        return "Caution"
    return "Review Required"


def _first_summary_paragraph(summary: dict | None) -> str | None:
    if not isinstance(summary, dict):
        return None
    key_insights = summary.get("keyInsights")
    if not isinstance(key_insights, str):
        return None
    paragraphs = [p.strip() for p in key_insights.split("\n\n") if p.strip()]
    if not paragraphs:
        return None
    for paragraph in paragraphs:
        if "production overview" in paragraph.lower():
            return paragraph
    return paragraphs[0]


def _redact_preview_financial_text(text: str) -> str:
    import re

    text = re.sub(r"[£€$]\s?\d[\d,]*(?:\.\d+)?\s?(?:[KkMm])?", "[upgrade to see estimate]", text)
    text = re.sub(r"\b\d+(?:\.\d+)?%", "[upgrade to see rate]", text)
    return text


def _build_preview_key_insights(
    data: dict,
    rankings: list[dict],
    urgent_count: int,
    complexity_count: int,
) -> str:
    summary = data.get("executiveSummary") if isinstance(data.get("executiveSummary"), dict) else {}
    overview = _first_summary_paragraph(summary)
    if not overview:
        title = data.get("scriptTitle") or data.get("title") or "this production"
        overview = (
            "**Script Overview**\n"
            f"Prodculator has built a production intelligence preview for {title}, "
            "using the submitted format, budget, territory, and schedule inputs to "
            "identify the leading production-location strategy."
        )
    else:
        overview = _redact_preview_financial_text(overview)

    paragraphs = [overview]

    if rankings:
        top = rankings[0]
        top_name = top.get("name") or summary.get("recommendedTerritory")
        if top_name:
            paragraphs.append(
                "**Primary Recommendation**\n"
                f"{top_name} is the lead territory recommendation. "
                f"Its incentive bankability verdict is {_preview_verdict(top.get('bankabilityLabel'))}. "
                "Upgrade to see the net rate, estimated rebate, qualifying spend, and payment timeline."
            )

    if len(rankings) > 1:
        second = rankings[1]
        second_name = second.get("name")
        if second_name:
            paragraphs.append(
                "**Second Territory**\n"
                f"{second_name} is also in the comparison set with a "
                f"{_preview_verdict(second.get('bankabilityLabel'))} verdict. "
                "Upgrade to see the financial trade-off against the primary recommendation."
            )

    complexity = data.get("complexity") or "production"
    factor_text = (
        f"{complexity_count} specific production factor"
        f"{'' if complexity_count == 1 else 's'}"
        if complexity_count > 0 else "specific production factors"
    )
    paragraphs.append(
        "**Production Complexity Snapshot**\n"
        f"This production carries {complexity} complexity from {factor_text}. "
        "Upgrade to see the full script intelligence analysis."
    )

    if urgent_count > 0:
        action_text = f"{urgent_count} time-sensitive action{'' if urgent_count == 1 else 's'}"
    else:
        action_text = "time-sensitive actions"
    paragraphs.append(
        "**Strategic Recommendations**\n"
        f"This production has {action_text}. Upgrade to see the prioritised action plan."
    )

    return "\n\n".join(paragraphs)


def _apply_watermark(pdf_bytes: bytes) -> bytes:
    """Overlay a diagonal trial watermark on every page of the PDF."""
    import io

    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as rl_canvas

        watermark_text = "Prodculator Trial Report — Upgrade at prodculator.com"

        # Build a single-page watermark PDF using reportlab
        packet = io.BytesIO()
        width, height = A4
        c = rl_canvas.Canvas(packet, pagesize=(width, height))
        c.saveState()
        c.setFont("Helvetica", 28)
        c.setFillColorRGB(0.6, 0.6, 0.6, alpha=0.35)
        c.translate(width / 2, height / 2)
        c.rotate(45)
        c.drawCentredString(0, 0, watermark_text)
        c.restoreState()
        c.save()
        packet.seek(0)
        watermark_pdf = PdfReader(packet)
        watermark_page = watermark_pdf.pages[0]

        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            page.merge_page(watermark_page)
            writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()
    except Exception:
        logger.warning("PDF watermarking failed — returning original PDF bytes")
        return pdf_bytes


@router.get("/{report_id}/export-excel")
async def export_excel(
    report_id: str,
    user: AuthUser = Depends(RequirePlan("producer")),
    service: ReportService = Depends(get_report_service),
):
    """Export the full report data as a multi-sheet Excel workbook (Producer+ only)."""
    from app.modules.reports.excel_service import build_excel_workbook

    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    if not report.get("report_data"):
        raise HTTPException(status_code=422, detail="Report data not yet available — check back once the report has completed processing")

    try:
        workbook_bytes = build_excel_workbook(report)
    except ImportError:
        logger.error("openpyxl is not installed — Excel export unavailable")
        raise HTTPException(status_code=503, detail="Excel export temporarily unavailable")
    except Exception:
        logger.exception("Excel export failed: report_id=%s", report_id)
        raise HTTPException(status_code=500, detail="Failed to generate Excel export")

    script_title = report.get("script_title", "Report")
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in script_title).strip()
    filename = f"Prodculator Export - {safe_title}.xlsx"

    return Response(
        content=workbook_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(workbook_bytes)),
        },
    )


@router.patch("/{report_id}/project-details")
async def update_project_details(
    report_id: str,
    body: UpdateProjectDetailsRequest,
    user: AuthUser = Depends(RequirePlan("producer")),
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Save user-authored project details (creative team, finance plan) for Producer+ users.
    Fields are merged with any existing data — safe to call multiple times with partial payloads.
    """
    try:
        updated = service.update_project_details(
            report_id,
            user.id,
            body.project_details.model_dump(exclude_none=False),
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")

    return _format_report_response(updated, settings, user_plan=user.plan)


@router.get("/{report_id}/investor-summary")
async def download_investor_summary(
    report_id: str,
    user: AuthUser = Depends(RequirePlan("producer")),
    service: ReportService = Depends(get_report_service),
    settings: Settings = Depends(get_settings),
):
    """Generate and stream a 2-page investor summary PDF (Producer+ only)."""
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    pdf_bytes = _generate_investor_summary_pdf(report)
    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="Could not generate investor summary")

    script_title = report.get("script_title", "Report")
    filename = f"Investor Summary - {script_title}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


def _generate_investor_summary_pdf(report: dict) -> bytes | None:
    """Render the investor_summary.html template and produce PDF bytes."""
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    report_data: dict = report.get("report_data") or {}
    project_details: dict = report.get("project_details") or {}

    # --- Territory rankings ---
    location_rankings: list[dict] = report_data.get("locationRankings") or []
    top_loc: dict = location_rankings[0] if location_rankings else {}
    top_territory_name: str = top_loc.get("name", "")

    # --- Executive summary ---
    exec_summary: dict = report_data.get("executiveSummary") or {}

    # --- Financial analysis: find scenario for top territory ---
    financial: dict = report_data.get("financialAnalysis") or {}
    budget_scenarios: list[dict] = financial.get("budgetScenarios") or []
    top_scenario: dict = {}
    for s in budget_scenarios:
        if s.get("territory", "").lower() == top_territory_name.lower():
            top_scenario = s
            break
    if not top_scenario and budget_scenarios:
        top_scenario = budget_scenarios[0]

    net_budget_str: str = (
        exec_summary.get("headlineNetBudget")
        or top_scenario.get("netBudget")
        or exec_summary.get("budget")
        or ""
    )
    top_rebate_pct: str = (
        top_loc.get("rebatePercent")
        or exec_summary.get("recommendedTerritoryRebate")
        or top_scenario.get("rateNet")
        or ""
    )

    # --- Incentive estimates indexed by territory ---
    incentive_estimates: list[dict] = report_data.get("incentiveEstimates") or []
    incentive_by_territory: dict[str, dict] = {}
    for inc in incentive_estimates:
        key = (inc.get("territory") or "").lower()
        if key and key not in incentive_by_territory:
            incentive_by_territory[key] = inc
    top_incentive: dict = incentive_by_territory.get(top_territory_name.lower(), {})
    if not top_incentive and incentive_estimates:
        top_incentive = incentive_estimates[0]

    # --- Production details ---
    # Production details — try productionDetails key first (b2c reports), then fall back to
    # top-level fields on the report_data (standard AI reports store genre/complexity at root)
    # and request_metadata for crew/cast sizes.
    _pd_raw: dict = report_data.get("productionDetails") or {}
    _req_meta: dict = report.get("request_metadata") or {}
    _genres_raw = (
        _pd_raw.get("genres")
        or report_data.get("genres")
        or ([report_data["genre"]] if report_data.get("genre") else None)
        or _req_meta.get("genre")
        or []
    )
    if isinstance(_genres_raw, str):
        _genres_raw = [_genres_raw]
    production_details: dict = {
        "format": _pd_raw.get("format") or report_data.get("format") or _req_meta.get("format") or "",
        "genres": _genres_raw,
        "complexity": _pd_raw.get("complexity") or report_data.get("complexity") or "",
        "estimatedShootingDays": (
            _pd_raw.get("estimatedShootingDays")
            or report_data.get("estimatedShootingDays")
            or exec_summary.get("shootDays")
            or _req_meta.get("shoot_days")
        ),
        "castSize": _pd_raw.get("castSize") or report_data.get("castSize") or _req_meta.get("cast_size") or "",
        "crewSize": _pd_raw.get("crewSize") or report_data.get("crewSize") or _req_meta.get("crew_size") or "",
        "vfxRequirements": _pd_raw.get("vfxRequirements") or report_data.get("vfxRequirements") or "",
    }

    # --- Comparable productions ---
    # Standard AI reports use "comparables"; b2c reports use "comparableProductions"
    comparables: list[dict] = report_data.get("comparableProductions") or report_data.get("comparables") or []

    # --- Grant / funding opportunities ---
    # DB-sourced grants (b2c path) have: title, organization, amount, deadline, territory
    # AI-sourced fundingOpportunities have: name, type, deadline, notes, tier, website
    # Normalize both to the same shape for the template.
    grants_raw: list[dict] = report_data.get("grantOpportunities") or []
    if not grants_raw:
        for fo in (report_data.get("fundingOpportunities") or []):
            if isinstance(fo, dict):
                grants_raw.append({
                    "title": fo.get("name") or fo.get("title") or "",
                    "organization": fo.get("type") or "",
                    "amount": fo.get("tier") or "Varies",
                    "deadline": fo.get("deadline") or "Rolling",
                    "notes": fo.get("notes") or "",
                })
    grants: list[dict] = grants_raw

    # --- Weather logistics for top territory ---
    weather_raw = report_data.get("weatherLogistics") or []
    top_weather: dict = {}
    if isinstance(weather_raw, list):
        for w in weather_raw:
            if isinstance(w, dict) and w.get("territory", "").lower() == top_territory_name.lower():
                top_weather = w
                break
        if not top_weather and weather_raw and isinstance(weather_raw[0], dict):
            top_weather = weather_raw[0]

    # --- Per-territory key risks for risk section ---
    all_territory_risks: list[dict] = []
    for loc in location_rankings[:3]:
        risks = [r for r in (loc.get("keyRisks") or []) if r]
        if risks:
            all_territory_risks.append({"territory": loc.get("name", ""), "risks": risks[:4]})

    # --- Enrich top-3 locations with programme + bankability for comparison table ---
    top_locations_enriched: list[dict] = []
    for loc in location_rankings[:3]:
        t_key = (loc.get("name") or "").lower()
        inc = incentive_by_territory.get(t_key, {})
        programme = inc.get("program") or ""
        if not programme and t_key == top_territory_name.lower():
            programme = top_scenario.get("programme") or ""
        top_locations_enriched.append({
            **loc,
            "programme": programme,
            "bankabilityLabel": loc.get("bankabilityLabel") or inc.get("bankabilityLabel") or "",
        })

    templates_dir = Path(__file__).resolve().parents[2] / "templates" / "pdf"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("investor_summary.html")
    html = template.render(
        script_title=report.get("script_title", "Untitled"),
        created_at=str(report.get("created_at", ""))[:10],
        exec_summary=exec_summary,
        net_budget_str=net_budget_str,
        top_rebate_pct=top_rebate_pct,
        top_loc=top_loc,
        top_territory_name=top_territory_name,
        top_scenario=top_scenario,
        top_incentive=top_incentive,
        top_weather=top_weather,
        top_locations=top_locations_enriched,
        production_details=production_details,
        key_flags=exec_summary.get("keyFlags") or [],
        action_timeline=exec_summary.get("actionTimeline") or [],
        comparables=comparables[:4],
        grants=grants[:4],
        all_territory_risks=all_territory_risks,
        project_details=project_details,
    )
    html = strip_em_dashes(html)

    try:
        from weasyprint import HTML as WeasyHTML  # type: ignore
        pdf = WeasyHTML(string=html).write_pdf()
        return pdf
    except Exception as exc:
        logger.warning("Investor summary PDF generation failed: %s", exc)
        return None


def _format_report_response(report: dict, settings: Settings, user_plan: str = "free") -> dict:
    """Format a DB report row into the API response shape.

    Territory rankings and premium sections are filtered based on plan level:
    - free: top 3 territories, no premium sections, no pdfUrl
    - professional: top 5 territories, full 13-section report
    - producer/studio: all territories, full report + investorSummary
    """
    import copy

    analysis = report.get("report_data")
    is_free = user_plan == "free"
    # Producer and Studio get investorSummary; Free and Professional do not
    has_investor_summary = user_plan in ("producer", "studio")

    if analysis:
        analysis = copy.deepcopy(analysis)
        # Apply territory cap for the plan
        territory_limit = _TERRITORY_LIMITS.get(user_plan)
        if territory_limit is not None and "locationRankings" in analysis:
            analysis["locationRankings"] = analysis["locationRankings"][:territory_limit]

        if is_free:
            analysis = _build_free_tier_report_data(analysis)
        elif not has_investor_summary:
            # Professional: full report but no Investor Summary section
            analysis.pop("investorSummary", None)

    return {
        "id": report["id"],
        "title": report.get("script_title", ""),
        "reportType": report.get("report_type", "paid"),
        "createdAt": str(report.get("created_at", "")),
        "analysis": analysis,
        # Free users get a sentinel so the frontend shows the download button.
        # The actual PDF is generated on-the-fly by download_pdf; the S3 URL is not exposed.
        "pdfUrl": "preview" if is_free else _resolve_pdf_url(report.get("pdf_url"), settings),
        "userPlan": user_plan,
        # Expose share_token so the frontend knows whether a share link exists.
        # None means no active share link.
        "shareToken": report.get("share_token"),
        "projectDetails": report.get("project_details"),
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



def _dispatch_report_job(
    *,
    background_tasks: BackgroundTasks,
    report_id: str,
    user_id: str,
    user_email: str,
    script_text: str,
    filename: str,
    settings: Settings,
) -> None:
    """Hand report generation off to a worker.

    When the durable queue is enabled (``REPORT_QUEUE_ENABLED``), the job is
    enqueued on RQ and processed by a separate worker process — so it survives a
    web-process restart. Otherwise it falls back to in-process FastAPI
    ``BackgroundTasks`` (the legacy path), which keeps local dev and the test
    suite working without a running worker or Redis.

    Either way the report row is the durable record of progress; the API only
    ever creates the job and polls status.
    """
    queue = get_report_queue(settings)
    if queue is not None:
        # Enqueue by function reference (RQ stores its dotted path). Args are
        # plain strings — Settings is intentionally NOT passed, since the worker
        # re-resolves it from its own environment inside process_report_task.
        queue.enqueue(
            process_report_task,
            args=(report_id, user_id, user_email, script_text, filename),
            job_timeout=settings.REPORT_QUEUE_JOB_TIMEOUT,
            description=f"report:{report_id}",
        )
        logger.info("Enqueued report job on RQ: report_id=%s queue=%s", report_id, queue.name)
        return

    # Legacy in-process path — referenced by bare name so tests can monkeypatch it.
    background_tasks.add_task(
        process_report_task,
        report_id,
        user_id,
        user_email,
        script_text,
        filename,
    )


def process_report_task(
    report_id: str,
    user_id: str,
    user_email: str,
    script_text: str,
    script_filename: str,
) -> None:
    """Background task: analyse pre-extracted script text -> generate report -> upload PDF to S3.

    The script file is **never written to disk or S3**. The caller extracts text in the
    request handler and passes only the plain-text string here.

    Settings are resolved here (rather than passed in) so the function is safe to
    enqueue on RQ: the worker re-imports this module and loads settings from its
    own environment.
    """
    settings = get_settings()
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

            # Never charge for a report that didn't generate. The failed row is
            # already excluded from the quota count; if it also consumed a
            # pay-per-report credit, hand that credit back.
            if ((report_row or {}).get("request_metadata") or {}).get("_credit_consumed"):
                try:
                    from app.modules.subscriptions.service import SubscriptionService

                    SubscriptionService(supabase).refund_report_credit(user_id)
                    logger.info(
                        "Refunded pay-per-report credit after failure: report_id=%s user_id=%s",
                        report_id,
                        user_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to refund pay-per-report credit: report_id=%s user_id=%s",
                        report_id,
                        user_id,
                    )
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
