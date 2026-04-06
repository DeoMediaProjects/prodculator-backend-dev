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

    def _get_user_email(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        try:
            result = (
                self.supabase.table("users")
                .select("email")
                .eq("id", user_id)
                .single()
                .execute()
            )
            return (result.data or {}).get("email")
        except Exception:
            return None

    def get_recent_activity(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent platform events across reports, users, and subscriptions."""
        events: list[dict[str, Any]] = []

        try:
            reports = (
                self.supabase.table("reports")
                .select("id, script_title, created_at, user_id")
                .eq("status", "completed")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
                .data
                or []
            )
            for r in reports:
                events.append({
                    "id": str(r["id"]),
                    "type": "report_generated",
                    "description": f"Report generated: {r.get('script_title') or 'Untitled'}",
                    "user_email": self._get_user_email(r.get("user_id")),
                    "timestamp": str(r["created_at"]) if r.get("created_at") else None,
                })
        except Exception:
            pass

        try:
            users = (
                self.supabase.table("users")
                .select("id, email, created_at")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
                .data
                or []
            )
            for u in users:
                events.append({
                    "id": str(u["id"]),
                    "type": "user_registered",
                    "description": "New user registered",
                    "user_email": u.get("email"),
                    "timestamp": str(u["created_at"]) if u.get("created_at") else None,
                })
        except Exception:
            pass

        try:
            subs = (
                self.supabase.table("subscriptions")
                .select("id, user_id, plan_type, created_at")
                .eq("status", "active")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
                .data
                or []
            )
            for s in subs:
                events.append({
                    "id": str(s["id"]),
                    "type": "subscription_activated",
                    "description": f"{(s.get('plan_type') or 'Unknown').title()} plan activated",
                    "user_email": self._get_user_email(s.get("user_id")),
                    "timestamp": str(s["created_at"]) if s.get("created_at") else None,
                })
        except Exception:
            pass

        events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
        return events[:limit]

    def check_db_health(self) -> bool:
        """Return True if the database is reachable."""
        try:
            self.supabase.table("users").select("id", count="exact", head=True).limit(1).execute()
            return True
        except Exception:
            return False

    def get_derived_tasks(self) -> list[dict[str, Any]]:
        """Derive maintenance tasks from actual data staleness (last update > 30 days)."""
        from datetime import datetime, timedelta, timezone

        tasks: list[dict[str, Any]] = []
        stale_threshold = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        checks: list[tuple[str, str, str, str]] = [
            ("incentives", "Review and update incentive data", "high", "This week"),
            ("crew_costs", "Verify crew cost rates are current", "medium", "This week"),
            ("grants", "Sync grants database", "low", "Next week"),
        ]

        for table, task_label, priority, due in checks:
            try:
                result = (
                    self.supabase.table(table)
                    .select("updated_at")
                    .order("updated_at", desc=True)
                    .limit(1)
                    .execute()
                    .data
                )
                latest = (result or [{}])[0].get("updated_at") or ""
                if not result or str(latest) < stale_threshold:
                    tasks.append({"task": task_label, "priority": priority, "due": due})
            except Exception:
                pass

        return tasks

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
