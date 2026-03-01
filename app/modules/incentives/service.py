from typing import Any

from app.core.database_client import DatabaseClient

_TABLE = "incentive_programs"


class IncentivesService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def list_for_admin(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        count = self.supabase.table(_TABLE).select("*", count="exact", head=True).execute().count or 0
        rows = (
            self.supabase.table(_TABLE)
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
            .data
            or []
        )
        return rows, count

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.supabase.table(_TABLE).insert(payload).select("*").single().execute().data

    def update(self, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return (
            self.supabase.table(_TABLE).update(payload).eq("id", row_id).select("*").single().execute().data
        )

    def delete(self, row_id: str) -> None:
        self.supabase.table(_TABLE).delete().eq("id", row_id).execute()
