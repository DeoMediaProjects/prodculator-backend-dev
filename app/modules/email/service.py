import logging
import re
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from app.core.config import Settings

logger = logging.getLogger(__name__)
_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")

# Brevo transactional email API — https://developers.brevo.com/reference/sendtransacemail
BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_SEND_TIMEOUT = 10

EMAIL_SUBJECTS: dict[str, str] = {
    "verify_email": "Verify your Prodculator account",
    "reset_password": "Reset your Prodculator password",
    "welcome": "Welcome to Prodculator",
    "report_ready": "Your Prodculator report is ready",
    "payment_confirmation": "Payment confirmation",
    "payment_failed": "Action required: your payment failed",
    "subscription_recovered": "Your subscription is back to active",
    "subscription_downgraded": "Your subscription has been cancelled",
    "plan_upgraded": "Your Prodculator plan has been upgraded",
    "downgrade_scheduled": "Your plan change is scheduled",
    "downgrade_applied": "Your Prodculator plan has changed",
    "processing_started": "We started processing your report",
    "grant_alert": "New grant opportunity for your watchlist",
    "festival_deadline": "Festival deadline reminder",
    "admin_invite": "You've been invited to Prodculator Admin",
    "support_inquiry": "New Prodculator support inquiry",
    "support_inquiry_confirmation": "We received your Prodculator inquiry",
    "b2b_subscription_active": "Your B2B intelligence subscription is active",
    "b2b_subscription_updated": "Your B2B intelligence subscription was updated",
    "b2b_intelligence_ready": "Your B2B intelligence PDF is ready",
}


class EmailService:
    def __init__(self, settings: Settings):
        self.settings = settings
        templates_dir = Path(__file__).resolve().parents[2] / "templates" / "emails"
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render(self, template_name: str, context: dict[str, Any] | None = None) -> tuple[str, str]:
        context = self._normalise_context(context or {})
        template_key = template_name.replace(".html", "")
        filename = f"{template_key}.html"
        subject = EMAIL_SUBJECTS.get(template_key, "Prodculator Notification")
        try:
            template = self.env.get_template(filename)
        except TemplateNotFound as exc:
            raise ValueError(f"Unknown email template: {template_name}") from exc
        return subject, template.render(**context)

    def send(
        self,
        to_email: str,
        template_name: str,
        context: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        subject, html = self.render(template_name, context)
        if not self.settings.BREVO_API_KEY:
            logger.warning("BREVO_API_KEY not configured; email send skipped (to=%s)", to_email)
            return

        payload: dict[str, Any] = {
            "sender": {
                "email": self.settings.BREVO_FROM_EMAIL,
                "name": self.settings.BREVO_FROM_NAME,
            },
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html,
        }
        # Brevo infers the content type from the filename, so only content + name
        # are needed. Attachment content is already base64-encoded by callers.
        if attachments:
            payload["attachment"] = [
                {"content": attachment["content"], "name": attachment["filename"]}
                for attachment in attachments
            ]

        response = httpx.post(
            BREVO_SEND_URL,
            headers={
                "api-key": self.settings.BREVO_API_KEY,
                "accept": "application/json",
                "content-type": "application/json",
            },
            json=payload,
            timeout=BREVO_SEND_TIMEOUT,
        )
        response.raise_for_status()

    @staticmethod
    def _camel_to_snake(key: str) -> str:
        return _CAMEL_CASE_BOUNDARY_RE.sub("_", key).lower()

    def _normalise_context(self, context: dict[str, Any]) -> dict[str, Any]:
        normalised = dict(context)
        for key, value in context.items():
            snake_key = self._camel_to_snake(key)
            if snake_key != key and snake_key not in normalised:
                normalised[snake_key] = value

        if "userName" in context and "name" not in normalised:
            normalised["name"] = context["userName"]

        app_url = self.settings.FRONTEND_URL.rstrip("/")
        normalised.setdefault("app_url", app_url)
        normalised.setdefault("dashboard_url", f"{app_url}/dashboard")
        normalised.setdefault("login_url", f"{app_url}/login")
        normalised.setdefault("support_email", self.settings.CONTACT_EMAIL or "support@prodculator.com")
        normalised.setdefault("billing_email", "billing@prodculator.com")
        normalised.setdefault("currency", "USD")

        report_id = normalised.get("report_id")
        if report_id and "report_url" not in normalised:
            normalised["report_url"] = f"{app_url}/reports/{report_id}"

        if report_id and "pdf_url" not in normalised:
            normalised["pdf_url"] = f"{app_url}/reports/{report_id}"

        plan_type = normalised.get("plan_type")
        if plan_type and "plan_name" not in normalised:
            normalised["plan_name"] = f"{str(plan_type).replace('_', ' ').title()} Plan"

        amount_paid = normalised.get("amount_paid")
        if amount_paid is not None and "amount" not in normalised:
            try:
                value = float(amount_paid)
                normalised["amount"] = f"{(value / 100):.2f}" if value > 200 else f"{value:.2f}"
            except (TypeError, ValueError):
                pass

        return normalised
