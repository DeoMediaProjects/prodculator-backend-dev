import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.cache import close_redis, init_redis
from app.core.config import get_settings
from app.core.database_client import create_client
from app.core.db import init_db
from app.core.limiter import limiter
from app.core.scheduler import start_scheduler, stop_scheduler
from app.modules.scraper.service import ScraperService

logger = logging.getLogger(__name__)
from app.modules.admin.router import router as admin_router
from app.modules.admin.auth_router import router as admin_auth_router
from app.modules.admin.admin_users_router import router as admin_users_router
from app.modules.festivals.admin_router import router as festivals_admin_router
from app.modules.incentives.admin_router import router as incentives_admin_router
from app.modules.crew_costs.admin_router import router as crew_costs_admin_router
from app.modules.grants.admin_router import router as grants_admin_router
from app.modules.subscribers.admin_router import router as subscribers_admin_router
from app.modules.data_sources.admin_router import router as data_sources_admin_router
from app.modules.email_gating.admin_router import router as email_gating_admin_router
from app.modules.pdf_reports.admin_router import router as pdf_reports_admin_router
from app.modules.auth.router import router as auth_router
from app.modules.email.router import router as admin_email_router
from app.modules.health.router import router as health_router
from app.modules.payments.router import (
    router as payments_router,
    webhook_router as payments_webhook_router,
)
from app.modules.grants.router import router as grants_router
from app.modules.festivals.router import router as festivals_router
from app.modules.reports.router import router as reports_router
from app.modules.scripts.router import router as scripts_router
from app.modules.subscriptions.router import router as subscriptions_router
from app.modules.watchlist.router import router as watchlist_router

settings = get_settings()


def configure_logging() -> None:
    requested_level = (settings.LOG_LEVEL or ("DEBUG" if settings.DEBUG else "INFO")).upper()
    level = getattr(logging, requested_level, logging.INFO)

    # Uvicorn provides handlers; we align logger levels so app logs are visible.
    logging.getLogger().setLevel(level)
    logging.getLogger("app").setLevel(level)


configure_logging()


@asynccontextmanager
async def lifespan(app_: FastAPI):
    # Startup
    init_redis(settings)
    if settings.AUTO_CREATE_DB_SCHEMA:
        init_db()
    # Seed scrape sources on startup
    db = create_client()
    try:
        ScraperService(db, settings).seed_sources()
    finally:
        db.close()
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()
    await close_redis()


app = FastAPI(
    title="Prodculator API",
    version="1.0.0",
    description="Production Intelligence Platform Backend",
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url="/api/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)



def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    # Register a global Bearer token security scheme
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    # Apply it to every operation so Swagger shows the lock icon everywhere
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation.setdefault("security", [{"BearerAuth": []}])
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

# Routers
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(scripts_router)
app.include_router(reports_router)
app.include_router(payments_router)
app.include_router(payments_webhook_router)
app.include_router(grants_router)
app.include_router(festivals_router)
app.include_router(watchlist_router)
app.include_router(subscriptions_router)
app.include_router(admin_auth_router)
app.include_router(admin_router)
app.include_router(admin_users_router)
app.include_router(festivals_admin_router)
app.include_router(incentives_admin_router)
app.include_router(crew_costs_admin_router)
app.include_router(grants_admin_router)
app.include_router(admin_email_router)
app.include_router(subscribers_admin_router)
app.include_router(data_sources_admin_router)
app.include_router(email_gating_admin_router)
app.include_router(pdf_reports_admin_router)
