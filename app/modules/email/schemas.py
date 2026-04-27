import base64
import binascii
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class EmailPreviewRequest(BaseModel):
    template_name: str
    context: dict[str, Any] = Field(default_factory=dict)


class EmailPreviewResponse(BaseModel):
    subject: str
    html: str


class SendTestEmailRequest(BaseModel):
    to_email: EmailStr
    template_name: str
    context: dict[str, Any] = Field(default_factory=dict)


class EmailAttachment(BaseModel):
    filename: str = Field(min_length=1)
    content: str = Field(min_length=1)
    type: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def validate_base64_content(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Attachment content must be a valid base64 string") from exc
        return value


class TransactionalEmailRequest(BaseModel):
    template: str = Field(min_length=1)
    to: EmailStr
    data: dict[str, Any]
    attachments: list[EmailAttachment] = Field(default_factory=list)


class TransactionalEmailPreviewRequest(BaseModel):
    template: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)


class TransactionalEmailPreviewResponse(BaseModel):
    subject: str
    html: str


class TransactionalEmailSuccessResponse(BaseModel):
    success: Literal[True] = True


class TransactionalEmailErrorResponse(BaseModel):
    success: Literal[False] = False
    error: str
