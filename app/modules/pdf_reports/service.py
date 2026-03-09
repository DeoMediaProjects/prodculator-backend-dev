from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.core.storage import StorageClient

TABLE = "reports"


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


class PdfReportsService:
    def __init__(self, db: DatabaseClient):
        self.db = db

    def list_reports(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        reports_t = self.db._table(TABLE)
        users_t = self.db._table("users")

        base_filter = [
            reports_t.c.status == "completed",
            reports_t.c.pdf_url.isnot(None),
        ]

        count_stmt = (
            select(func.count())
            .select_from(reports_t)
            .where(*base_filter)
        )
        total = int(self.db.session.execute(count_stmt).scalar_one() or 0)

        stmt = (
            select(
                reports_t.c.id,
                reports_t.c.script_title,
                reports_t.c.created_at,
                reports_t.c.downloaded,
                reports_t.c.user_id,
                users_t.c.email,
            )
            .join(users_t, reports_t.c.user_id == users_t.c.id, isouter=True)
            .where(*base_filter)
            .order_by(reports_t.c.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = [dict(r._mapping) for r in self.db.session.execute(stmt).all()]
        return rows, total

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        result = (
            self.db.table(TABLE)
            .select("*")
            .eq("id", report_id)
            .single()
            .execute()
        )
        return result.data

    def get_user_email(self, user_id: str) -> str | None:
        result = (
            self.db.table("users")
            .select("email")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if result.data:
            return result.data.get("email")
        return None

    def mark_downloaded(self, report_id: str) -> None:
        self.db.table(TABLE).update({"downloaded": True}).eq("id", report_id).execute()

    def get_file_size(self, user_id: str, report_id: str, settings: Settings) -> str | None:
        path = Path(settings.STORAGE_ROOT) / "reports" / user_id / f"{report_id}.pdf"
        try:
            return _format_size(path.stat().st_size)
        except FileNotFoundError:
            return None

    def download_pdf(self, user_id: str, report_id: str, settings: Settings) -> bytes:
        storage = StorageClient(settings)
        return storage.from_("reports").download(f"{user_id}/{report_id}.pdf")
