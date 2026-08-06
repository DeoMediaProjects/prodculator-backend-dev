from __future__ import annotations

import logging

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response,
)
from pydantic import BaseModel, EmailStr
import stripe as stripe_lib

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_user, get_supabase
from app.core.limiter import limiter
from app.modules.auth.schemas import AuthUser
from app.modules.b2b.invite_service import (
    B2BInviteService,
    InviteEmailMismatch,
    InviteNotClaimable,
    InviteNotFound,
)
from app.modules.b2b.schemas import (
    B2BCheckoutRequest,
    B2BCheckoutResponse,
    B2BIntelligenceRequestCreate,
    B2BIntelligenceRequestListResponse,
    B2BIntelligenceRequestResponse,
    B2BInvitePreviewResponse,
    B2BProductResponse,
    B2BRequestEntitlementResponse,
    B2BSubscriptionListResponse,
    B2BSubscriptionResponse,
)
from app.modules.b2b.service import (
    B2B_PRODUCTS,
    B2BService,
    EntitlementScopeError,
    process_request_task,
)
from app.modules.payments.service import StripeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/b2b", tags=["B2B"])


def get_b2b_service(
    db: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> B2BService:
    return B2BService(db, settings)


def get_stripe_service(settings: Settings = Depends(get_settings)) -> StripeService:
    return StripeService(settings)


@router.get("/products", response_model=list[B2BProductResponse])
async def list_products(
    _user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
):
    return service.list_products()


# ── Manual-contract invite claim (handoff §4.3/§4.4) ──────────────────────────


def get_invite_service(
    db: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> B2BInviteService:
    return B2BInviteService(db, settings)


@router.get("/invites/{token}", response_model=B2BInvitePreviewResponse)
@limiter.limit("20/minute")
async def preview_invite(
    request: Request,
    token: str,
    invites: B2BInviteService = Depends(get_invite_service),
):
    """What this invitation offers, before signing in.

    Unauthenticated by necessity: the client may not have an account yet, and
    they need to see what they are being asked to claim before creating one. Rate
    limited because the token is the only credential, so this is the one place an
    attacker could test guesses. A 404 is returned for an unknown token, giving
    nothing back that distinguishes 'no such invite' from any other failure.
    """
    try:
        return invites.preview(token)
    except InviteNotFound:
        raise HTTPException(status_code=404, detail="This invitation link is not valid")


@router.post("/invites/{token}/accept", response_model=B2BSubscriptionResponse)
@limiter.limit("10/minute")
async def accept_invite(
    request: Request,
    token: str,
    user: AuthUser = Depends(get_current_user),
    invites: B2BInviteService = Depends(get_invite_service),
    service: B2BService = Depends(get_b2b_service),
):
    """Claim the invitation, creating the contracted subscription.

    Requires a signed-in user whose email matches the invited address: an invite
    is tied to a named counterparty, and a link any account could redeem would
    let a forwarded email transfer a paid entitlement.
    """
    try:
        return invites.accept(
            token=token,
            user_id=user.id,
            user_email=user.email,
            b2b_service=service,
        )
    except InviteNotFound:
        raise HTTPException(status_code=404, detail="This invitation link is not valid")
    except InviteEmailMismatch as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "message": str(exc),
                "reason": "email_mismatch",
                "invited_email": exc.invited_email,
            },
        )
    except InviteNotClaimable as exc:
        # 409: the link is real, its state has moved on. The status is returned
        # so the accept page can say expired vs. revoked vs. already used rather
        # than showing one generic error for three different situations.
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "reason": exc.status},
        )


@router.get("/subscriptions", response_model=B2BSubscriptionListResponse)
async def list_subscriptions(
    user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
):
    return {"items": service.list_user_subscriptions(user.id)}


class B2BRecipientUpdate(BaseModel):
    """Set (or clear, with null) the additional recipient on your subscription."""

    extra_recipient_email: EmailStr | None = None


@router.patch("/subscriptions/{subscription_id}/recipients", response_model=B2BSubscriptionResponse)
async def update_subscription_recipients(
    subscription_id: str,
    body: B2BRecipientUpdate,
    user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
):
    """Manage the distribution list for your own Business Intelligence subscription."""
    subscription = service.set_extra_recipient(
        subscription_id,
        user.id,
        str(body.extra_recipient_email) if body.extra_recipient_email else None,
    )
    if not subscription:
        # Same response for "does not exist" and "not yours" so the endpoint
        # cannot be used to probe for other companies' subscription ids.
        raise HTTPException(status_code=404, detail="Subscription not found")
    return subscription


