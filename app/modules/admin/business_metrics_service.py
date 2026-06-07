"""Business Metrics dashboard service.

Computes the admin Business Metrics that are derivable from the platform's own
data (users / subscriptions / reports): MRR/ARR, churn, conversion, activation,
plan & role distribution, and — once billing geography has been captured from
Stripe — geographic distribution. Metrics that need data we don't yet collect
(CAC/LTV, acquisition channels, API usage, NPS, B2B contracts) are intentionally
out of scope and surfaced as "coming soon" by the frontend.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import Settings
from app.core.database_client import DatabaseClient

PAID_USER_TYPES = {"paid", "b2b"}
_CANCELLED = {"cancelled", "canceled"}

PLAN_DISPLAY_NAMES: dict[str, str] = {
    "free": "Free",
    "professional": "Professional",
    "producer": "Producer",
    "studio": "Studio",
    "single": "Professional",  # legacy alias
}

# Display names for the countries we expect to see most. Unknown codes fall
# back to the raw ISO code so nothing is dropped.
COUNTRY_NAMES: dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "IE": "Ireland",
    "NZ": "New Zealand",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "NL": "Netherlands",
    "ZA": "South Africa",
    "IN": "India",
}

US_STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class BusinessMetricsDashboardService:
    def __init__(
        self,
        db: DatabaseClient,
        settings: Settings,
        fx_converter: Callable[[float, str], float] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self._fx_converter = fx_converter
        self._fx = None

    # ── currency ──────────────────────────────────────────────────────────
    def _to_usd(self, amount: float, currency: str | None) -> float:
        code = (currency or "usd").upper()
        if code == "USD" or amount == 0:
            return amount
        if self._fx_converter is not None:
            return self._fx_converter(amount, code)
        try:
            if self._fx is None:
                from app.modules.fx.service import FXService

                self._fx = FXService(self.settings)
            converted, _ = self._fx.convert(amount, code, "USD")
            return converted
        except Exception:
            # FX unavailable — fall back to the raw figure rather than dropping it.
            return amount

    # ── public ────────────────────────────────────────────────────────────
    def get_dashboard(self) -> dict[str, Any]:
        users = self.db.table("users").select(
            "id, user_type, role, plan, country, state, created_at"
        ).execute().data or []
        subs = self.db.table("subscriptions").select(
            "user_id, status, amount_cents, currency, plan_type, created_at, cancelled_at"
        ).execute().data or []
        reports = self.db.table("reports").select("user_id").execute().data or []

        now = datetime.now(timezone.utc)
        month_ago = now - timedelta(days=30)

        total_users = len(users)
        paid_user_ids = {u["id"] for u in users if u.get("user_type") in PAID_USER_TYPES}
        total_paid_users = len(paid_user_ids)

        active_subs = [s for s in subs if s.get("status") == "active"]

        # ── MRR / ARR ────────────────────────────────────────────────────
        mrr_by_currency: dict[str, float] = defaultdict(float)
        for s in active_subs:
            code = (s.get("currency") or "usd").upper()
            mrr_by_currency[code] += (s.get("amount_cents") or 0) / 100
        mrr_usd = sum(self._to_usd(amt, code) for code, amt in mrr_by_currency.items())
        arr_usd = mrr_usd * 12

        # ── churn (last 30d) ─────────────────────────────────────────────
        cancelled_30 = sum(
            1
            for s in subs
            if s.get("status") in _CANCELLED
            and (dt := _parse_dt(s.get("cancelled_at"))) is not None
            and dt >= month_ago
        )
        churn_denom = len(active_subs) + cancelled_30
        monthly_churn_percent = round(cancelled_30 / churn_denom * 100, 2) if churn_denom else 0.0

        # ── conversion / time-to-convert ─────────────────────────────────
        free_to_paid_percent = round(total_paid_users / total_users * 100, 2) if total_users else 0.0

        first_sub_created: dict[str, datetime] = {}
        for s in subs:
            uid, ts = s.get("user_id"), _parse_dt(s.get("created_at"))
            if uid and ts and (uid not in first_sub_created or ts < first_sub_created[uid]):
                first_sub_created[uid] = ts
        user_created = {u["id"]: _parse_dt(u.get("created_at")) for u in users}
        deltas = [
            (first_sub_created[uid] - user_created[uid]).total_seconds() / 86400
            for uid in paid_user_ids
            if first_sub_created.get(uid) and user_created.get(uid)
            and first_sub_created[uid] >= user_created[uid]
        ]
        avg_days_to_convert = round(sum(deltas) / len(deltas), 1) if deltas else None

        # ── activation ───────────────────────────────────────────────────
        activated = len({r.get("user_id") for r in reports if r.get("user_id")})
        activation_rate_percent = round(activated / total_users * 100, 1) if total_users else 0.0

        # ── plan & role distribution ─────────────────────────────────────
        plan_counts: dict[str, int] = defaultdict(int)
        for u in users:
            plan = PLAN_DISPLAY_NAMES.get(u.get("plan") or "free", (u.get("plan") or "Free").title())
            plan_counts[plan] += 1
        plan_distribution = sorted(
            ({"plan": p, "count": c} for p, c in plan_counts.items()),
            key=lambda x: x["count"],
            reverse=True,
        )

        role_counts: dict[str, int] = defaultdict(int)
        for u in users:
            role = (u.get("role") or "").strip() or "Unspecified"
            role_counts[role] += 1
        role_distribution = sorted(
            ({"role": r, "count": c} for r, c in role_counts.items()),
            key=lambda x: x["count"],
            reverse=True,
        )

        # ── geographic distribution ──────────────────────────────────────
        active_sub_by_user: dict[str, dict] = {}
        for s in active_subs:
            uid = s.get("user_id")
            if uid and uid not in active_sub_by_user:
                active_sub_by_user[uid] = s

        country_users: dict[str, int] = defaultdict(int)
        country_revenue: dict[str, float] = defaultdict(float)
        state_users: dict[str, int] = defaultdict(int)
        state_revenue: dict[str, float] = defaultdict(float)
        known_geo_paid = 0

        for u in users:
            if u.get("user_type") not in PAID_USER_TYPES:
                continue
            sub = active_sub_by_user.get(u["id"])
            revenue = self._to_usd((sub.get("amount_cents") or 0) / 100, sub.get("currency")) if sub else 0.0
            country = u.get("country")
            key = country.upper() if country else "__unknown__"
            country_users[key] += 1
            country_revenue[key] += revenue
            if country:
                known_geo_paid += 1
                if key == "US" and u.get("state"):
                    st = u["state"].upper()
                    state_users[st] += 1
                    state_revenue[st] += revenue

        geo_available = known_geo_paid > 0
        geographic: list[dict[str, Any]] = []
        if geo_available:
            for code, count in sorted(country_users.items(), key=lambda x: x[1], reverse=True):
                is_unknown = code == "__unknown__"
                geographic.append({
                    "country_code": "" if is_unknown else code,
                    "country": "Unknown" if is_unknown else COUNTRY_NAMES.get(code, code),
                    "users": count,
                    "percentage": round(count / total_paid_users * 100, 1) if total_paid_users else 0.0,
                    "revenue_usd": round(country_revenue[code], 2),
                })

        us_states = [
            {
                "state_code": code,
                "state": US_STATE_NAMES.get(code, code),
                "users": count,
                "revenue_usd": round(state_revenue[code], 2),
            }
            for code, count in sorted(state_users.items(), key=lambda x: x[1], reverse=True)
        ]

        return {
            "total_users": total_users,
            "total_paid_users": total_paid_users,
            "active_subscriptions": len(active_subs),
            "mrr_usd": round(mrr_usd, 2),
            "arr_usd": round(arr_usd, 2),
            "mrr_by_currency": [
                {"currency": code, "amount": round(amt, 2)}
                for code, amt in sorted(mrr_by_currency.items(), key=lambda x: x[1], reverse=True)
            ],
            "monthly_churn_percent": monthly_churn_percent,
            "free_to_paid_percent": free_to_paid_percent,
            "avg_days_to_convert": avg_days_to_convert,
            "activation_rate_percent": activation_rate_percent,
            "plan_distribution": plan_distribution,
            "role_distribution": role_distribution,
            "geo_available": geo_available,
            "geographic": geographic,
            "us_states": us_states,
        }
