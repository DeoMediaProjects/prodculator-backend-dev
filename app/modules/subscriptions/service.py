from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.models.enums import normalize_plan
from app.modules.payments.plan_catalog import build_price_to_plan_map, classify_change
from app.modules.payments.service import StripeService


class SubscriptionService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def get_active_subscription(self, user_id: str) -> dict | None:
        # Treat past_due as still-active for change/cancel purposes — the user
        # is mid-grace-period and should be able to manage their plan.
        result = (
            self.supabase.table("subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .in_("status", ["active", "trialing", "past_due"])
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    # --- Plan-change flow -----------------------------------------------------

    def preview_change(
        self,
        user_id: str,
        target_price_id: str,
        settings: Settings,
        stripe_service: StripeService,
    ) -> dict:
        """Return proration preview for a proposed plan change."""
        subscription = self.get_active_subscription(user_id)
        if not subscription or not subscription.get("stripe_subscription_id"):
            raise ValueError("no_active_subscription")

        target_plan = self._resolve_plan_from_price(target_price_id, settings)
        if not target_plan:
            raise ValueError("unknown_price_id")

        current_plan = normalize_plan(subscription.get("plan_type") or "free")
        direction = classify_change(current_plan, target_plan)

        preview = stripe_service.preview_subscription_change(
            subscription["stripe_subscription_id"], target_price_id
        )
        return {
            "direction": direction,
            "target_plan": target_plan,
            **preview,
        }

    def change_plan(
        self,
        user_id: str,
        target_price_id: str,
        idempotency_key: str,
        settings: Settings,
        stripe_service: StripeService,
    ) -> dict:
        """Apply an upgrade immediately or schedule a downgrade at period end."""
        subscription = self.get_active_subscription(user_id)
        if not subscription or not subscription.get("stripe_subscription_id"):
            raise ValueError("no_active_subscription")

        target_plan = self._resolve_plan_from_price(target_price_id, settings)
        if not target_plan:
            raise ValueError("unknown_price_id")

        current_plan = normalize_plan(subscription.get("plan_type") or "free")
        direction = classify_change(current_plan, target_plan)
        if direction == "same":
            raise ValueError("same_plan")

        is_upgrade = direction == "upgrade"
        stripe_service.change_subscription_plan(
            subscription["stripe_subscription_id"],
            target_price_id,
            prorate=is_upgrade,
            idempotency_key=idempotency_key,
        )

        # Downgrades: write pending_plan locally so the UI can show
        # "Switching to X at next renewal" before the rollover webhook fires.
        # Cleared by _handle_subscription_updated when the new plan is active.
        if not is_upgrade:
            self.supabase.table("subscriptions").update(
                {"pending_plan": target_plan}
            ).eq("id", subscription["id"]).execute()
            return {
                "status": "scheduled",
                "direction": direction,
                "target_plan": target_plan,
                "effective_at": subscription.get("current_period_end"),
            }

        # Upgrade: webhook will mirror plan_type → user.plan; no immediate write here.
        return {
            "status": "applied",
            "direction": direction,
            "target_plan": target_plan,
            "effective_at": None,
        }

    def get_current(self, user_id: str, current_plan: str) -> dict:
        """Bundle every field the frontend needs to render plan UI."""
        subscription = self.get_active_subscription(user_id)
        return {
            "subscription": subscription,
            "plan": normalize_plan(current_plan),
            "pending_plan": (subscription or {}).get("pending_plan"),
            "past_due_since": (subscription or {}).get("past_due_since"),
            "cancel_at_period_end": bool((subscription or {}).get("cancel_at_period_end")),
        }

    @staticmethod
    def _resolve_plan_from_price(price_id: str, settings: Settings) -> str | None:
        return build_price_to_plan_map(settings).get(price_id)

    def get_credits_remaining(self, user_id: str) -> int:
        result = (
            self.supabase.table("users")
            .select("credits_remaining")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return (result.data or {}).get("credits_remaining", 0) or 0

    def consume_report_credit(self, user_id: str) -> None:
        """Decrement credits_remaining by 1 after a pay-per-report usage."""
        current = self.get_credits_remaining(user_id)
        new_value = max(0, current - 1)
        self.supabase.table("users").update(
            {"credits_remaining": new_value}
        ).eq("id", user_id).execute()

    def can_generate_report(self, user_id: str) -> tuple[bool, str]:
        subscription = self.get_active_subscription(user_id)
        if not subscription:
            # Pay-per-report credits take priority over the free-report check.
            credits = self.get_credits_remaining(user_id)
            if credits > 0:
                return (True, f"pay-per-report ({credits} credit(s) remaining)")

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
            # Subscription limit hit — credits act as overflow capacity.
            # A user who bought pay-as-you-go credits and then subscribed should
            # still be able to use those credits even after the monthly cap.
            credits = self.get_credits_remaining(user_id)
            if credits > 0:
                return (True, f"pay-per-report ({credits} credit(s) remaining)")
            return (False, f"Report limit reached ({report_count}/{report_limit}).")

        remaining = report_limit - report_count
        return (True, f"{remaining} reports remaining this period")

