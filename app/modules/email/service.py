import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.core.config import Settings

logger = logging.getLogger(__name__)

EMAIL_SUBJECTS: dict[str, str] = {
    "welcome": "Welcome to Prodculator",
    "report_ready": "Your Prodculator report is ready",
    "payment_confirmation": "Payment confirmation",
    "processing_started": "We started processing your report",
    "grant_alert": "New grant opportunity for your watchlist",
    "festival_deadline": "Festival deadline reminder",
    "admin_invite": "You've been invited to Prodculator Admin",
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
        context = context or {}
        template_key = template_name.replace(".html", "")
        filename = f"{template_key}.html"
        subject = EMAIL_SUBJECTS.get(template_key, "Prodculator Notification")
        try:
            template = self.env.get_template(filename)
        except TemplateNotFound as exc:
            raise ValueError(f"Unknown email template: {template_name}") from exc
        return subject, template.render(**context)

    def send(self, to_email: str, template_name: str, context: dict[str, Any] | None = None) -> None:
        subject, html = self.render(template_name, context)
        if not self.settings.SENDGRID_API_KEY:
            logger.warning("SENDGRID_API_KEY not configured; email send skipped (to=%s)", to_email)
            return

        message = Mail(
            from_email=self.settings.SENDGRID_FROM_EMAIL,
            to_emails=to_email,
            subject=subject,
            html_content=html,
        )
        client = SendGridAPIClient(self.settings.SENDGRID_API_KEY)
        client.send(message)

