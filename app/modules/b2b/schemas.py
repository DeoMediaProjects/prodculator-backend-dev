from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator


B2BProductType = Literal[
    "camera_equipment",
    "production_services",
    "crew_casting",
    "production_trend",
    "enterprise",
]
B2BCurrency = Literal["gbp", "usd"]
B2BDeliveryFrequency = Literal["monthly", "quarterly"]


class B2BProductResponse(BaseModel):
    product_type: B2BProductType
    title: str
    audience: str
    description: str
    features: list[str]
    price_gbp_cents: int | None = None
    price_usd_cents: int | None = None
    self_service: bool
    # "coming_soon" while pricing is being finalised, "custom_contract" for
    # bespoke agreements, "listed" once a real price is published.
    pricing_status: Literal["coming_soon", "custom_contract", "listed"] = "listed"
    stripe_price_configured: dict[str, bool]


class B2BWithheldSection(BaseModel):
    """A section this client cannot receive because another holds it exclusively."""

    section_key: str
    section_title: str
    module_label: str | None = None
    # None means the exclusivity is perpetual, not that it is unknown.
    available_from: str | None = None


class B2BRequestEntitlementResponse(BaseModel):
    """What a client may request for a product (SOW 4.4).

    The dashboard uses this to disable withheld sections before the client asks,
    so exclusivity is a stated constraint rather than a refusal after the fact.
    """

    product_type: str
    # Everything the product's template covers.
    section_keys: list[str]
    # The subset this client may actually receive.
    allowed_section_keys: list[str]
    withheld_sections: list[B2BWithheldSection] = []
    # False only when exclusivity leaves nothing renderable at all.
    can_request: bool


class B2BCheckoutRequest(BaseModel):
    product_type: B2BProductType
    currency: B2BCurrency = "gbp"
    delivery_frequency: B2BDeliveryFrequency = "monthly"
    extra_recipient_email: EmailStr | None = None


class B2BCheckoutResponse(BaseModel):
    session_id: str
    url: str


class B2BIntelligenceRequestCreate(BaseModel):
    product_type: B2BProductType
    period_start: date
    period_end: date
    extra_recipient_email: EmailStr | None = None

    @model_validator(mode="after")
    def validate_period(self) -> "B2BIntelligenceRequestCreate":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class B2BSubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    product_type: B2BProductType
    status: str
    source: str
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    price_id: str | None = None
    amount_cents: int | None = None
    currency: str | None = None
    delivery_frequency: str
    extra_recipient_email: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    next_delivery_at: datetime | None = None
    cancel_at_period_end: bool = False
    company_name: str | None = None
    admin_notes: str | None = None
    #: Resolved from the users table on the admin listing so the console can name
    #: the account instead of printing a bare UUID. Absent on single-row
    #: responses, where the caller already knows who it asked about.
    user_email: str | None = None
    created_at: datetime
    updated_at: datetime


class B2BIntelligenceRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    b2b_subscription_id: str | None = None
    product_type: B2BProductType
    status: str
    request_type: str
    period_start: date
    period_end: date
    recipient_email: str
    extra_recipient_email: str | None = None
    pdf_url: str | None = None
    download_url: str | None = None
    metrics: dict[str, Any] | None = None
    error_message: str | None = None
    delivered_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class B2BSubscriptionListResponse(BaseModel):
    items: list[B2BSubscriptionResponse]


class B2BIntelligenceRequestListResponse(BaseModel):
    items: list[B2BIntelligenceRequestResponse]
    total: int


class AdminB2BManualSubscriptionCreate(BaseModel):
    user_email: EmailStr
    product_type: B2BProductType
    delivery_frequency: B2BDeliveryFrequency = "monthly"
    extra_recipient_email: EmailStr | None = None
    status: str = "active"
    company_name: str | None = None
    admin_notes: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed = {"active", "trialing", "past_due", "cancelled", "inactive"}
        if value not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return value


class AdminB2BSubscriptionUpdate(BaseModel):
    status: str | None = None
    delivery_frequency: B2BDeliveryFrequency | None = None
    extra_recipient_email: EmailStr | None = None
    next_delivery_at: datetime | None = None
    company_name: str | None = None
    admin_notes: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = {"active", "trialing", "past_due", "cancelled", "inactive"}
        if value not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return value


# ── Manual-contract invites (handoff §4.3/§4.4) ───────────────────────────────

B2BInviteStatus = Literal["pending", "accepted", "revoked", "expired"]


class AdminB2BInviteCreate(BaseModel):
    """Issue an invite to claim a manually-contracted subscription.

    The invited party does not need an account yet — that is the point of the
    flow. Terms recorded here are what the subscription is created with on claim.
    """

    email: EmailStr
    product_type: B2BProductType
    delivery_frequency: B2BDeliveryFrequency = "monthly"
    extra_recipient_email: EmailStr | None = None
    company_name: str | None = None
    admin_notes: str | None = None
    expires_in_days: int = 30
    # Off only when an admin intends to pass the link on by another channel;
    # the accept URL is returned either way.
    send_email: bool = True

    @field_validator("expires_in_days")
    @classmethod
    def validate_expiry(cls, value: int) -> int:
        if not 1 <= value <= 365:
            raise ValueError("expires_in_days must be between 1 and 365")
        return value


class AdminB2BInviteResponse(BaseModel):
    """Admin view of an invite. Never carries the token or its hash."""

    id: str
    email: str
    product_type: str
    status: B2BInviteStatus
    # First few characters of the token, so two outstanding invites are
    # distinguishable in the UI. Not enough to reconstruct a link.
    token_prefix: str | None = None
    expires_at: datetime | None = None
    delivery_frequency: str | None = None
    extra_recipient_email: str | None = None
    company_name: str | None = None
    admin_notes: str | None = None
    created_by: str | None = None
    sent_count: int = 0
    last_sent_at: datetime | None = None
    accepted_at: datetime | None = None
    accepted_by_user_id: str | None = None
    b2b_subscription_id: str | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AdminB2BInviteIssuedResponse(BaseModel):
    """The issue/resend response — the only place the accept URL is ever returned.

    The raw token is not stored, so this URL cannot be recovered afterwards.
    Resending mints a new one and invalidates this.
    """

    invite: AdminB2BInviteResponse
    accept_url: str


class AdminB2BInviteListResponse(BaseModel):
    items: list[AdminB2BInviteResponse]
    total: int


class B2BInvitePreviewResponse(BaseModel):
    """Unauthenticated view of an invite, for the accept page.

    Deliberately thin: it is reachable by anyone holding the link, so it carries
    nothing beyond what the email that delivered it already contained.
    """

    email: str
    product_type: str
    company_name: str | None = None
    delivery_frequency: str | None = None
    expires_at: datetime | None = None
    status: B2BInviteStatus
    claimable: bool


class AdminB2BRequestListResponse(BaseModel):
    items: list[B2BIntelligenceRequestResponse]
    total: int


class AdminB2BResendResponse(BaseModel):
    sent: bool
    recipients: list[str]

