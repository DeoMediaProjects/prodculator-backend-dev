import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.cache import get_redis
from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.territories import Territory, resolve_territory
from app.modules.reports.format_eligibility import (
    LABELS,
    UNCONFIRMED_VERDICTS,
    UNVERIFIED,
    evaluate_format_eligibility,
    verdict_rank,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Health"])


@router.get("/health")
async def health_check(settings: Settings = Depends(get_settings)):
    """Liveness probe — confirms the process is up and which integrations are
    configured. Cheap and dependency-free; use /health/ready for readiness."""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "database_configured": bool(settings.DB_URL),
        "anthropic_configured": bool(settings.ANTHROPIC_API_KEY),
        "stripe_configured": bool(settings.STRIPE_SECRET_KEY),
        "brevo_configured": bool(settings.BREVO_API_KEY),
    }


@router.get("/health/ready")
async def readiness_check(db: DatabaseClient = Depends(get_supabase)):
    """Readiness probe for orchestration/load balancers — actually exercises the
    backing services rather than just reporting config presence.

    The database is required: if it's unreachable we return 503 so the instance
    is pulled from rotation. Redis and storage are best-effort (the app degrades
    without them), so a failure there is reported but does not fail readiness —
    pulling the whole API over unreachable PDF storage would be worse than the
    problem it signals.
    """
    checks: dict[str, object] = {"database": False, "redis": False, "storage": False}

    try:
        db.session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as e:  # noqa: BLE001 - report any failure, don't crash the probe
        logger.warning("Readiness: database check failed: %s", e)

    try:
        await get_redis().ping()
        checks["redis"] = True
    except Exception as e:  # noqa: BLE001 - Redis is non-critical; report and continue
        logger.warning("Readiness: redis check failed: %s", e)

    # Storage is checked here rather than at first use because report PDFs upload
    # at the end of a multi-minute pipeline: without this, a bad bucket name,
    # region or IAM policy only surfaces after a full paid analysis has run.
    storage_ok, storage_detail = await run_in_threadpool(db.storage.preflight)
    checks["storage"] = storage_ok
    checks["storage_detail"] = storage_detail
    if not storage_ok:
        logger.error("Readiness: storage check failed: %s", storage_detail)

    ready = checks["database"]
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@router.get("/territories")
async def list_territories(
    supabase: DatabaseClient = Depends(get_supabase),
    include_all: bool = Query(
        default=False,
        description=(
            "Also return territories with no active incentive, flagged via "
            "hasActiveIncentive=false. Used by the intake picker, which asks "
            "where a production is being considered rather than where a rebate "
            "can be computed."
        ),
    ),
    production_format: str | None = Query(
        default=None,
        alias="format",
        description=(
            "Production format (e.g. 'Short'). When given, each territory also "
            "carries formatEligibility describing the best verdict its programmes "
            "can offer that format. Lets the intake warning be driven by the "
            "programme data instead of by the format alone, so it disappears once "
            "the eligibility research is populated."
        ),
    ),
):
    """Return only territories the platform has active incentive coverage for.

    A territory is included only when it has at least one active,
    non-supplementary row in ``incentive_programs``.  Supplementary-only
    territories (e.g. British Columbia PSTC, Scotland Creative Scotland fund,
    Bavaria FFF) are intentionally excluded — those credits stack on top of a
    parent-territory programme and are never a valid standalone selection.

    Each object contains:
      - ``label``         canonical territory name (matches DB ``territory`` column)
      - ``iso``           ISO 3166-1 alpha-2 code (null if not in Territory enum)
      - ``parent``        parent territory label for sub-territories, else null
      - ``isSubTerritory``  true for states/provinces/regions
    """
    columns = "territory, is_supplementary, status"
    if production_format:
        columns += (
            ", applicable_formats, format_eligibility_status, format_conditions,"
            " format_source_url, format_verified_at"
        )
    rows = (
        supabase.table("incentive_programs")
        .select(columns)
        .execute()
        .data or []
    )
    return _territory_rows_to_options(
        rows, include_all=include_all, production_format=production_format,
    )


