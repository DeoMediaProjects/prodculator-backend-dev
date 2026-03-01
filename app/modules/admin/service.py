from datetime import datetime, timezone
from typing import Any

from app.core.database_client import DatabaseClient


class AdminService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def list_table(
        self,
        table_name: str,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: str = "created_at",
        ascending: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        count_result = self.supabase.table(table_name).select("*", count="exact", head=True).execute()
        total = count_result.count or 0

        rows_result = (
            self.supabase.table(table_name)
            .select("*")
            .order(order_by, desc=not ascending)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return rows_result.data or [], total

    def create_row(self, table_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.supabase.table(table_name).insert(payload).select("*").single().execute()
        return result.data

    def update_row(self, table_name: str, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = (
            self.supabase.table(table_name).update(payload).eq("id", row_id).select("*").single().execute()
        )
        return result.data

    def delete_row(self, table_name: str, row_id: str) -> None:
        self.supabase.table(table_name).delete().eq("id", row_id).execute()

    def get_business_metrics(self) -> dict[str, Any]:
        total_users = self.supabase.table("users").select("*", count="exact", head=True).execute().count or 0
        active_subs = (
            self.supabase.table("subscriptions")
            .select("*", count="exact", head=True)
            .eq("status", "active")
            .execute()
            .count
            or 0
        )
        total_reports = self.supabase.table("reports").select("*", count="exact", head=True).execute().count or 0

        start_of_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        reports_this_month = (
            self.supabase.table("reports")
            .select("*", count="exact", head=True)
            .gte("created_at", start_of_month.isoformat())
            .execute()
            .count
            or 0
        )

        subscriptions = (
            self.supabase.table("subscriptions")
            .select("amount_cents, currency")
            .eq("status", "active")
            .execute()
            .data
            or []
        )
        mrr_usd = 0.0
        for sub in subscriptions:
            amount_cents = sub.get("amount_cents") or 0
            currency = (sub.get("currency") or "usd").lower()
            amount_usd = (amount_cents * 1.27) if currency == "gbp" else amount_cents
            mrr_usd += amount_usd / 100

        conversion_rate = (active_subs / total_users * 100.0) if total_users else 0.0
        return {
            "total_users": total_users,
            "active_subscriptions": active_subs,
            "total_reports": total_reports,
            "reports_this_month": reports_this_month,
            "mrr_usd": round(mrr_usd, 2),
            "conversion_rate_percent": round(conversion_rate, 2),
        }

    def get_production_signals(
        self,
        *,
        territory: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        query = self.supabase.table("production_signals").select("*")
        if territory:
            query = query.eq("territory", territory)
        if start_date:
            query = query.gte("submission_date", start_date)
        if end_date:
            query = query.lte("submission_date", end_date)
        result = query.order("submission_date", desc=True).execute()
        items = result.data or []
        return items, len(items)
