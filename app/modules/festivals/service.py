from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient

_TABLE = "film_festivals"
_SYNC_SETTINGS_TABLE = "sync_settings"
_PENDING_CHANGES_TABLE = "pending_changes"
_SCRAPE_SOURCES_TABLE = "scrape_sources"
_RESOURCE_TYPE = "festivals"

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

_PC_CAMEL_TO_SNAKE: dict[str, str] = {
    "currentValue": "current_value",
    "detectedValue": "detected_value",
    "resourceId": "resource_id",
    "resourceType": "resource_type",
    "createdAt": "created_at",
    "resolvedAt": "resolved_at",
    "resolvedBy": "resolved_by",
    "recordLabel": "record_label",
}
_PC_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _PC_CAMEL_TO_SNAKE.items()}

_SS_CAMEL_TO_SNAKE: dict[str, str] = {
    "lastSyncAt": "last_sync_at",
    "nextScheduledCheck": "next_scheduled",
    "resourceType": "resource_type",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}
_SS_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _SS_CAMEL_TO_SNAKE.items()}

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


def _pending_change_from_db(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat()
        result[_PC_SNAKE_TO_CAMEL.get(k, k)] = v
    return result


def _sync_settings_from_db(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat()
        result[_SS_SNAKE_TO_CAMEL.get(k, k)] = v
    return result


class FestivalsService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def get_festivals(self) -> list[dict]:
        result = self.supabase.table(_TABLE).select("*").execute()
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
        self._materialize_approved_changes_without_resource()
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
        return [_festival_from_db(r) for r in rows], count

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        db_payload = _festival_to_db(payload)
        db_payload["id"] = str(uuid4())
        db_payload["created_at"] = now
        db_payload["updated_at"] = now
        result = self.supabase.table(_TABLE).insert(db_payload).select("*").single().execute()
        return _festival_from_db(result.data)

    def update(self, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db_payload = _festival_to_db(payload)
        db_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = (
            self.supabase.table(_TABLE)
            .update(db_payload)
            .eq("id", row_id)
            .select("*")
            .single()
            .execute()
        )
        return _festival_from_db(result.data)

    def delete(self, row_id: str) -> None:
        self.supabase.table(_TABLE).delete().eq("id", row_id).execute()

    # ── Sync status ──────────────────────────────────────────────────────────

    def get_sync_status(self) -> dict[str, Any]:
        all_rows = self.supabase.table(_TABLE).select("location").execute().data or []
        territories = len({r.get("location") for r in all_rows if r.get("location")})

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
        if field and detected_value is not None:
            db_field = _CAMEL_TO_SNAKE.get(field, field)
            if not resource_id:
                resource_id = self._get_or_create_resource_id_for_change(change, now)
            self.supabase.table(_TABLE).update(
                {db_field: detected_value, "updated_at": now}
            ).eq("id", resource_id).execute()

        update_payload: dict[str, Any] = {
            "status": "approved",
            "resolved_at": now,
            "resolved_by": admin_id,
        }
        if resource_id:
            update_payload["resource_id"] = resource_id

        result = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .update(update_payload)
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

    # ── Sync trigger ──────────────────────────────────────────────────────────

    def trigger_sync(self) -> dict[str, Any]:
        from app.core.config import get_settings
        from app.modules.scraper.service import ScraperService

        settings = get_settings()
        scraper = ScraperService(self.supabase, settings)
        return scraper.run_for_resource(_RESOURCE_TYPE, triggered_by="admin")

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

    def _get_or_create_resource_id_for_change(self, change: dict[str, Any], now: str) -> str:
        source_url = change.get("source")

        if source_url:
            existing = (
                self.supabase.table(_TABLE)
                .select("id")
                .eq("website_url", source_url)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
                .data
                or []
            )
            if existing:
                return existing[0]["id"]

        inferred_name = self._infer_name_from_source(source_url)
        if inferred_name:
            existing = (
                self.supabase.table(_TABLE)
                .select("id")
                .eq("name", inferred_name)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
                .data
                or []
            )
            if existing:
                return existing[0]["id"]

        row_id = str(uuid4())
        create_payload: dict[str, Any] = {
            "id": row_id,
            "name": inferred_name,
            "website_url": source_url,
            "created_at": now,
            "updated_at": now,
        }
        self.supabase.table(_TABLE).insert(create_payload).execute()
        return row_id

    def _infer_name_from_source(self, source_url: str | None) -> str | None:
        if not source_url:
            return None

        rows = (
            self.supabase.table(_SCRAPE_SOURCES_TABLE)
            .select("*")
            .eq("resource_type", _RESOURCE_TYPE)
            .eq("url", source_url)
            .limit(1)
            .execute()
            .data or []
        )
        if not rows:
            return None
        return rows[0].get("label")

    def _materialize_approved_changes_without_resource(self) -> None:
        rows = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .select("*")
            .eq("resource_type", _RESOURCE_TYPE)
            .eq("status", "approved")
            .eq("resource_id", None)
            .order("created_at", desc=False)
            .execute()
            .data
            or []
        )
        if not rows:
            return

        for change in rows:
            field = change.get("field")
            detected_value = change.get("detected_value")
            if not field or detected_value is None:
                continue

            now = datetime.now(timezone.utc).isoformat()
            resource_id = self._get_or_create_resource_id_for_change(change, now)
            db_field = _CAMEL_TO_SNAKE.get(field, field)
            self.supabase.table(_TABLE).update(
                {db_field: detected_value, "updated_at": now}
            ).eq("id", resource_id).execute()
            self.supabase.table(_PENDING_CHANGES_TABLE).update({"resource_id": resource_id}).eq(
                "id", change["id"]
            ).execute()

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
