from pydantic import BaseModel, ConfigDict


class SubscriptionRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    user_id: str | None = None
    status: str | None = None


class ActiveSubscriptionResponse(BaseModel):
    subscription: SubscriptionRecord | None = None


class CanGenerateResponse(BaseModel):
    can_generate: bool
    reason: str

