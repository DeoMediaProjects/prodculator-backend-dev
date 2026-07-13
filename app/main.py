import logging
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.auth_cookies import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER
from app.core.cache import close_redis, init_redis
from app.core.config import get_settings
from app.core.database_client import create_client
from app.core.db import init_db
from app.core.limiter import limiter
from app.core.logging_config import configure_logging, request_id_ctx
from app.core.scheduler import start_scheduler, stop_scheduler
from app.modules.payments.plan_catalog import find_missing_price_ids
from app.modules.scraper.service import ScraperService

logger = logging.getLogger(__name__)
from app.modules.admin.router import router as admin_router
from app.modules.admin.auth_router import router as admin_auth_router
from app.modules.admin.admin_users_router import router as admin_users_router
from app.modules.distributors.admin_router import router as distributors_admin_router
from app.modules.territory_profiles.admin_router import router as territory_profiles_admin_router
from app.modules.festivals.admin_router import router as festivals_admin_router
from app.modules.incentives.admin_router import router as incentives_admin_router
from app.modules.grants.admin_router import router as grants_admin_router
from app.modules.subscribers.admin_router import router as subscribers_admin_router
from app.modules.data_sources.admin_router import router as data_sources_admin_router
from app.modules.email_gating.admin_router import router as email_gating_admin_router
from app.modules.pdf_reports.admin_router import router as pdf_reports_admin_router
from app.modules.auth.router import router as auth_router
from app.modules.email.router import router as admin_email_router
from app.modules.email.transactional_router import router as transactional_email_router
from app.modules.health.router import router as health_router
from app.modules.payments.router import (
    router as payments_router,
    webhook_router as payments_webhook_router,
)
from app.modules.grants.router import router as grants_router
from app.modules.festivals.router import router as festivals_router
from app.modules.distributors.router import router as distributors_router
from app.modules.reports.router import router as reports_router
from app.modules.scripts.router import router as scripts_router
from app.modules.subscriptions.router import router as subscriptions_router
from app.modules.watchlist.router import router as watchlist_router
from app.modules.calculator.router import router as calculator_router
from app.modules.territories.router import router as territories_router
from app.modules.milestones.router import router as milestones_router
from app.modules.support.router import router as support_router
from app.modules.contact.router import router as contact_router
from app.modules.b2b.router import router as b2b_router
from app.modules.b2b.admin_router import router as b2b_admin_router

settings = get_settings()

configure_logging(settings)


def _init_sentry() -> None:
    """Initialise Sentry error monitoring. No-op when SENTRY_DSN is unset or the
    SDK isn't installed, so local dev and the test suite are unaffected."""
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; skipping init.")
        return
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT or settings.APP_ENV,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
    )
    logger.info("Sentry error monitoring initialised (env=%s)", settings.SENTRY_ENVIRONMENT or settings.APP_ENV)


_init_sentry()


@asynccontextmanager
async def lifespan(app_: FastAPI):
    # Startup
    init_redis(settings)
    if settings.AUTO_CREATE_DB_SCHEMA:
        init_db()
    if settings.SCRAPER_ENABLED:
        # Seed scrape sources on startup (skip gracefully if table missing)
        db = create_client()
        try:
            ScraperService(db, settings).seed_sources()
        except Exception as e:
            logger.warning("Could not seed scrape sources: %s", e)
        finally:
            db.close()
    # Surface any missing Stripe price IDs loudly at boot. We warn rather than
    # crash so a missing annual price can't take the whole API down, but the gap
    # is visible before it strands a customer's plan change.
    missing_prices = find_missing_price_ids(settings)
    if missing_prices:
        logger.warning(
            "Stripe price config incomplete — plan resolution will fail for: %s. "
            "Set the corresponding STRIPE_PRICE_* environment variables.",
            ", ".join(missing_prices),
        )
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


