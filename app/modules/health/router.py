from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

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
