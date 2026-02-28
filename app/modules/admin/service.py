from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient

# ── Festival field maps (camelCase ↔ snake_case) ──────────────────────────────

_CAMEL_TO_SNAKE: dict[str, str] = {
    "budgetTiers": "budget_tiers",
    "festivalDates": "festival_dates",
    "premiereRequirement": "premiere_requirement",
    "acceptanceRate": "acceptance_rate",
    "websiteUrl": "website_url",
    "filmfreewayUrl": "filmfreeway_url",
    "dataSource": "data_source",
    "isNew": "is_new",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "lastVerifiedAt": "last_verified_at",
    "notableAlumni": "notable_alumni",
    "averageBudgetOfAcceptedFilms": "average_budget_of_accepted_films",
}
_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _CAMEL_TO_SNAKE.items()}

# Keys computed on-the-fly — never written to DB
_COMPUTED_KEYS = {"currentStatus", "nextDeadline", "daysUntilNextDeadline"}


def _compute_festival_status(deadlines: list[dict]) -> dict[str, Any]:
    """Derive currentStatus, nextDeadline, daysUntilNextDeadline from deadlines array."""
    today = date.today()
    future = []
    for d in deadlines or []:
        raw = d.get("date")
        if not raw:
            continue
        try:
            dl_date = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if dl_date >= today:
            future.append((dl_date, d))

    if not future:
        return {"currentStatus": "closed", "nextDeadline": None, "daysUntilNextDeadline": None}

    future.sort(key=lambda x: x[0])
    next_date, next_dl = future[0]
    tier = (next_dl.get("tier") or "").lower()
    status_map = {
        "early-bird": "early-bird-open",
        "regular": "regular-open",
        "late": "late-open",
        "extended": "late-open",
    }
    current_status = status_map.get(tier, "upcoming")
    return {
        "currentStatus": current_status,
        "nextDeadline": next_dl,
        "daysUntilNextDeadline": (next_date - today).days,
    }


def _festival_to_db(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert camelCase frontend payload to snake_case DB dict, dropping computed/empty fields."""
    result: dict[str, Any] = {}
    for k, v in payload.items():
        if k in _COMPUTED_KEYS:
            continue
        db_key = _CAMEL_TO_SNAKE.get(k, k)
        result[db_key] = v
    # Strip empty id — DB will receive a proper UUID on create
    if result.get("id") == "":
        result.pop("id")
    return result


def _festival_from_db(row: dict[str, Any]) -> dict[str, Any]:
    """Convert snake_case DB row to camelCase and append computed status fields."""
    result: dict[str, Any] = {}
    for k, v in row.items():
        camel_key = _SNAKE_TO_CAMEL.get(k, k)
        result[camel_key] = v
    result.update(_compute_festival_status(result.get("deadlines") or []))
    return result


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

    # ── Festival-specific methods ──────────────────────────────────────────────

    def list_festivals(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        rows, total = self.list_table("film_festivals", limit=limit, offset=offset)
        return [_festival_from_db(row) for row in rows], total

    def create_festival(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        db_payload = _festival_to_db(payload)
        db_payload["id"] = str(uuid4())
        db_payload["created_at"] = now
        db_payload["updated_at"] = now
        result = self.supabase.table("film_festivals").insert(db_payload).select("*").single().execute()
        return _festival_from_db(result.data)

    def update_festival(self, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db_payload = _festival_to_db(payload)
        db_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = (
            self.supabase.table("film_festivals")
            .update(db_payload)
            .eq("id", row_id)
            .select("*")
            .single()
            .execute()
        )
        return _festival_from_db(result.data)

