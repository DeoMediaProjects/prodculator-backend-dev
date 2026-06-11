from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.modules.auth.schemas import AuthUser
from app.modules.email.service import EmailService
from app.modules.support.schemas import SupportInquiryCreate, SupportInquirySubmitResponse

logger = logging.getLogger(__name__)


class SupportService:
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

    def submit_inquiry(
        self,
        user: AuthUser,
        inquiry: SupportInquiryCreate,
    ) -> SupportInquirySubmitResponse:
        inquiry_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        row = {
            "id": inquiry_id,
            "user_id": user.id,
            "user_email": user.email,
            "user_name": user.name,
            "company": user.company,
            "role": user.role,
            "plan": user.plan,
            "category": inquiry.category,
            "message": inquiry.message,
            "selected_faq_question": inquiry.selected_faq_question,
            "selected_faq_answer": inquiry.selected_faq_answer,
            "page_url": inquiry.page_url,
            "internal_email_sent": False,
            "auto_reply_sent": False,
            "email_error": None,
            "status": "open",
            "created_at": created_at,
        }

        self.supabase.table("support_inquiries").insert(row).execute()

        context = self._build_email_context(row)
        internal_email_sent, internal_error = self._send_internal_email(context)
        auto_reply_sent, auto_reply_error = self._send_auto_reply(context)
        email_error = "; ".join(
            error for error in [internal_error, auto_reply_error] if error
        ) or None

        self.supabase.table("support_inquiries").update(
            {
                "internal_email_sent": internal_email_sent,
                "auto_reply_sent": auto_reply_sent,
                "email_error": email_error,
            }
        ).eq("id", inquiry_id).execute()

        return SupportInquirySubmitResponse(inquiry_id=inquiry_id)

    def _build_email_context(self, row: dict[str, Any]) -> dict[str, Any]:
        submitted_at = row["created_at"]
        return {
            "inquiry_id": row["id"],
            "user_id": row["user_id"],
            "user_email": row["user_email"],
            "user_name": row.get("user_name") or row["user_email"],
            "company": row.get("company"),
            "role": row.get("role"),
            "plan": row.get("plan"),
            "category": row["category"],
            "message": row["message"],
            "selected_faq_question": row.get("selected_faq_question"),
            "selected_faq_answer": row.get("selected_faq_answer"),
            "page_url": row.get("page_url"),
            "submitted_at": submitted_at,
            "support_email": self.contact_email,
        }

    def _send_internal_email(self, context: dict[str, Any]) -> tuple[bool, str | None]:
        try:
            self.email_service.send(
                self.contact_email,
                "support_inquiry",
                context,
            )
            return True, None
        except Exception as exc:
            logger.warning(
                "Failed to send support inquiry email: inquiry_id=%s",
                context.get("inquiry_id"),
                exc_info=True,
            )
            return False, f"internal email failed: {exc}"

    def _send_auto_reply(self, context: dict[str, Any]) -> tuple[bool, str | None]:
        try:
            self.email_service.send(
                context["user_email"],
                "support_inquiry_confirmation",
                context,
            )
            return True, None
        except Exception as exc:
            logger.warning(
                "Failed to send support inquiry confirmation: inquiry_id=%s",
                context.get("inquiry_id"),
                exc_info=True,
            )
            return False, f"auto reply failed: {exc}"
