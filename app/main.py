from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import init_db
from app.modules.admin.router import router as admin_router
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
from app.modules.storage.router import router as storage_router
from app.modules.subscriptions.router import router as subscriptions_router
from app.modules.watchlist.router import router as watchlist_router

settings = get_settings()

app = FastAPI(
    title="Prodculator API",
    version="1.0.0",
    description="Production Intelligence Platform Backend",
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url="/api/redoc" if settings.DEBUG else None,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event() -> None:
    if settings.AUTO_CREATE_DB_SCHEMA:
        init_db()

# Routers
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(scripts_router)
app.include_router(storage_router)
app.include_router(reports_router)
app.include_router(payments_router)
app.include_router(payments_webhook_router)
app.include_router(grants_router)
app.include_router(festivals_router)
app.include_router(watchlist_router)
app.include_router(subscriptions_router)
app.include_router(admin_router)
app.include_router(admin_email_router)
