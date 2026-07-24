from pydantic import BaseModel


class CheckoutRequest(BaseModel):
    price_id: str
    currency: str = "usd"


class SubscriptionCheckoutRequest(BaseModel):
    # price_id is optional: when blank (e.g. a frontend build without
    # VITE_STRIPE_PRICE_* baked in), the backend resolves the Stripe price from
    # plan_type + currency + billing_cycle out of its own STRIPE_PRICE_* config.
    price_id: str = ""
    currency: str = "usd"
    plan_type: str = "professional"
    billing_cycle: str = "monthly"  # monthly | annual


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
