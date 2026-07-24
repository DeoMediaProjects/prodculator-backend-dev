#!/usr/bin/env python3
"""Create low-cost ($1/month by default) demo Stripe prices and print the
STRIPE_PRICE_* env lines that point at them.

Why: the plan env vars (STRIPE_PRICE_PRODUCER_USD, ...) point at the REAL plan
prices ($149/mo etc.), so live checkout charges the real amount. For a demo you
want the same plans to cost ~$1. This clones each configured plan price onto a
new $1/month price on the SAME Stripe product + currency, and prints the env
lines to paste into the backend host. Creating a Price never charges anyone.

Idempotent: reuses an existing demo price (matched by lookup_key) instead of
creating duplicates, so it's safe to re-run.

Runs against whatever mode STRIPE_SECRET_KEY is in — the script prints the mode
up front so you can confirm it matches where you're demoing. LIVE-mode prices
only work with a live secret key, TEST-mode with a test key.

Usage:
    python scripts/create_demo_prices.py                 # $1.00 / month
    python scripts/create_demo_prices.py --amount 200    # $2.00 / month (minor units)
    python scripts/create_demo_prices.py --interval-days 2   # 2-day cycle instead of monthly

After running, copy the printed STRIPE_PRICE_* lines into the backend host's
env and redeploy. Keep the real price IDs saved so you can switch back after
the demo.
"""
import argparse
import sys

import stripe

# Allow running from the project root.
sys.path.insert(0, ".")

from app.core.config import get_settings

# Every plan price the public checkout can resolve to.
PLAN_ENV_VARS = [
    "STRIPE_PRICE_PROFESSIONAL_USD",
    "STRIPE_PRICE_PROFESSIONAL_GBP",
    "STRIPE_PRICE_PRODUCER_USD",
    "STRIPE_PRICE_PRODUCER_GBP",
    "STRIPE_PRICE_STUDIO_USD",
    "STRIPE_PRICE_STUDIO_GBP",
    # Annual variants (GBP only in current config); harmless if unset.
    "STRIPE_PRICE_PROFESSIONAL_ANNUAL_GBP",
    "STRIPE_PRICE_PRODUCER_ANNUAL_GBP",
    "STRIPE_PRICE_STUDIO_ANNUAL_GBP",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--amount", type=int, default=100,
        help="Price in the currency's MINOR unit (100 = $1.00 / £1.00). Default 100.",
    )
    parser.add_argument(
        "--interval-days", type=int, default=None,
        help="If set, use a day-based cycle of N days instead of monthly (e.g. 2 for the "
             "compressed-cycle billing test). Default: monthly.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        print("ERROR: STRIPE_SECRET_KEY is not set. Configure it before running.", file=sys.stderr)
        return 1
    stripe.api_key = settings.STRIPE_SECRET_KEY

    mode = "LIVE" if settings.STRIPE_SECRET_KEY.startswith("sk_live") else "TEST"
    if args.interval_days:
        recurring = {"interval": "day", "interval_count": args.interval_days}
        cadence = f"{args.interval_days}day"
    else:
        recurring = {"interval": "month"}
        cadence = "monthly"

    print(f"# Stripe mode: {mode}")
    print(f"# Demo price: {args.amount} minor units / {cadence}")
    print("# Paste the lines below into the backend host env, then redeploy.\n")

    created_any = False
    for var in PLAN_ENV_VARS:
        real_id = (getattr(settings, var, "") or "").strip()
        if not real_id:
            print(f"# {var} not configured — skipped")
            continue

        try:
            real = stripe.Price.retrieve(real_id).to_dict()
        except Exception as exc:  # noqa: BLE001 — surface and continue per-price
            print(f"# {var}={real_id}  ← ERROR retrieving ({exc}); left as-is")
            continue

        lookup_key = f"demo_{args.amount}_{cadence}_{real_id}"
        existing = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1)
        if existing.data:
            new_id = existing.data[0].id
        else:
            created = stripe.Price.create(
                product=real["product"],
                currency=real["currency"],
                unit_amount=args.amount,
                recurring=recurring,
                lookup_key=lookup_key,
                nickname=f"[DEMO {args.amount} {cadence}] cloned from {real_id}",
                metadata={"demo_price": "true", "source_price_id": real_id},
            )
            new_id = created.id
        created_any = True
        print(f"{var}={new_id}")

    if not created_any:
        print("\n# Nothing to do — no STRIPE_PRICE_* plan vars are configured.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
