import json
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.core.database_client import DatabaseClient

logger = logging.getLogger(__name__)


class PDFService:
    def __init__(self):
        templates_dir = Path(__file__).resolve().parents[2] / "templates" / "pdf"
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render_report_html(self, report_data: dict[str, Any]) -> str:
        template = self.env.get_template("report_base.html")
        return template.render(report=report_data)

    def generate_pdf_bytes(self, html: str) -> bytes | None:
        """
        Generate PDF bytes from HTML.
        Uses WeasyPrint when available. If unavailable, returns None.
        """
        try:
            from weasyprint import HTML  # type: ignore

            return HTML(string=html).write_pdf()
        except Exception as exc:
            logger.warning("PDF generation skipped (weasyprint unavailable or failed): %s", exc)
            return None

    def upload_pdf(
        self,
        supabase: DatabaseClient,
        *,
        user_id: str,
        report_id: str,
        pdf_bytes: bytes,
    ) -> str | None:
        storage_path = f"{user_id}/{report_id}.pdf"
        supabase.storage.from_("reports").upload(
            storage_path,
            pdf_bytes,
            {
                "content-type": "application/pdf",
                "x-upsert": "true",
            },
        )

        public_url = supabase.storage.from_("reports").get_public_url(storage_path)
        if isinstance(public_url, str):
            return public_url
        if isinstance(public_url, dict):
            return public_url.get("publicUrl") or public_url.get("public_url")
        return None

    def fallback_report_text(self, report_data: dict[str, Any]) -> str:
        return json.dumps(report_data, indent=2)

