from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.core.database_client import DatabaseClient

PLAN_DISPLAY_NAMES: dict[str, str] = {
    "free": "Free",
    "single": "Pro Monthly",
    "studio": "Studio",
}

PLAN_PRICES: dict[str, float] = {
    "free": 0,
    "single": 49,
    "studio": 249,
}


class SubscriberAdminService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def get_subscriber_metrics(self) -> dict[str, Any]:
        paid_users_count = (
            self.supabase.table("users")
            .select("*", count="exact", head=True)
            .in_("user_type", ["paid", "b2b"])
            .execute()
            .count or 0
        )

        active_subs = (
            self.supabase.table("subscriptions")
            .select("amount_cents, currency, plan_type")
            .eq("status", "active")
            .execute()
            .data or []
        )

        mrr_usd = 0.0
        mrr_gbp = 0.0
        plan_counts: dict[str, int] = defaultdict(int)
        plan_revenue: dict[str, float] = defaultdict(float)

        for sub in active_subs:
            amount_cents = sub.get("amount_cents") or 0
            currency = (sub.get("currency") or "usd").lower()
            plan_type = sub.get("plan_type") or "single"
            amount = amount_cents / 100

            if currency == "gbp":
                mrr_gbp += amount
            else:
                mrr_usd += amount

            display_plan = PLAN_DISPLAY_NAMES.get(plan_type, plan_type)
            plan_counts[display_plan] += 1
            plan_revenue[display_plan] += amount

        start_of_month = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        reports_month = (
            self.supabase.table("reports")
            .select("report_type")
            .gte("created_at", start_of_month.isoformat())
            .execute()
            .data or []
        )

        reports_total = len(reports_month)
        reports_free = sum(1 for r in reports_month if r.get("report_type") == "preview")
        reports_paid = reports_total - reports_free

        avg_reports = round(reports_paid / paid_users_count, 1) if paid_users_count else 0.0

        free_users_count = (
            self.supabase.table("users")
            .select("*", count="exact", head=True)
            .eq("user_type", "free")
            .execute()
            .count or 0
        )

        plan_distribution = [
            {"plan": "Free", "user_count": free_users_count, "revenue": 0},
        ]
        for plan_name in ["Pro Monthly", "Studio"]:
            plan_distribution.append({
                "plan": plan_name,
                "user_count": plan_counts.get(plan_name, 0),
                "revenue": round(plan_revenue.get(plan_name, 0), 2),
            })

        return {
            "total_paid_users": paid_users_count,
            "mrr_usd": round(mrr_usd, 2),
            "mrr_gbp": round(mrr_gbp, 2),
            "reports_this_month_total": reports_total,
            "reports_this_month_free": reports_free,
            "reports_this_month_paid": reports_paid,
            "avg_reports_per_user": avg_reports,
            "plan_distribution": plan_distribution,
        }

    def list_subscribers(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        count_active = (
            self.supabase.table("subscriptions")
            .select("*", count="exact", head=True)
            .eq("status", "active")
            .execute()
            .count or 0
        )
        count_past_due = (
            self.supabase.table("subscriptions")
            .select("*", count="exact", head=True)
            .eq("status", "past_due")
            .execute()
            .count or 0
        )
        count_canceled = (
            self.supabase.table("subscriptions")
            .select("*", count="exact", head=True)
            .eq("status", "canceled")
            .execute()
            .count or 0
        )

        if status:
            status_subs = (
                self.supabase.table("subscriptions")
                .select("user_id")
                .eq("status", status)
                .execute()
                .data or []
            )
            sub_user_ids = {s["user_id"] for s in status_subs if s.get("user_id")}
        else:
            all_subs = (
                self.supabase.table("subscriptions")
                .select("user_id")
                .execute()
                .data or []
            )
            sub_user_ids = {s["user_id"] for s in all_subs if s.get("user_id")}

        if search:
            search_pattern = f"%{search}%"
            matched_ids: set[str] = set()
            for field in ("name", "email", "company"):
                results = (
                    self.supabase.table("users")
                    .select("id")
                    .ilike(field, search_pattern)
                    .execute()
                    .data or []
                )
                matched_ids.update(r["id"] for r in results)
            sub_user_ids = sub_user_ids & matched_ids

        if not sub_user_ids:
            return {
                "items": [],
                "total": 0,
                "limit": limit,
                "offset": offset,
                "counts": {
                    "active": count_active,
                    "past_due": count_past_due,
                    "canceled": count_canceled,
                },
            }

        user_id_list = sorted(sub_user_ids)
        total = len(user_id_list)
        page_ids = user_id_list[offset : offset + limit]

        if not page_ids:
            return {
                "items": [],
                "total": total,
                "limit": limit,
                "offset": offset,
                "counts": {
                    "active": count_active,
                    "past_due": count_past_due,
                    "canceled": count_canceled,
                },
            }

        users = (
            self.supabase.table("users")
            .select("*")
            .in_("id", page_ids)
            .execute()
            .data or []
        )
        users_by_id = {u["id"]: u for u in users}

        subs = (
            self.supabase.table("subscriptions")
            .select("*")
            .in_("user_id", page_ids)
            .order("created_at", desc=True)
            .execute()
            .data or []
        )
        subs_by_user: dict[str, dict] = {}
        for s in subs:
            uid = s.get("user_id")
            if uid and uid not in subs_by_user:
                subs_by_user[uid] = s

        start_of_month = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        all_reports = (
            self.supabase.table("reports")
            .select("user_id, created_at")
            .in_("user_id", page_ids)
            .execute()
            .data or []
        )

        total_reports_by_user: dict[str, int] = defaultdict(int)
        month_reports_by_user: dict[str, int] = defaultdict(int)
        for r in all_reports:
            uid = r.get("user_id")
            if uid:
                total_reports_by_user[uid] += 1
                created = r.get("created_at", "")
                if created >= start_of_month.isoformat():
                    month_reports_by_user[uid] += 1

        items = []
        for uid in page_ids:
            user = users_by_id.get(uid)
            if not user:
                continue
            sub = subs_by_user.get(uid, {})
            plan_type = sub.get("plan_type") or user.get("plan") or "free"

            items.append({
                "id": uid,
                "name": user.get("name"),
                "email": user.get("email", ""),
                "company": user.get("company"),
                "plan": PLAN_DISPLAY_NAMES.get(plan_type, plan_type),
                "status": (sub.get("status") or "active").replace("_", " ").title(),
                "reports_this_month": month_reports_by_user.get(uid, 0),
                "report_limit": sub.get("report_limit"),
                "monthly_spend": (sub.get("amount_cents") or 0) / 100,
                "payment_currency": (sub.get("currency") or "USD").upper(),
                "join_date": str(user.get("created_at", ""))[:10],
                "last_active": str(user.get("last_active", ""))[:10] if user.get("last_active") else None,
                "total_reports_generated": total_reports_by_user.get(uid, 0),
            })

        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "counts": {
                "active": count_active,
                "past_due": count_past_due,
                "canceled": count_canceled,
            },
        }

    def block_subscriber(self, user_id: str) -> None:
        self.supabase.table("users").update({
            "is_blocked": True,
            "blocked_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", user_id).execute()

    def unblock_subscriber(self, user_id: str) -> None:
        self.supabase.table("users").update({
            "is_blocked": False,
            "blocked_at": None,
        }).eq("id", user_id).execute()

    def adjust_credits(self, user_id: str, adjustment: int) -> dict[str, Any]:
        user_result = (
            self.supabase.table("users")
            .select("id, credits_remaining")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if not user_result.data:
            raise ValueError("User not found")

        current = user_result.data.get("credits_remaining", 0)
        new_credits = max(0, current + adjustment)

        updated = (
            self.supabase.table("users")
            .update({"credits_remaining": new_credits})
            .eq("id", user_id)
            .select("id, credits_remaining")
            .single()
            .execute()
        )
        return updated.data
