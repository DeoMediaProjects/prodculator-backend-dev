from app.core.database_client import DatabaseClient


class SubscriptionService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def get_active_subscription(self, user_id: str) -> dict | None:
        result = (
            self.supabase.table("subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    def can_generate_report(self, user_id: str) -> tuple[bool, str]:
        subscription = self.get_active_subscription(user_id)
        if not subscription:
            free_reports = (
                self.supabase.table("reports")
                .select("id")
                .eq("user_id", user_id)
                .eq("report_type", "free")
                .execute()
            )
            free_report_count = len(free_reports.data or [])
            if free_report_count > 0:
                return (
                    False,
                    "Free report already used. Please upgrade to generate more reports.",
                )
            return (True, "Free report available")

        report_limit = subscription.get("report_limit")
        if report_limit in (-1, None):
            return (True, "Unlimited reports")

        query = self.supabase.table("reports").select("id").eq("user_id", user_id)
        period_start = subscription.get("current_period_start")
        period_end = subscription.get("current_period_end")
        if period_start:
            query = query.gte("created_at", period_start)
        if period_end:
            query = query.lte("created_at", period_end)

        report_count = len(query.execute().data or [])
        if report_count >= report_limit:
            return (False, f"Report limit reached ({report_count}/{report_limit}).")

        remaining = report_limit - report_count
        return (True, f"{remaining} reports remaining this period")

