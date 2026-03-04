from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient

_TABLE = "crew_costs"
_SYNC_SETTINGS_TABLE = "sync_settings"
_PENDING_CHANGES_TABLE = "pending_changes"
_RESOURCE_TYPE = "crew_costs"

# ── Crew cost field maps (camelCase ↔ snake_case) ────────────────────────────

_CAMEL_TO_SNAKE: dict[str, str] = {
    "dayRate": "day_rate",
    "weekRate": "week_rate",
    "lastUpdated": "last_updated",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}
_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _CAMEL_TO_SNAKE.items()}

# Pending changes field maps
_PC_CAMEL_TO_SNAKE: dict[str, str] = {
    "currentValue": "current_value",
    "detectedValue": "detected_value",
    "resourceId": "resource_id",
    "resourceType": "resource_type",
    "createdAt": "created_at",
    "resolvedAt": "resolved_at",
    "resolvedBy": "resolved_by",
}
_PC_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _PC_CAMEL_TO_SNAKE.items()}

# Sync settings field maps
_SS_CAMEL_TO_SNAKE: dict[str, str] = {
    "lastSyncAt": "last_sync_at",
    "nextScheduledCheck": "next_scheduled",
    "resourceType": "resource_type",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}
_SS_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _SS_CAMEL_TO_SNAKE.items()}


