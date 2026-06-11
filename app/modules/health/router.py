from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.territories import resolve_territory

router = APIRouter(prefix="/api", tags=["Health"])


@router.get("/health")
async def health_check(settings: Settings = Depends(get_settings)):
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "database_configured": bool(settings.DB_URL),
        "anthropic_configured": bool(settings.ANTHROPIC_API_KEY),
        "stripe_configured": bool(settings.STRIPE_SECRET_KEY),
        "brevo_configured": bool(settings.BREVO_API_KEY),
    }


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
