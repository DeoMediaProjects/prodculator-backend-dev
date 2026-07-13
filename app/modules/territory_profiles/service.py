from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient
from app.core.territories import resolve_territory

_TABLE = "territory_profiles"

# camelCase ↔ snake_case (single-word fields need no mapping)
_CAMEL_TO_SNAKE: dict[str, str] = {
    "isoCode": "iso_code",
    "crewDepthTier": "crew_depth_tier",
    "crewDepthScore": "crew_depth_score",
    "crewDepthNotes": "crew_depth_notes",
    "infrastructureTier": "infrastructure_tier",
    "infrastructureScore": "infrastructure_score",
    "infrastructureNotes": "infrastructure_notes",
    "certWeeksMin": "cert_weeks_min",
    "certWeeksMax": "cert_weeks_max",
    "paymentWeeksMin": "payment_weeks_min",
    "paymentWeeksMax": "payment_weeks_max",
    "bankabilitySourceQuality": "bankability_source_quality",
    "bankabilitySourceNote": "bankability_source_note",
    "bankabilityRealWorldConfirms": "bankability_real_world_confirms",
    "bankabilitySuspended": "bankability_suspended",
    "bankabilitySourceUrl": "bankability_source_url",
    "bankabilityAiRule": "bankability_ai_rule",
    "intlProductions3yr": "intl_productions_3yr",
    "intlProductionsSource": "intl_productions_source",
    "lastReviewedAt": "last_reviewed_at",
    "reviewedBy": "reviewed_by",
    "reviewNotes": "review_notes",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}
_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _CAMEL_TO_SNAKE.items()}


def _to_db(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in payload.items():
        result[_CAMEL_TO_SNAKE.get(k, k)] = v
    if result.get("id") == "":
        result.pop("id")
    return result


def _from_db(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat()
        result[_SNAKE_TO_CAMEL.get(k, k)] = v
    return result


class TerritoryProfilesService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def list_for_admin(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        count = (
            self.supabase.table(_TABLE)
            .select("*", count="exact", head=True)
            .execute()
            .count
            or 0
        )
        rows = (
            self.supabase.table(_TABLE)
            .select("*")
            .order("territory", desc=False)
            .range(offset, offset + limit - 1)
            .execute()
            .data
            or []
        )
        return [_from_db(r) for r in rows], count

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        territory = (payload.get("territory") or "").strip()
        if not territory:
            raise ValueError("territory is required")
        if resolve_territory(territory) is None:
            raise ValueError(f"'{territory}' is not a canonical territory")

        existing = (
            self.supabase.table(_TABLE)
            .select("id")
            .eq("territory", territory)
            .execute()
            .data
            or []
        )
        if existing:
            raise ValueError(f"A profile for '{territory}' already exists")

        now = datetime.now(timezone.utc).isoformat()
        db_payload = _to_db(payload)
        db_payload.setdefault("id", str(uuid4()))
        db_payload["created_at"] = now
        db_payload["updated_at"] = now
        result = (
            self.supabase.table(_TABLE)
            .insert(db_payload)
            .select("*")
            .single()
            .execute()
        )
        return _from_db(result.data)

    def update(self, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db_payload = _to_db(payload)
        db_payload.pop("id", None)
        db_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = (
            self.supabase.table(_TABLE)
            .update(db_payload)
            .eq("id", row_id)
            .select("*")
            .single()
            .execute()
        )
        return _from_db(result.data)

    def delete(self, row_id: str) -> None:
        self.supabase.table(_TABLE).delete().eq("id", row_id).execute()
