"""Maps Stripe price IDs to plan tiers — the canonical Stripe-side fact.

Webhook handlers and the subscription-change endpoint resolve the user's plan
from the active subscription item's price, not from session/sub metadata.
Metadata depends on us setting it correctly on every code path; price IDs come
straight from Stripe and can't drift.
"""

from app.core.config import Settings
from app.models.enums import PlanType, normalize_plan

# Direction classifier — same hierarchy as RequirePlan in app/core/dependencies.py.
_PLAN_HIERARCHY: dict[str, int] = {
    "free": 0,
    "single": 1,
    "professional": 1,
    "producer": 2,
    "studio": 3,
}

# Report limits per billing period. Single source of truth — imported by
# webhook_handler and subscription service so limits can never drift apart.
PLAN_REPORT_LIMITS: dict[str, int] = {
    "free": 1,
    "professional": 1,
    "producer": 3,
    "studio": 10,
}


def build_price_to_plan_map(settings: Settings) -> dict[str, str]:
    """Return {price_id: plan_type} from configured Stripe price IDs.

    Empty/unset price IDs are skipped so the map only contains real entries.
    """
    raw_map: dict[str, str] = {
        settings.STRIPE_PRICE_SINGLE_USD: PlanType.PROFESSIONAL.value,
        settings.STRIPE_PRICE_SINGLE_GBP: PlanType.PROFESSIONAL.value,
        settings.STRIPE_PRICE_PROFESSIONAL_USD: PlanType.PROFESSIONAL.value,
        settings.STRIPE_PRICE_PROFESSIONAL_GBP: PlanType.PROFESSIONAL.value,
        settings.STRIPE_PRICE_PROFESSIONAL_ANNUAL_GBP: PlanType.PROFESSIONAL.value,
        settings.STRIPE_PRICE_PROFESSIONAL_ANNUAL_USD: PlanType.PROFESSIONAL.value,
        settings.STRIPE_PRICE_PRODUCER_USD: PlanType.PRODUCER.value,
        settings.STRIPE_PRICE_PRODUCER_GBP: PlanType.PRODUCER.value,
        settings.STRIPE_PRICE_PRODUCER_ANNUAL_GBP: PlanType.PRODUCER.value,
        settings.STRIPE_PRICE_PRODUCER_ANNUAL_USD: PlanType.PRODUCER.value,
        settings.STRIPE_PRICE_STUDIO_USD: PlanType.STUDIO.value,
        settings.STRIPE_PRICE_STUDIO_GBP: PlanType.STUDIO.value,
        settings.STRIPE_PRICE_STUDIO_ANNUAL_GBP: PlanType.STUDIO.value,
        settings.STRIPE_PRICE_STUDIO_ANNUAL_USD: PlanType.STUDIO.value,
    }
    return {price_id: plan for price_id, plan in raw_map.items() if price_id}


def resolve_plan_from_subscription(subscription: dict, settings: Settings) -> str | None:
    """Resolve the plan tier from the active subscription item's price ID.

    Stripe subscription objects carry items[].price.id — this is the source of
    truth that survives mid-cycle modifies, schedule rollovers, and metadata drift.
    Returns None if the subscription has no items or the price isn't recognized.
    """
    items = (subscription.get("items") or {}).get("data") or []
    if not items:
        return None

    price = items[0].get("price") or {}
    price_id = price.get("id")
    if not price_id:
        return None

    price_map = build_price_to_plan_map(settings)
    plan = price_map.get(price_id)
    return normalize_plan(plan) if plan else None


def classify_change(current_plan: str, target_plan: str) -> str:
    """Return 'upgrade', 'downgrade', or 'same' for a plan transition."""
    current = _PLAN_HIERARCHY.get(normalize_plan(current_plan), 0)
    target = _PLAN_HIERARCHY.get(normalize_plan(target_plan), 0)
    if target > current:
        return "upgrade"
    if target < current:
        return "downgrade"
    return "same"
