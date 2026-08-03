from __future__ import annotations

import logging

import stripe as stripe_lib
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.modules.admin.schemas import AdminUser
from app.modules.b2b.schemas import (
    AdminB2BManualSubscriptionCreate,
    AdminB2BRequestListResponse,
    AdminB2BResendResponse,
    AdminB2BSubscriptionUpdate,
    B2BIntelligenceRequestResponse,
    B2BSubscriptionListResponse,
    B2BSubscriptionResponse,
)
from app.modules.b2b.service import B2B_PRODUCTS, B2BService
from app.modules.payments.service import StripeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/b2b", tags=["Admin B2B"])


def get_b2b_service(
    db: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> B2BService:
    return B2BService(db, settings)


class B2BTestCheckoutRequest(BaseModel):
    """Admin-only: mint a compressed-cycle B2B test checkout for a target user."""
    user_email: str
    product_type: str
    currency: str = "gbp"  # gbp | usd


@router.post("/test/checkout")
async def create_b2b_test_checkout(
    body: B2BTestCheckoutRequest,
    _admin: AdminUser = Depends(RequirePermission("canManageAdmins")),
    settings: Settings = Depends(get_settings),
    supabase: DatabaseClient = Depends(get_supabase),
    service: B2BService = Depends(get_b2b_service),
):
    """Mint a B2B Checkout URL that bills a target user a token amount on the
    normal monthly cycle and auto-refunds every charge. LIVE money,
    master-admin only, inert unless STRIPE_TEST_BILLING_ENABLED is on."""
    if not settings.STRIPE_TEST_BILLING_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    if body.product_type not in B2B_PRODUCTS:
        raise HTTPException(status_code=404, detail="B2B product not found")

    email = body.user_email.strip().lower()
    user_rows = (
        supabase.table("users").select("id,email").eq("email", email).limit(1).execute().data or []
    )
    if not user_rows:
        raise HTTPException(status_code=404, detail=f"No user account found for {email}")
    target = user_rows[0]

    price_id = service.get_price_id(body.product_type, body.currency)
    if not price_id:
        raise HTTPException(
            status_code=400,
            detail=f"No Stripe price configured for {body.product_type} in {body.currency.upper()}",
        )

    try:
        result = StripeService(settings).create_b2b_subscription_checkout(
            price_id=price_id,
            user_email=target["email"],
            user_id=target["id"],
            product_type=body.product_type,
            currency=body.currency,
            delivery_frequency="monthly",
            test_billing=True,
        )
        logger.info(
            "B2B test-billing checkout minted for user=%s product=%s by admin=%s",
            target["id"], body.product_type, _admin.id,
        )
        return result
    except stripe_lib.StripeError:
        logger.exception("Stripe error minting B2B test checkout for %s", email)
        raise HTTPException(status_code=400, detail="Payment processing failed")
    except Exception:
        logger.exception("Unexpected error minting B2B test checkout for %s", email)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/subscriptions", response_model=B2BSubscriptionListResponse)
async def list_subscriptions(
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    rows = (
        service.db.table("b2b_subscriptions")
        .select("*")
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    return {"items": rows}


@router.post("/subscriptions", response_model=B2BSubscriptionResponse)
async def create_manual_subscription(
    body: AdminB2BManualSubscriptionCreate,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    try:
        return service.create_manual_subscription(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/subscriptions/{subscription_id}", response_model=B2BSubscriptionResponse)
async def update_subscription(
    subscription_id: str,
    body: AdminB2BSubscriptionUpdate,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    subscription = service.update_subscription(subscription_id, body.model_dump(exclude_unset=True))
    if not subscription:
        raise HTTPException(status_code=404, detail="B2B subscription not found")
    return subscription


@router.get("/requests", response_model=AdminB2BRequestListResponse)
async def list_requests(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    items, total = service.list_requests(limit=limit, offset=offset, include_metrics=True)
    return {"items": items, "total": total}


@router.get("/requests/{request_id}", response_model=B2BIntelligenceRequestResponse)
async def get_request(
    request_id: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    request = service.get_request(request_id, include_metrics=True)
    if not request:
        raise HTTPException(status_code=404, detail="B2B request not found")
    return request


@router.post("/requests/{request_id}/resend", response_model=AdminB2BResendResponse)
async def resend_request(
    request_id: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    request = service.get_request(request_id, include_metrics=True)
    if not request:
        raise HTTPException(status_code=404, detail="B2B request not found")
    if request.get("status") != "completed" or not request.get("pdf_url"):
        raise HTTPException(status_code=409, detail="Only completed B2B requests with PDFs can be resent")

    try:
        recipients = service.deliver_request_pdf(request)
    except Exception:
        logger.exception("B2B resend failed: request_id=%s", request_id)
        raise HTTPException(status_code=500, detail="Failed to resend B2B PDF")
    return {"sent": True, "recipients": recipients}


@router.get("/requests/{request_id}/pdf")
async def download_request_pdf(
    request_id: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    request = service.get_request(request_id, include_metrics=True)
    if not request:
        raise HTTPException(status_code=404, detail="B2B request not found")
    if request.get("status") != "completed" or not request.get("pdf_url"):
        raise HTTPException(status_code=404, detail="PDF not available")

    try:
        pdf_bytes = service.download_request_pdf(request)
    except Exception:
        logger.warning("Admin B2B PDF download failed: request_id=%s", request_id)
        raise HTTPException(status_code=404, detail="PDF not found")

    filename = f"B2B Intelligence - {request['product_type']} - {request['period_start']} to {request['period_end']}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ---------------------------------------------------------------------------
# Package assembly (admin data-pull layer)
# ---------------------------------------------------------------------------
from datetime import date as _date  # noqa: E402

from pydantic import BaseModel  # noqa: E402

from app.modules.b2b.composer_service import (  # noqa: E402
    ConsentGrantRefused,
    PackageTemplateService,
    SignalPoolService,
    TemplateNameConflict,
)
from app.modules.b2b.entitlement_service import (  # noqa: E402
    EntitlementConflict,
    EntitlementService,
)
from app.modules.b2b.package_service import SECTION_BY_KEY, PackageService  # noqa: E402


def get_package_service(
    service: B2BService = Depends(get_b2b_service),
) -> PackageService:
    return PackageService(service)


def get_entitlement_service(
    db: DatabaseClient = Depends(get_supabase),
) -> EntitlementService:
    return EntitlementService(db)


def get_template_service(
    db: DatabaseClient = Depends(get_supabase),
) -> PackageTemplateService:
    return PackageTemplateService(db)


def get_signal_pool_service(
    db: DatabaseClient = Depends(get_supabase),
) -> SignalPoolService:
    return SignalPoolService(db)


class PackagePreviewRequest(BaseModel):
    section_keys: list[str]
    period_start: _date
    period_end: _date
    subscription_id: str | None = None


class BespokeGenerateRequest(BaseModel):
    subscription_id: str | None = None
    title: str
    section_keys: list[str]
    period_start: _date
    period_end: _date
    client_name: str | None = None


@router.get("/package/library")
async def package_library(
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    pkg: PackageService = Depends(get_package_service),
):
    """The full section catalogue an admin can compose from (signals + market context)."""
    return {"sections": pkg.library()}


@router.get("/package/template/{product_type}")
async def package_template(
    product_type: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    pkg: PackageService = Depends(get_package_service),
):
    """Ordered default section list for a standard product."""
    return {"product_type": product_type, "section_keys": pkg.product_template(product_type)}


@router.post("/package/preview")
async def package_preview(
    body: PackagePreviewRequest,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    pkg: PackageService = Depends(get_package_service),
    entitlements: EntitlementService = Depends(get_entitlement_service),
):
    """Sufficiency preview: which sections/segments WOULD render for the period,
    before anything is generated or delivered.

    Sections locked to another client by exclusivity are surfaced here too, so an
    admin finds out at preview time rather than being refused at generate time.
    """
    blocked = {
        conflict["section_key"]: conflict
        for conflict in entitlements.conflicts_for(
            subscription_id=body.subscription_id, section_keys=body.section_keys
        )
    }
    return pkg.preview(
        section_keys=body.section_keys,
        period_start=body.period_start,
        period_end=body.period_end,
        blocked_keys=blocked,
    )


class AggregateBackfillRequest(BaseModel):
    product_type: str
    period_start: _date
    period_end: _date


class EntitlementGrantRequest(BaseModel):
    b2b_subscription_id: str
    module_key: str
    module_label: str | None = None
    section_keys: list[str] = []
    is_exclusive: bool = False
    reverts_at: _date | None = None
    notes: str | None = None


@router.get("/entitlements")
async def list_entitlements(
    subscription_id: str | None = Query(default=None),
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    entitlements: EntitlementService = Depends(get_entitlement_service),
):
    """The entitlement registry: what each client is owed and holds exclusively."""
    rows = (
        entitlements.list_for_subscription(subscription_id)
        if subscription_id
        else entitlements.list_all()
    )
    today = _date.today()
    return {
        "entitlements": [
            {**row, "exclusivity_in_force": entitlements.is_in_force(row, today)} for row in rows
        ]
    }


@router.post("/entitlements")
async def grant_entitlement(
    body: EntitlementGrantRequest,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    entitlements: EntitlementService = Depends(get_entitlement_service),
):
    """Grant or update an entitlement. Idempotent per (subscription, module)."""
    unknown = [key for key in body.section_keys if key not in SECTION_BY_KEY]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown section keys: {', '.join(unknown)}")
    if body.is_exclusive:
        # Two clients holding the same section exclusively is unsatisfiable, and
        # silently accepting it would mean both contracts look honoured in the
        # registry while composition blocks both.
        clash = entitlements.conflicts_for(
            subscription_id=body.b2b_subscription_id, section_keys=body.section_keys
        )
        if clash:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Those sections are already exclusive to another client",
                    "conflicts": clash,
                },
            )
    return entitlements.grant(
        subscription_id=body.b2b_subscription_id,
        module_key=body.module_key,
        module_label=body.module_label,
        section_keys=body.section_keys,
        is_exclusive=body.is_exclusive,
        reverts_at=body.reverts_at,
        notes=body.notes,
    )


@router.delete("/entitlements/{entitlement_id}")
async def revoke_entitlement(
    entitlement_id: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    entitlements: EntitlementService = Depends(get_entitlement_service),
):
    if not entitlements.revoke(entitlement_id):
        raise HTTPException(status_code=404, detail="Entitlement not found")
    return {"revoked": True, "id": entitlement_id}


@router.post("/package/generate")
async def package_generate(
    body: BespokeGenerateRequest,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
    pkg: PackageService = Depends(get_package_service),
    entitlements: EntitlementService = Depends(get_entitlement_service),
):
    """Generate a bespoke admin-composed package as a PDF.

    Runs the composed sections through the same privacy floors as a standard
    product, and refuses outright if any section is exclusively licensed to a
    different client.
    """
    if not body.section_keys:
        raise HTTPException(status_code=400, detail="At least one section is required")
    if body.period_start > body.period_end:
        raise HTTPException(status_code=400, detail="period_start must not be after period_end")

    try:
        entitlements.assert_available(
            subscription_id=body.subscription_id, section_keys=body.section_keys
        )
    except EntitlementConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "conflicts": exc.conflicts},
        ) from exc

    subscription = (
        service.get_subscription(body.subscription_id) if body.subscription_id else None
    )
    if body.subscription_id and not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    metrics = pkg.compose(
        section_keys=body.section_keys,
        period_start=body.period_start,
        period_end=body.period_end,
        title=body.title,
        client_name=body.client_name or (subscription or {}).get("company_name"),
    )
    if metrics.get("insufficient_data"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "The period does not contain enough consented signals to clear "
                    "the privacy floor. Widen the period or drop the signal sections."
                ),
                "signal_count": metrics.get("source_signal_count"),
                "thresholds": metrics.get("thresholds"),
            },
        )

    admin_email = getattr(_admin, "email", None) or "admin@prodculator.com"
    user_id = (subscription or {}).get("user_id") or getattr(_admin, "id", None) or "admin"
    try:
        request_row = service.generate_bespoke_report(
            metrics=metrics,
            user_id=user_id,
            recipient_email=admin_email,
            period_start=body.period_start,
            period_end=body.period_end,
            subscription_id=body.subscription_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "request_id": request_row["id"],
        "status": request_row.get("status"),
        "title": body.title,
        "section_count": len(metrics.get("sections") or []),
        "signal_count": metrics.get("source_signal_count"),
        "suppressed_segments": len(metrics.get("suppressed_segments") or []),
    }


@router.get("/aggregates/{product_type}")
async def list_monthly_aggregates(
    product_type: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    """Which months are stored for a product, and how many signals each holds.

    Quarterly composes from three stored months and yearly from twelve, so this
    is how an admin sees whether a period can be composed yet.
    """
    stored = service.get_monthly_aggregates(product_type)
    months = [
        {"period_month": key, "signal_count": row.get("signal_count") or 0}
        for key, row in sorted(stored.items())
    ]
    return {
        "product_type": product_type,
        "months": months,
        "stored_month_count": len(months),
        "yearly_available": len(months) >= 12,
    }


@router.post("/aggregates/backfill")
async def backfill_monthly_aggregates(
    body: AggregateBackfillRequest,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    """Recompute and store every month in the range. Idempotent."""
    if body.product_type not in B2B_PRODUCTS:
        raise HTTPException(status_code=404, detail="Unknown product type")
    if body.period_start > body.period_end:
        raise HTTPException(status_code=400, detail="period_start must not be after period_end")
    stored = service.backfill_monthly_aggregates(
        body.product_type, body.period_start, body.period_end
    )
    return {"product_type": body.product_type, "months_stored": stored}


# ── Saved package templates (SOW 4.4: "save as template") ────────────────────


class PackageTemplateSaveRequest(BaseModel):
    name: str
    section_keys: list[str]
    description: str | None = None
    product_type: str | None = None
    # Present => update that template; absent => create a new one.
    template_id: str | None = None


@router.get("/package/templates")
async def list_package_templates(
    product_type: str | None = Query(default=None),
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    templates: PackageTemplateService = Depends(get_template_service),
):
    """Saved bespoke compositions.

    Each template's keys are resolved against the live SECTION_LIBRARY so the
    composer can show titles, and can warn about keys a later deploy removed
    rather than silently dropping them.
    """
    rows = templates.list_all(product_type)
    out = []
    for row in rows:
        keys = row.get("section_keys") or []
        unknown = [k for k in keys if k not in SECTION_BY_KEY]
        out.append(
            {
                **row,
                "section_titles": [
                    SECTION_BY_KEY[k].title for k in keys if k in SECTION_BY_KEY
                ],
                "unknown_section_keys": unknown,
            }
        )
    return {"templates": out}


@router.post("/package/templates")
async def save_package_template(
    body: PackageTemplateSaveRequest,
    admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    templates: PackageTemplateService = Depends(get_template_service),
):
    """Create or update a saved composition."""
    unknown = [k for k in body.section_keys if k not in SECTION_BY_KEY]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown section key(s): {', '.join(unknown)}",
        )
    try:
        return templates.save(
            name=body.name,
            section_keys=body.section_keys,
            description=body.description,
            product_type=body.product_type,
            created_by=getattr(admin, "email", None) or getattr(admin, "id", None),
            template_id=body.template_id,
        )
    except TemplateNameConflict as exc:
        # 409, not 400: the request is well formed, it collides with existing state.
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/package/templates/{template_id}")
async def delete_package_template(
    template_id: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    templates: PackageTemplateService = Depends(get_template_service),
):
    if not templates.delete(template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"deleted": True, "id": template_id}


# ── Signal pool visibility and controls (SOW 4.5) ─────────────────────────────


class SignalFlagUpdate(BaseModel):
    # Both optional: a request may set either flag, or both.
    is_internal: bool | None = None
    b2b_consent: bool | None = None


@router.get("/signal-pool/summary")
async def signal_pool_summary(
    period_start: _date | None = Query(default=None),
    period_end: _date | None = Query(default=None),
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    pool: SignalPoolService = Depends(get_signal_pool_service),
):
    """How much of the pool is actually usable, and why the rest is not."""
    return pool.summary(period_start, period_end)


@router.get("/signal-pool")
async def list_signal_pool(
    period_start: _date | None = Query(default=None),
    period_end: _date | None = Query(default=None),
    consent: bool | None = Query(default=None),
    internal: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    pool: SignalPoolService = Depends(get_signal_pool_service),
):
    """Signal-level view of consent and internal flags."""
    return pool.list_signals(
        period_start=period_start,
        period_end=period_end,
        consent=consent,
        internal=internal,
        limit=limit,
        offset=offset,
    )


@router.patch("/signal-pool/{signal_id}")
async def update_signal_flags(
    signal_id: str,
    body: SignalFlagUpdate,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    pool: SignalPoolService = Depends(get_signal_pool_service),
):
    """Update governance flags on one signal.

    `is_internal` moves freely. `b2b_consent` may only be set to false: consent
    is the producer's to give, so an admin can honour a revocation but can never
    manufacture consent on someone's behalf. See composer_service docstring.
    """
    if body.is_internal is None and body.b2b_consent is None:
        raise HTTPException(status_code=400, detail="Nothing to update")

    updated: dict | None = None
    if body.is_internal is not None:
        updated = pool.set_internal(signal_id, body.is_internal)
        if updated is None:
            raise HTTPException(status_code=404, detail="Signal not found")
    if body.b2b_consent is not None:
        try:
            updated = pool.set_consent(signal_id, body.b2b_consent)
        except ConsentGrantRefused as exc:
            # 422: semantically invalid, and deliberately not something a retry fixes.
            raise HTTPException(status_code=422, detail=str(exc))
        if updated is None:
            raise HTTPException(status_code=404, detail="Signal not found")

    return {
        "id": signal_id,
        "b2b_consent": bool((updated or {}).get("b2b_consent")),
        "is_internal": bool((updated or {}).get("is_internal")),
    }
