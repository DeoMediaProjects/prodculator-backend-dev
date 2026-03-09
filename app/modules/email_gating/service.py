from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient

TABLE = "email_gating_records"


class EmailGatingService:
    def __init__(self, db: DatabaseClient):
        self.db = db

    def create_record(self, email: str, report_generated: bool = False) -> dict[str, Any]:
        payload = {
            "id": str(uuid4()),
            "email": email,
            "report_generated": report_generated,
            "blocked": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = self.db.table(TABLE).insert(payload).select("*").single().execute()
        return result.data

    def is_blocked(self, email: str) -> bool:
        result = (
            self.db.table(TABLE)
            .select("blocked")
            .eq("email", email)
            .eq("blocked", True)
            .limit(1)
            .execute()
        )
        return bool(result.data)

    def list_records(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        search: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        count_query = self.db.table(TABLE).select("*", count="exact", head=True)
        if search:
            count_query = count_query.ilike("email", f"%{search}%")
        total = count_query.execute().count or 0

        rows_query = self.db.table(TABLE).select("*")
        if search:
            rows_query = rows_query.ilike("email", f"%{search}%")
        rows = (
            rows_query.order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return rows.data or [], total

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        result = (
            self.db.table(TABLE)
            .select("*")
            .eq("id", record_id)
            .single()
            .execute()
        )
        return result.data

    def block_record(self, record_id: str) -> dict[str, Any] | None:
        result = (
            self.db.table(TABLE)
            .update({"blocked": True})
            .eq("id", record_id)
            .select("*")
            .single()
            .execute()
        )
        return result.data

    def unblock_record(self, record_id: str) -> dict[str, Any] | None:
        result = (
            self.db.table(TABLE)
            .update({"blocked": False})
            .eq("id", record_id)
            .select("*")
            .single()
            .execute()
        )
        return result.data
