from pydantic import BaseModel


class CheckoutRequest(BaseModel):
    price_id: str
    currency: str = "usd"


class SubscriptionCheckoutRequest(BaseModel):
    price_id: str
    currency: str = "usd"
    plan_type: str = "professional"


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
