from typing import Any

from pydantic import BaseModel, EmailStr


class EmailPreviewRequest(BaseModel):
    template_name: str
    context: dict[str, Any] = {}


class EmailPreviewResponse(BaseModel):
    subject: str
    html: str


class SendTestEmailRequest(BaseModel):
    to_email: EmailStr
    template_name: str
    context: dict[str, Any] = {}