def _crew_to_db(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in payload.items():
        result[_CAMEL_TO_SNAKE.get(k, k)] = v
    if result.get("id") == "":
        result.pop("id")
    return result


def _crew_from_db(row: dict[str, Any]) -> dict[str, Any]:
    return {_SNAKE_TO_CAMEL.get(k, k): v for k, v in row.items()}


def _pending_change_from_db(row: dict[str, Any]) -> dict[str, Any]:
    return {_PC_SNAKE_TO_CAMEL.get(k, k): v for k, v in row.items()}


def _sync_settings_from_db(row: dict[str, Any]) -> dict[str, Any]:
    return {_SS_SNAKE_TO_CAMEL.get(k, k): v for k, v in row.items()}


class CrewCostsService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    # ── Admin CRUD ───────────────────────────────────────────────────────────

    def list_for_admin(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        count = self.supabase.table(_TABLE).select("*", count="exact", head=True).execute().count or 0
        rows = (
            self.supabase.table(_TABLE)
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
            .data
            or []
        )
        return [_crew_from_db(r) for r in rows], count

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        db_payload = _crew_to_db(payload)
        db_payload.setdefault("id", str(uuid4()))
        db_payload["created_at"] = now
        db_payload["updated_at"] = now
        result = self.supabase.table(_TABLE).insert(db_payload).select("*").single().execute()
        return _crew_from_db(result.data)

    def update(self, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db_payload = _crew_to_db(payload)
        db_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = (
            self.supabase.table(_TABLE)
            .update(db_payload)
            .eq("id", row_id)
            .select("*")
            .single()
            .execute()
        )
        return _crew_from_db(result.data)

    def delete(self, row_id: str) -> None:
        self.supabase.table(_TABLE).delete().eq("id", row_id).execute()

    # ── Sync status ──────────────────────────────────────────────────────────

    def get_sync_status(self) -> dict[str, Any]:
        all_rows = self.supabase.table(_TABLE).select("territory").execute().data or []
        territories = len({r.get("territory") for r in all_rows if r.get("territory")})

        pending_count = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .select("*", count="exact", head=True)
            .eq("resource_type", _RESOURCE_TYPE)
            .eq("status", "pending")
            .execute()
            .count or 0
        )

        settings_row = self._get_or_create_sync_settings()
        last_sync = settings_row.get("last_sync_at")
        next_scheduled = settings_row.get("next_scheduled")

        days_since = 0
        if last_sync:
            try:
                last_dt = datetime.fromisoformat(str(last_sync))
                days_since = (datetime.now(timezone.utc) - last_dt).days
            except (ValueError, TypeError):
                days_since = 0

        return {
            "territoriesSyncing": territories,
            "pendingChanges": pending_count,
            "daysSinceLastCheck": days_since,
            "nextScheduledCheck": str(next_scheduled) if next_scheduled else None,
        }

    # ── Pending changes ──────────────────────────────────────────────────────

    def get_pending_changes(self) -> list[dict[str, Any]]:
        rows = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .select("*")
            .eq("resource_type", _RESOURCE_TYPE)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .execute()
            .data or []
        )
        return [_pending_change_from_db(r) for r in rows]

    def approve_change(self, change_id: str, admin_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()

        change = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .select("*")
            .eq("id", change_id)
            .single()
            .execute()
            .data
        )
        if not change:
            raise ValueError("Pending change not found")

        resource_id = change.get("resource_id")
        field = change.get("field")
        detected_value = change.get("detected_value")
        if resource_id and field and detected_value is not None:
            db_field = _CAMEL_TO_SNAKE.get(field, field)
            self.supabase.table(_TABLE).update(
                {db_field: detected_value, "updated_at": now}
            ).eq("id", resource_id).execute()

        result = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .update({"status": "approved", "resolved_at": now, "resolved_by": admin_id})
            .eq("id", change_id)
            .select("*")
            .single()
            .execute()
        )
        return _pending_change_from_db(result.data)

    def reject_change(self, change_id: str, admin_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        result = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .update({"status": "rejected", "resolved_at": now, "resolved_by": admin_id})
            .eq("id", change_id)
            .select("*")
            .single()
            .execute()
        )
        return _pending_change_from_db(result.data)

    # ── Sync trigger ─────────────────────────────────────────────────────────

    def trigger_sync(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        settings = self._get_or_create_sync_settings()

        self.supabase.table(_SYNC_SETTINGS_TABLE).update(
            {"last_sync_at": now, "updated_at": now}
        ).eq("id", settings["id"]).execute()

        return {
            "message": "Sync triggered successfully",
            "triggeredAt": now,
            "resourceType": _RESOURCE_TYPE,
        }

    # ── Sync settings ────────────────────────────────────────────────────────

    def get_sync_settings(self) -> dict[str, Any]:
        row = self._get_or_create_sync_settings()
        return _sync_settings_from_db(row)

    def update_sync_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self._get_or_create_sync_settings()
        now = datetime.now(timezone.utc).isoformat()

        update_data: dict[str, Any] = {"updated_at": now}
        if "schedule" in payload:
            update_data["schedule"] = payload["schedule"]
        if "enabled" in payload:
            update_data["enabled"] = payload["enabled"]

        if "schedule" in payload and payload["schedule"]:
            update_data["next_scheduled"] = self._compute_next_scheduled(payload["schedule"])

        result = (
            self.supabase.table(_SYNC_SETTINGS_TABLE)
            .update(update_data)
            .eq("id", settings["id"])
            .select("*")
            .single()
            .execute()
        )
        return _sync_settings_from_db(result.data)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_or_create_sync_settings(self) -> dict[str, Any]:
        rows = (
            self.supabase.table(_SYNC_SETTINGS_TABLE)
            .select("*")
            .eq("resource_type", _RESOURCE_TYPE)
            .execute()
            .data or []
        )
        if rows:
            return rows[0]

        now = datetime.now(timezone.utc).isoformat()
        default = {
            "id": str(uuid4()),
            "resource_type": _RESOURCE_TYPE,
            "schedule": "monthly",
            "enabled": True,
            "last_sync_at": None,
            "next_scheduled": None,
            "created_at": now,
            "updated_at": now,
        }
        result = self.supabase.table(_SYNC_SETTINGS_TABLE).insert(default).select("*").single().execute()
        return result.data

    @staticmethod
    def _compute_next_scheduled(schedule: str) -> str:
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        days_map = {
            "monthly": 30,
            "quarterly": 90,
            "biannual": 182,
            "annual": 365,
        }
        days = days_map.get(schedule, 30)
        return (now + timedelta(days=days)).isoformat()
