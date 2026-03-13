import json
import logging
from pathlib import Path
from time import perf_counter
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

    def render_report_html(
        self,
        report_data: dict[str, Any],
        *,
        script_title: str = "Untitled",
        report_type: str = "paid",
        created_at: str = "",
    ) -> str:
        started = perf_counter()
        template = self.env.get_template("report_base.html")
        html = template.render(
            report=report_data,
            script_title=script_title,
            report_type=report_type,
            created_at=created_at,
        )
        logger.debug(
            "Rendered report HTML: keys=%s html_chars=%s elapsed_ms=%s",
            sorted(report_data.keys()),
            len(html),
            int((perf_counter() - started) * 1000),
        )
        return html

    def generate_pdf_bytes(self, html: str) -> bytes | None:
        """
        Generate PDF bytes from HTML.
        Uses WeasyPrint when available. If unavailable, returns None.
        """
        started = perf_counter()
        logger.debug("Starting PDF generation: html_chars=%s", len(html))
        try:
            from weasyprint import HTML  # type: ignore

            pdf = HTML(string=html).write_pdf()
            if pdf is None:
                raise RuntimeError("WeasyPrint returned None")
            logger.info(
                "PDF generation successful: bytes=%s elapsed_ms=%s",
                len(pdf),
                int((perf_counter() - started) * 1000),
            )
            return pdf
        except Exception as exc:
            logger.warning(
                "PDF generation skipped (weasyprint unavailable or failed): %s elapsed_ms=%s",
                exc,
                int((perf_counter() - started) * 1000),
            )
            return None

    def upload_pdf(
        self,
        supabase: DatabaseClient,
        *,
        user_id: str,
        report_id: str,
        pdf_bytes: bytes,
    ) -> str | None:
        """Upload the PDF to storage and return the S3 object key (or local path).

        The key is stored in the ``pdf_url`` DB column.
        A presigned URL is generated fresh at serve time by ``_resolve_pdf_url``
        in the reports router — so the DB value never goes stale.
        """
        started = perf_counter()
        storage_path = f"{user_id}/{report_id}.pdf"
        logger.info("Uploading report PDF: report_id=%s storage_path=%s", report_id, storage_path)
        bucket = supabase.storage.from_("reports")
        bucket.upload(
            storage_path,
            pdf_bytes,
            {
                "content-type": "application/pdf",
                "x-upsert": "true",
            },
        )

        # Return the canonical key/path — not a presigned URL
        s3_key = bucket.get_s3_key(storage_path)
        logger.info(
            "Uploaded report PDF: report_id=%s s3_key=%s elapsed_ms=%s",
            report_id,
            s3_key,
            int((perf_counter() - started) * 1000),
        )
        return s3_key

    def fallback_report_text(self, report_data: dict[str, Any]) -> str:
        return json.dumps(report_data, indent=2)
