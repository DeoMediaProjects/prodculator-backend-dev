import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.cache import get_redis
from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.territories import resolve_territory

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
    is pulled from rotation. Redis is best-effort (the app degrades gracefully
    without it), so a Redis failure is reported but does not fail readiness.
    """
    checks = {"database": False, "redis": False}

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

    ready = checks["database"]
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@router.get("/territories")
async def list_territories(supabase: DatabaseClient = Depends(get_supabase)):
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
    rows = (
        supabase.table("incentive_programs")
        .select("territory, is_supplementary, status")
        .execute()
        .data or []
    )

    # Collect distinct territories with at least one active non-supplementary row.
    # Mirrors the service's active-row logic: status = 'active', '' (empty), or NULL.
    covered: set[str] = set()
    for r in rows:
        label = r.get("territory")
        if not label:
            continue
        status = (r.get("status") or "").lower()
        if status not in ("active", ""):
            continue
        if r.get("is_supplementary"):
            continue
        covered.add(label)
        # Also surface the parent country so users can select e.g. "United States"
        # and have the builder expand to the best covered state.
        t = resolve_territory(label)
        if t and t.parent:
            covered.add(t.parent.label)

    result = []
    for label in sorted(covered):
        t = resolve_territory(label)
        result.append({
            "label": label,
            "iso": t.iso if t else None,
            "parent": t.parent.label if t and t.parent else None,
            "isSubTerritory": t.is_sub_territory if t else False,
        })

    return result
