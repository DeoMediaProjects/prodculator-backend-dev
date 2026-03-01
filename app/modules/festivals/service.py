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


class FestivalsService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def get_festivals(self) -> list[dict]:
        result = self.supabase.table("film_festivals").select("*").execute()
        rows = [_festival_from_db(row) for row in (result.data or [])]
        rows.sort(
            key=lambda r: (
                r.get("daysUntilNextDeadline") is None,
                r.get("daysUntilNextDeadline") or 0,
            )
        )
        return rows

    # ── Admin methods ──────────────────────────────────────────────────────────

    def list_for_admin(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        count = (
            self.supabase.table("film_festivals").select("*", count="exact", head=True).execute().count or 0
        )
        rows = (
            self.supabase.table("film_festivals")
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
            .data
            or []
        )
        return [_festival_from_db(r) for r in rows], count

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        db_payload = _festival_to_db(payload)
        db_payload["id"] = str(uuid4())
        db_payload["created_at"] = now
        db_payload["updated_at"] = now
        result = self.supabase.table("film_festivals").insert(db_payload).select("*").single().execute()
        return _festival_from_db(result.data)

    def update(self, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
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

    def delete(self, row_id: str) -> None:
        self.supabase.table("film_festivals").delete().eq("id", row_id).execute()
