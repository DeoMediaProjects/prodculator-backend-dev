from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.modules.contact.schemas import ContactMessageCreate, ContactMessageSubmitResponse
from app.modules.email.service import EmailService

logger = logging.getLogger(__name__)


def _format_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


class ContactService:
    def __init__(
        self,
        supabase: DatabaseClient,
        settings: Settings,
        email_service: EmailService | None = None,
    ) -> None:
        self.supabase = supabase
        self.settings = settings
        self.email_service = email_service or EmailService(settings)

    @property
    def contact_email(self) -> str:
        return self.settings.CONTACT_EMAIL or "support@prodculator.com"

    def submit_message(
        self,
        payload: ContactMessageCreate,
    ) -> ContactMessageSubmitResponse:
        message_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        row = {
            "id": message_id,
            "name": payload.name,
            "email": payload.email,
            "company": payload.company,
            "category": payload.category,
            "subject": payload.subject,
            "message": payload.message,
            "page_url": payload.page_url,
            "internal_email_sent": False,
            "auto_reply_sent": False,
            "email_error": None,
            "status": "open",
            "created_at": created_at,
        }

        self.supabase.table("contact_messages").insert(row).execute()

        context = self._build_email_context(row)
        internal_email_sent, internal_error = self._send_internal_email(context)
        auto_reply_sent, auto_reply_error = self._send_auto_reply(context)
        email_error = "; ".join(
            error for error in [internal_error, auto_reply_error] if error
        ) or None

        self.supabase.table("contact_messages").update(
            {
                "internal_email_sent": internal_email_sent,
                "auto_reply_sent": auto_reply_sent,
                "email_error": email_error,
            }
        ).eq("id", message_id).execute()

        return ContactMessageSubmitResponse(message_id=message_id)

    def _build_email_context(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "message_id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "company": row.get("company"),
            "category": row["category"],
            "subject": row["subject"],
            "message": row["message"],
            "page_url": row.get("page_url"),
            "submitted_at": _format_timestamp(row["created_at"]),
            "support_email": self.contact_email,
        }

    def _send_internal_email(self, context: dict[str, Any]) -> tuple[bool, str | None]:
        try:
            self.email_service.send(
                self.contact_email,
                "contact_message",
                context,
            )
            return True, None
        except Exception as exc:
            logger.warning(
                "Failed to send contact message email: message_id=%s",
                context.get("message_id"),
                exc_info=True,
            )
            return False, f"internal email failed: {exc}"

    def _send_auto_reply(self, context: dict[str, Any]) -> tuple[bool, str | None]:
        try:
            self.email_service.send(
                context["email"],
                "contact_message_confirmation",
                context,
            )
            return True, None
        except Exception as exc:
            logger.warning(
                "Failed to send contact message confirmation: message_id=%s",
                context.get("message_id"),
                exc_info=True,
            )
            return False, f"auto reply failed: {exc}"
