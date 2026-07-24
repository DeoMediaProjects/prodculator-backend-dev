from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
import stripe as stripe_lib

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_user, get_supabase
from app.modules.auth.schemas import AuthUser
from app.modules.b2b.schemas import (
    B2BCheckoutRequest,
    B2BCheckoutResponse,
    B2BIntelligenceRequestCreate,
    B2BIntelligenceRequestListResponse,
    B2BIntelligenceRequestResponse,
    B2BProductResponse,
    B2BSubscriptionListResponse,
)
from app.modules.b2b.service import B2B_PRODUCTS, B2BService, process_request_task
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


@router.get("/subscriptions", response_model=B2BSubscriptionListResponse)
async def list_subscriptions(
    user: AuthUser = Depends(get_current_user),
    service: B2BService = Depends(get_b2b_service),
):
    return {"items": service.list_user_subscriptions(user.id)}


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
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    background_tasks.add_task(process_request_task, request["id"])
    return service.add_download_url(request)


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

