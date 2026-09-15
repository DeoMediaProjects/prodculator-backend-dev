from typing import Literal

from pydantic import BaseModel, Field


class CheckoutRequest(BaseModel):
    # Compatibility assertion only. The server resolves the actual price from
    # its catalog and rejects a mismatch.
    price_id: str = Field(default="", max_length=255)
    currency: Literal["usd", "gbp"] = "usd"


class SubscriptionCheckoutRequest(BaseModel):
    # price_id is optional: when blank (e.g. a frontend build without
    # VITE_STRIPE_PRICE_* baked in), the backend resolves the Stripe price from
    # plan_type + currency + billing_cycle out of its own STRIPE_PRICE_* config.
    price_id: str = Field(default="", max_length=255)
    currency: Literal["usd", "gbp"] = "usd"
    plan_type: Literal["professional", "producer", "studio"] = "professional"
    billing_cycle: Literal["monthly", "annual"] = "monthly"


class CancelSubscriptionRequest(BaseModel):
    subscription_id: str


class UpdatePaymentMethodRequest(BaseModel):
    customer_id: str
    payment_method_id: str


class CustomerPortalRequest(BaseModel):
    customer_id: str


class CheckoutResponse(BaseModel):
    session_id: str
    url: str


class CustomerPortalResponse(BaseModel):
    url: str