_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Public auth-bootstrap endpoints. These establish or refresh a session and carry
# their own credentials/tokens in the request body, so they are not CSRF-relevant
# (an attacker cannot forge the password or ID token). They must keep working even
# when the browser still holds a stale auth cookie from a prior session — at which
# point the user has no CSRF token yet, so enforcing the check here would wrongly
# 403 a legitimate fresh sign-in.
_CSRF_EXEMPT_PATHS = frozenset({
    "/api/auth/signin",
    "/api/auth/signup",
    "/api/auth/google",
    "/api/auth/refresh",
    "/api/auth/verify-email",
    "/api/auth/resend-verification",
    "/api/auth/reset-password",
    "/api/auth/reset-password/confirm",
})


# Defined before CORS so CORS remains the outermost middleware — a rejected
# request still receives CORS headers and the browser can read the 403.
@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    """Double-submit CSRF check for cookie-authenticated, state-changing requests.

    Only applies when the request is authenticated via the httpOnly access cookie
    AND carries no Authorization header. Bearer/API clients are not vulnerable to
    CSRF (the browser never auto-attaches a Bearer token), so they are exempt —
    which is also why the existing header-authenticated test suite is unaffected.

    Public auth-bootstrap endpoints (see ``_CSRF_EXEMPT_PATHS``) are also exempt so
    a stale auth cookie from a prior session cannot block a fresh sign-in.
    """
    if (
        settings.AUTH_COOKIE_ENABLED
        and request.method not in _CSRF_SAFE_METHODS
        and request.url.path not in _CSRF_EXEMPT_PATHS
        and request.cookies.get(ACCESS_COOKIE)
        and not request.headers.get("authorization")
    ):
        header_token = request.headers.get(CSRF_HEADER)
        cookie_token = request.cookies.get(CSRF_COOKIE)
        if not header_token or not cookie_token or not secrets.compare_digest(header_token, cookie_token):
            return JSONResponse(status_code=403, content={"detail": "CSRF token missing or invalid"})
    return await call_next(request)


# Correlate every log line emitted while handling a request with a request ID.
# Honour an inbound X-Request-ID (e.g. from the reverse proxy) or mint one, and
# echo it back so clients/proxies can stitch logs together.
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    token = request_id_ctx.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_ctx.reset(token)
    response.headers.setdefault("X-Request-ID", rid)
    return response


# Security headers. Added before CORS so CORS stays the outermost middleware.
# This is a JSON API: a strict CSP is safe in production (interactive Swagger is
# only mounted when DEBUG, where we relax it). HSTS is emitted only outside DEBUG
# so local http dev isn't pinned to HTTPS.
_API_CSP_STRICT = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


# slowapi fail-open guard. When the rate-limit storage backend (Redis) is
# unreachable, `swallow_errors=True` swallows the error inside the limiter, but
# its endpoint wrapper still unconditionally reads `request.state.view_rate_limit`
# to emit rate-limit headers — raising AttributeError (→ 500) because the limiter
# never got far enough to set it. Pre-seeding it to None makes the limiter
# genuinely fail OPEN on a Redis blip (header injection no-ops on None) instead of
# 500ing; when Redis is healthy the limiter overwrites this with the real value.
@app.middleware("http")
async def rate_limit_failopen(request: Request, call_next):
    request.state.view_rate_limit = None
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if not settings.DEBUG:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=63072000; includeSubDomains; preload",
        )
        response.headers.setdefault("Content-Security-Policy", _API_CSP_STRICT)
    return response


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
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
app.include_router(distributors_router)
app.include_router(watchlist_router)
app.include_router(subscriptions_router)
app.include_router(calculator_router)
app.include_router(territories_router)
app.include_router(milestones_router)
app.include_router(support_router)
app.include_router(contact_router)
app.include_router(b2b_router)
app.include_router(admin_auth_router)
app.include_router(admin_router)
app.include_router(admin_users_router)
app.include_router(festivals_admin_router)
app.include_router(distributors_admin_router)
app.include_router(territory_profiles_admin_router)
app.include_router(incentives_admin_router)
app.include_router(grants_admin_router)
app.include_router(admin_email_router)
app.include_router(transactional_email_router)
app.include_router(subscribers_admin_router)
app.include_router(data_sources_admin_router)
app.include_router(email_gating_admin_router)
app.include_router(pdf_reports_admin_router)
app.include_router(b2b_admin_router)