def _format_eligibility_summary(
    rows: list[dict[str, Any]],
    production_format: str,
) -> dict[str, Any]:
    """Summarise what the best of *rows* can offer *production_format*.

    Per-territory rather than per-programme because the picker selects territories,
    but derived from the per-programme verdicts so it can never claim more than the
    underlying records support. ``status`` is the best verdict available, on the same
    ranking ``best_incentive`` uses: a territory with one verified programme is
    eligible whatever else sits on its record, and a territory is only reported
    unverified when nothing in it can do better.
    """
    verdicts = [
        evaluate_format_eligibility(r, production_format)["verdict"]
        for r in rows
        if not r.get("is_supplementary")
    ]
    if not verdicts:
        return {"status": UNVERIFIED, "label": LABELS[UNVERIFIED],
                "programmes": 0, "unverified": 0}
    best = max(verdicts, key=verdict_rank)
    return {
        "status": best,
        "label": LABELS.get(best, LABELS[UNVERIFIED]),
        "programmes": len(verdicts),
        "unverified": sum(1 for v in verdicts if v in UNCONFIRMED_VERDICTS),
    }


def _territory_rows_to_options(
    rows: list[dict[str, Any]],
    *,
    include_all: bool = False,
    production_format: str | None = None,
) -> list[dict[str, Any]]:
    """Classify ``incentive_programs`` *rows* into territory picker options.

    Pure, so the classification can be tested without a database.
    """
    # Collect distinct territories with at least one active non-supplementary row.
    # Mirrors the service's active-row logic: status = 'active', '' (empty), or NULL.
    #
    # A second set is collected from the same rows: territories that DO hold a
    # programme record, but not one whose bankability can be confirmed today
    # (suspended, or awaiting admin verification). "No incentive at all" and "an
    # incentive we cannot vouch for" are different facts, and one boolean told a
    # producer the same thing about a suspended programme as about a territory
    # that has never had one.
    covered: set[str] = set()
    unconfirmed: set[str] = set()
    # Programme rows per picker label, for the format summary. A parent country
    # inherits its sub-territories' rows because selecting "United States" makes
    # every covered state's programme a candidate.
    by_label: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        label = r.get("territory")
        if not label:
            continue
        status = (r.get("status") or "").lower()
        if production_format:
            by_label.setdefault(label, []).append(r)
            owner = resolve_territory(label)
            if owner and owner.parent:
                by_label.setdefault(owner.parent.label, []).append(r)
        if r.get("is_supplementary"):
            # Supplementary uplifts stack onto a primary programme and are never
            # a standalone selection, so they say nothing about this question.
            continue
        if status not in ("active", ""):
            if status != "no_programme":
                unconfirmed.add(label)
                parent = resolve_territory(label)
                if parent and parent.parent:
                    unconfirmed.add(parent.parent.label)
            continue
        covered.add(label)
        # Also surface the parent country so users can select e.g. "United States"
        # and have the builder expand to the best covered state.
        t = resolve_territory(label)
        if t and t.parent:
            covered.add(t.parent.label)

    # `include_all` adds the territories the platform knows but has no ACTIVE
    # incentive for: South Africa (DTIC programme suspended since March 2024),
    # Nigeria (no formal rebate) and Brazil (pending admin verification).
    #
    # The intake picker asks where a production is being CONSIDERED, which is a
    # different question from where a rebate can be computed. Excluding them
    # there contradicts the guidance on the records themselves ("never write off
    # South Africa as a territory, write off the incentive until reinstated") and
    # it silently drops that demand from the Business Intelligence signal pool.
    # Rebate-ranking callers keep the default, covered-only list.
    labels = set(covered)
    if include_all:
        labels |= {
            t.label for t in Territory
            if t.iso != "EU" and not t.is_sub_territory
        }

    result = []
    for label in sorted(labels):
        t = resolve_territory(label)
        option: dict[str, Any] = {
            "label": label,
            "iso": t.iso if t else None,
            "parent": t.parent.label if t and t.parent else None,
            "isSubTerritory": t.is_sub_territory if t else False,
            # Additive, so existing consumers are unaffected. False means the
            # territory is selectable but carries no bankable incentive today.
            "hasActiveIncentive": label in covered,
            # Three-state, because the boolean above cannot tell a suspended or
            # unverified programme apart from no programme at all:
            #   active       a bankable incentive can be modelled
            #   unconfirmed  a programme exists, its bankability is not confirmed
            #   none         no programme on record
            # Coverage wins: a country with one active programme and one suspended
            # one has something bankable to model, whatever else is on its record.
            "incentiveStatus": (
                "active" if label in covered
                else "unconfirmed" if label in unconfirmed
                else "none"
            ),
        }
        # Only present when a format was asked about, so existing consumers see an
        # unchanged payload.
        if production_format:
            option["formatEligibility"] = _format_eligibility_summary(
                by_label.get(label, []), production_format,
            )
        result.append(option)

    return result
