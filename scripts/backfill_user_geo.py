"""Backfill ``users.country`` / ``users.state`` from Stripe billing data.

For every user that has a Stripe customer (via their subscription) but no
country recorded yet, fetch the Stripe Customer's billing address — falling
back to the latest charge's billing details — and persist country/state.

New paid users get this captured automatically from the checkout session
(see ``WebhookHandler._handle_checkout_completed``); this script is a one-off
to populate the existing user base.

Run:  python -m scripts.backfill_user_geo [--dry-run]
"""
from __future__ import annotations

import argparse
import logging

import stripe

from app.core.config import get_settings
from app.core.database_client import DatabaseClient
from app.core.db import get_db_context

logger = logging.getLogger(__name__)


def _address(obj) -> dict:
    """Return an address-like dict from a Stripe object or plain dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    return dict(obj) if hasattr(obj, "keys") else {}


def geo_for_customer(customer_id: str) -> tuple[str | None, str | None]:
    """Best-effort billing country/state for a Stripe customer."""
    try:
        customer = stripe.Customer.retrieve(customer_id)
    except Exception as exc:
        logger.warning("Stripe customer %s lookup failed: %s", customer_id, exc)
        return None, None

    address = _address(customer.get("address") if isinstance(customer, dict) else getattr(customer, "address", None))
    country, state = address.get("country"), address.get("state")
    if country:
        return country, state

    # Fall back to the most recent charge's billing details — every payment
    # carries the address even when the Customer object doesn't.
    try:
        charges = stripe.Charge.list(customer=customer_id, limit=1)
        data = charges.get("data") if isinstance(charges, dict) else charges.data
        if data:
            billing = data[0].get("billing_details") if isinstance(data[0], dict) else getattr(data[0], "billing_details", None)
            addr = _address((billing or {}).get("address") if isinstance(billing, dict) else getattr(billing, "address", None))
            return addr.get("country"), addr.get("state")
    except Exception as exc:
        logger.warning("Stripe charge lookup failed for %s: %s", customer_id, exc)
    return country, state


def backfill_user_geo(db: DatabaseClient, *, dry_run: bool = False) -> dict[str, int]:
    """Populate country/state for users missing it. Returns a small summary."""
    subs = db.table("subscriptions").select("user_id, stripe_customer_id").execute().data or []
    customer_by_user: dict[str, str] = {}
    for s in subs:
        uid, cust = s.get("user_id"), s.get("stripe_customer_id")
        if uid and cust and uid not in customer_by_user:
            customer_by_user[uid] = cust

    users = db.table("users").select("id, country").execute().data or []
    pending = [u for u in users if not u.get("country") and u["id"] in customer_by_user]

    updated = 0
    for u in pending:
        uid = u["id"]
        country, state = geo_for_customer(customer_by_user[uid])
        if not country:
            continue
        logger.info("user %s -> country=%s state=%s", uid, country, state)
        if not dry_run:
            payload: dict = {"country": country}
            if state:
                payload["state"] = state
            db.table("users").update(payload).eq("id", uid).execute()
        updated += 1

    return {"candidates": len(pending), "updated": updated}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Backfill user billing geography from Stripe")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY
    with get_db_context() as session:
        db = DatabaseClient(session, settings)
        result = backfill_user_geo(db, dry_run=args.dry_run)
    logger.info("Backfill complete: %s", result)


if __name__ == "__main__":
    main()
