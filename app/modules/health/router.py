from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.territories import Territory

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
        "sendgrid_configured": bool(settings.SENDGRID_API_KEY),
    }


@router.get("/territories")
async def list_territories():
    """Return every territory the platform covers.

    Returns a flat list of objects with ``label``, ``iso``, ``parent``
    (null for top-level countries), and ``isSubTerritory`` for each
    Territory enum member.  The frontend can use this for drop-downs,
    filters, and the report-creation form without hardcoding values.
    """
    return [
        {
            "label": t.label,
            "iso": t.iso,
            "parent": t.parent.label if t.parent else None,
            "isSubTerritory": t.is_sub_territory,
        }
        for t in Territory
    ]