@router.post("/checkout", response_model=B2BCheckoutResponse)
async def create_checkout(
    body: B2BCheckoutRequest,
    user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
    stripe_service: StripeService = Depends(get_stripe_service),
    settings: Settings = Depends(get_settings),
):
    product = B2B_PRODUCTS.get(body.product_type)
    if not product:
        raise HTTPException(status_code=404, detail="B2B product not found")
    if not product.get("self_service"):
        raise HTTPException(status_code=400, detail="This B2B product is admin/manual-contract only")
    if service.active_subscription(user.id, body.product_type):
        raise HTTPException(status_code=409, detail="You already have an active subscription for this B2B product")

    price_id = service.get_price_id(body.product_type, body.currency)
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=f"Stripe price is not configured for {body.product_type} in {body.currency.upper()}",
        )

    try:
        # See the B2C checkout: when compressed-cycle billing test mode is ON
        # (ops flag, OFF by default), route the public B2B checkout through the
        # short-cycle $1 auto-refund test price so a demo subscriber can watch a
        # renewal fire and be kept whole. OFF → real price, no refund.
        result = stripe_service.create_b2b_subscription_checkout(
            price_id=price_id,
            user_email=user.email,
            user_id=user.id,
            product_type=body.product_type,
            currency=body.currency,
            delivery_frequency=body.delivery_frequency,
            extra_recipient_email=str(body.extra_recipient_email) if body.extra_recipient_email else None,
            test_billing=settings.STRIPE_TEST_BILLING_ENABLED,
        )
        return B2BCheckoutResponse(**result)
    except stripe_lib.StripeError:
        logger.exception("Stripe error in B2B checkout for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Payment processing failed")
    except Exception:
        logger.exception("Unexpected error in B2B checkout for user=%s", user.id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/requests", response_model=B2BIntelligenceRequestResponse)
async def create_intelligence_request(
    body: B2BIntelligenceRequestCreate,
    background_tasks: BackgroundTasks,
    user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
):
    try:
        request = service.create_intelligence_request(
            user_id=user.id,
            user_email=user.email,
            product_type=body.product_type,
            period_start=body.period_start,
            period_end=body.period_end,
            extra_recipient_email=str(body.extra_recipient_email) if body.extra_recipient_email else None,
        )
    except EntitlementScopeError as exc:
        # 409, not 403: the request is well formed and the client is authorised,
        # it collides with another client's exclusivity. The withheld sections
        # and their reversion dates go in the response so the client is told what
        # is unavailable and when — not just that they cannot have it. Checked
        # before anything is persisted, so there is no half-made request to clean
        # up. The dashboard reads GET /requests/entitlement to disable these up
        # front, so reaching this at all should be rare.
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "reason": "entitlement_scope",
                "withheld_sections": exc.withheld,
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    background_tasks.add_task(process_request_task, request["id"])
    return service.add_download_url(request)


@router.get("/requests/entitlement", response_model=B2BRequestEntitlementResponse)
async def get_request_entitlement(
    product_type: str = Query(..., description="B2B product to check"),
    user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
):
    """Which sections of a product this client may receive, and which are held
    exclusively by someone else.

    The dashboard uses this to disable withheld sections before the client asks,
    so exclusivity reads as a stated constraint with a reversion date rather than
    a refusal after the fact. Declared before /requests/{request_id} so the
    literal path is not captured by it.
    """
    if product_type not in B2B_PRODUCTS:
        raise HTTPException(status_code=404, detail="Unknown B2B product")

    subscription = service.active_subscription(user.id, product_type)
    if not subscription:
        raise HTTPException(
            status_code=403,
            detail="An active B2B subscription is required for this intelligence product",
        )
    return service.request_entitlement(
        product_type=product_type, subscription_id=subscription["id"],
    )


@router.get("/requests", response_model=B2BIntelligenceRequestListResponse)
async def list_requests(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
):
    items, total = service.list_requests(user_id=user.id, limit=limit, offset=offset)
    return {"items": items, "total": total}


@router.get("/requests/{request_id}", response_model=B2BIntelligenceRequestResponse)
async def get_request(
    request_id: str,
    user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
):
    request = service.get_request(request_id, user_id=user.id, include_metrics=True)
    if not request:
        raise HTTPException(status_code=404, detail="B2B request not found")
    return request


@router.get("/requests/{request_id}/pdf")
async def download_pdf(
    request_id: str,
    user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
):
    request = service.get_request(request_id, user_id=user.id, include_metrics=True)
    if not request:
        raise HTTPException(status_code=404, detail="B2B request not found")
    if request.get("status") != "completed" or not request.get("pdf_url"):
        raise HTTPException(status_code=404, detail="PDF not available")

    try:
        pdf_bytes = service.download_request_pdf(request)
    except Exception:
        logger.warning("B2B PDF download failed: request_id=%s", request_id)
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

