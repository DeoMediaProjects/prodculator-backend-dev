import logging
from datetime import datetime

import redis as sync_redis

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.models.enums import normalize_plan
from app.modules.payments.plan_catalog import (
    PLAN_REPORT_LIMITS,
    build_price_to_plan_map,
    classify_change,
)
from app.modules.payments.service import StripeService

logger = logging.getLogger(__name__)

_PLAN_LABEL: dict[str, str] = {
    "free": "Explorer",
    "professional": "Professional",
    "producer": "Producer",
    "studio": "Studio",
}


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
        """Apply an upgrade immediately or schedule a downgrade at period end.

        Upgrades:
          - Stripe charges the prorated difference immediately
            (always_invoice + error_if_incomplete).
          - After the Stripe call confirms, plan_type and report_limit are
            written to the DB synchronously so access is granted without waiting
            for the webhook to arrive.

        Downgrades:
          - A Stripe Subscription Schedule defers the item change to
            current_period_end. The subscription item stays on the current
            (paid) plan so customer.subscription.updated fires at the natural
            rollover, not immediately. This prevents the user from losing access
            to a tier they already paid for.
          - pending_plan is written locally so the UI can display
            "Switching to X at renewal" right away.
          - stripe_schedule_id is stored so the scheduled change can be
            cancelled later.
        """
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

        # Release any active schedule before making a new change — covers the
        # case where a user had a pending downgrade and now wants to upgrade or
        # change to a different plan.
        existing_schedule_id = subscription.get("stripe_schedule_id")
        if existing_schedule_id:
            try:
                stripe_service.cancel_scheduled_plan_change(existing_schedule_id)
            except Exception:
                logger.warning(
                    "Failed to release existing schedule %s for user %s — proceeding",
                    existing_schedule_id,
                    user_id,
                )
            self.supabase.table("subscriptions").update(
                {"stripe_schedule_id": None, "pending_plan": None}
            ).eq("id", subscription["id"]).execute()

        is_upgrade = direction == "upgrade"

        if is_upgrade:
            stripe_service.change_subscription_plan(
                subscription["stripe_subscription_id"],
                target_price_id,
                prorate=True,
                idempotency_key=idempotency_key,
            )
            # Write the new plan synchronously so the user gets access
            # immediately rather than waiting for the webhook to arrive.
            new_report_limit = PLAN_REPORT_LIMITS.get(target_plan, 3)
            self.supabase.table("subscriptions").update(
                {"plan_type": target_plan, "report_limit": new_report_limit, "pending_plan": None}
            ).eq("id", subscription["id"]).execute()
            self.supabase.table("users").update(
                {"plan": target_plan, "user_type": "paid"}
            ).eq("id", user_id).execute()
            self._bust_user_cache(user_id, settings)
            self._send_plan_email(
                user_id,
                "plan_upgraded",
                {
                    "previous_plan_name": _PLAN_LABEL.get(current_plan, current_plan.title()),
                    "new_plan_name": _PLAN_LABEL.get(target_plan, target_plan.title()),
                },
                settings,
            )
            return {
                "status": "applied",
                "direction": direction,
                "target_plan": target_plan,
                "effective_at": None,
            }

        # Downgrade: create a Subscription Schedule so the plan change defers
        # to period end and the user keeps their current tier until then.
        schedule_id = stripe_service.schedule_subscription_downgrade(
            subscription["stripe_subscription_id"],
            target_price_id,
        )
        self.supabase.table("subscriptions").update(
            {"pending_plan": target_plan, "stripe_schedule_id": schedule_id}
        ).eq("id", subscription["id"]).execute()
        self._send_plan_email(
            user_id,
            "downgrade_scheduled",
            {
                "current_plan_name": _PLAN_LABEL.get(current_plan, current_plan.title()),
                "new_plan_name": _PLAN_LABEL.get(target_plan, target_plan.title()),
                "effective_date": self._format_period_end(
                    subscription.get("current_period_end")
                ),
            },
            settings,
        )
        return {
            "status": "scheduled",
            "direction": direction,
            "target_plan": target_plan,
            "effective_at": subscription.get("current_period_end"),
        }

    def cancel_scheduled_change(
        self,
        user_id: str,
        settings: Settings,
        stripe_service: StripeService,
    ) -> dict:
        """Cancel a pending downgrade by releasing the Stripe Subscription Schedule.

        Releases the schedule (not cancels it), which returns the subscription
        to normal management on the current plan. Clears pending_plan and
        stripe_schedule_id from the DB.
        """
        subscription = self.get_active_subscription(user_id)
        if not subscription:
            raise ValueError("no_active_subscription")
        if not subscription.get("pending_plan"):
            raise ValueError("no_scheduled_change")

        schedule_id = subscription.get("stripe_schedule_id")
        if schedule_id:
            stripe_service.cancel_scheduled_plan_change(schedule_id)

        self.supabase.table("subscriptions").update(
            {"pending_plan": None, "stripe_schedule_id": None}
        ).eq("id", subscription["id"]).execute()
        return {"cancelled": True}

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

    @staticmethod
    def _bust_user_cache(user_id: str, settings: Settings) -> None:
        try:
            r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
            r.delete(f"user_profile:{user_id}")
            r.close()
        except Exception as exc:
            logger.warning("Cache bust failed for user %s: %s", user_id, exc)

    def _send_plan_email(
        self,
        user_id: str,
        template_name: str,
        context: dict,
        settings: Settings,
    ) -> None:
        try:
            from app.modules.email.service import EmailService
            result = (
                self.supabase.table("users")
                .select("email")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if not rows or not rows[0].get("email"):
                return
            EmailService(settings).send(rows[0]["email"], template_name, context)
        except Exception as exc:
            logger.warning("Plan email failed (%s) for user %s: %s", template_name, user_id, exc)

    @staticmethod
    def _format_period_end(iso_str: str | None) -> str:
        if not iso_str:
            return "your next billing date"
        try:
            dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
            return dt.strftime("%B %d, %Y")
        except Exception:
            return "your next billing date"

    @staticmethod
    def _count_chargeable(rows: list[dict] | None) -> int:
        """Count report rows that count against quota — i.e. everything except
        failed reports. A failed report (e.g. a Claude outage) must not consume a
        user's monthly slot or their one free report."""
        return sum(1 for row in (rows or []) if (row or {}).get("status") != "failed")

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

    def refund_report_credit(self, user_id: str) -> None:
        """Increment credits_remaining by 1 — the inverse of consume_report_credit.

        Used when a report that consumed a pay-per-report credit fails (e.g. a
        Claude outage), so the user is never charged for a report they didn't get.
        """
        current = self.get_credits_remaining(user_id)
        self.supabase.table("users").update(
            {"credits_remaining": current + 1}
        ).eq("id", user_id).execute()

    def get_usage(self, user_id: str, current_plan: str) -> dict:
        """Return current-period report usage for the dashboard widget.

        Returns the same counts that can_generate_report uses internally so
        there is no drift between the gate and the displayed numbers.
        """
        from app.models.enums import normalize_plan

        plan = normalize_plan(current_plan)
        subscription = self.get_active_subscription(user_id)
        credits = self.get_credits_remaining(user_id)

        if not subscription:
            # Free / pay-per-report path
            free_reports_result = (
                self.supabase.table("reports")
                .select("id, status")
                .eq("user_id", user_id)
                .eq("report_type", "free")
                .execute()
            )
            free_used = self._count_chargeable(free_reports_result.data)
            # Free users get exactly 1 lifetime trial
            limit: int | None = 1
            used = free_used
            remaining: int | None = max(0, limit - used) if limit is not None else None
            can_gen, reason = self.can_generate_report(user_id)
            return {
                "plan": plan,
                "reports_used": used,
                "reports_limit": limit,
                "reports_remaining": remaining,
                "credits_remaining": credits,
                "period_start": None,
                "period_end": None,
                "can_generate": can_gen,
                "reason": reason,
            }

        report_limit = subscription.get("report_limit")
        period_start = subscription.get("current_period_start")
        period_end = subscription.get("current_period_end")

        if report_limit in (-1, None):
            # Unlimited plan
            can_gen, reason = self.can_generate_report(user_id)
            return {
                "plan": plan,
                "reports_used": 0,
                "reports_limit": None,
                "reports_remaining": None,
                "credits_remaining": credits,
                "period_start": period_start,
                "period_end": period_end,
                "can_generate": can_gen,
                "reason": reason,
            }

        query = self.supabase.table("reports").select("id, status").eq("user_id", user_id)
        if period_start:
            query = query.gte("created_at", period_start)
        if period_end:
            query = query.lte("created_at", period_end)

        used = self._count_chargeable(query.execute().data)
        remaining = max(0, report_limit - used)
        can_gen, reason = self.can_generate_report(user_id)
        return {
            "plan": plan,
            "reports_used": used,
            "reports_limit": report_limit,
            "reports_remaining": remaining,
            "credits_remaining": credits,
            "period_start": period_start,
            "period_end": period_end,
            "can_generate": can_gen,
            "reason": reason,
        }

    def can_generate_report(self, user_id: str) -> tuple[bool, str]:
        subscription = self.get_active_subscription(user_id)
        if not subscription:
            # Pay-per-report credits take priority over the free-report check.
            credits = self.get_credits_remaining(user_id)
            if credits > 0:
                return (True, f"pay-per-report ({credits} credit(s) remaining)")

            free_reports = (
                self.supabase.table("reports")
                .select("id, status")
                .eq("user_id", user_id)
                .eq("report_type", "free")
                .execute()
            )
            free_report_count = self._count_chargeable(free_reports.data)
            if free_report_count > 0:
                return (
                    False,
                    "Free report already used. Please upgrade to generate more reports.",
                )
            return (True, "Free report available")

        report_limit = subscription.get("report_limit")
        if report_limit in (-1, None):
            return (True, "Unlimited reports")

        query = self.supabase.table("reports").select("id, status").eq("user_id", user_id)
        period_start = subscription.get("current_period_start")
        period_end = subscription.get("current_period_end")
        if period_start:
            query = query.gte("created_at", period_start)
        if period_end:
            query = query.lte("created_at", period_end)

        report_count = self._count_chargeable(query.execute().data)
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
