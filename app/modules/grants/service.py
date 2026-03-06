import csv
import io
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient

_TABLE = "grant_opportunities"
_SYNC_SETTINGS_TABLE = "sync_settings"
_PENDING_CHANGES_TABLE = "pending_changes"
_RESOURCE_TYPE = "grants"

# ── Grant field maps (camelCase ↔ snake_case) ─────────────────────────────────

_CAMEL_TO_SNAKE: dict[str, str] = {
    "fundingBody": "funding_body",
    "maxAmount": "max_amount",
    "applicationOpens": "application_opens",
    "applicationDeadline": "application_deadline",
    "daysUntilDeadline": "days_until_deadline",
    "websiteUrl": "website_url",
    "dataSource": "data_source",
    "isNew": "is_new",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "lastVerifiedAt": "last_verified_at",
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


def _grant_to_db(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in payload.items():
        result[_CAMEL_TO_SNAKE.get(k, k)] = v
    if result.get("id") == "":
        result.pop("id")
    return result


def _grant_from_db(row: dict[str, Any]) -> dict[str, Any]:
    return {_SNAKE_TO_CAMEL.get(k, k): v for k, v in row.items()}


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


class GrantsService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    # ── Public endpoint ────────────────────────────────────────────────────────

    def get_grants(self, territory: str | None = None) -> list[dict]:
        query = self.supabase.table(_TABLE).select("*")
        if territory:
            query = query.eq("territory", territory)
        result = query.order("application_deadline", desc=False).execute()
        return [_grant_from_db(r) for r in (result.data or [])]

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
        return [_grant_from_db(r) for r in rows], count

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        db_payload = _grant_to_db(payload)
        db_payload.setdefault("id", str(uuid4()))
        db_payload["created_at"] = now
        db_payload["updated_at"] = now
        result = self.supabase.table(_TABLE).insert(db_payload).select("*").single().execute()
        return _grant_from_db(result.data)

    def update(self, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db_payload = _grant_to_db(payload)
        db_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = (
            self.supabase.table(_TABLE)
            .update(db_payload)
            .eq("id", row_id)
            .select("*")
            .single()
            .execute()
        )
        return _grant_from_db(result.data)

    def delete(self, row_id: str) -> None:
        self.supabase.table(_TABLE).delete().eq("id", row_id).execute()

    def bulk_import(self, csv_content: str) -> dict[str, Any]:
        reader = csv.DictReader(io.StringIO(csv_content))
        imported = 0
        failed = 0
        errors: list[dict[str, Any]] = []

        for row_num, row in enumerate(reader, start=2):  # row 1 = headers
            if not (row.get("title") or "").strip():
                failed += 1
                errors.append({"row": row_num, "reason": "Missing title"})
                continue
            try:
                eligibility_raw = row.get("eligibility", "")
                eligibility = [e.strip() for e in eligibility_raw.split(";") if e.strip()]

                verified_raw = (row.get("verified") or "false").strip().lower()
                verified = verified_raw in ("true", "1", "yes")

                now = datetime.now(timezone.utc).isoformat()
                db_payload: dict[str, Any] = {
                    "id": str(uuid4()),
                    "title": row.get("title", "").strip(),
                    "territory": row.get("territory", "").strip() or None,
                    "funding_body": row.get("fundingBody") or row.get("funding_body") or None,
                    "max_amount": row.get("maxAmount") or row.get("max_amount") or None,
                    "currency": row.get("currency") or None,
                    "application_opens": row.get("applicationOpens") or row.get("application_opens") or None,
                    "application_deadline": row.get("applicationDeadline") or row.get("application_deadline") or None,
                    "eligibility": eligibility or None,
                    "website_url": row.get("websiteUrl") or row.get("website_url") or None,
                    "verified": verified,
                    "data_source": "manual",
                    "is_new": True,
                    "created_at": now,
                    "updated_at": now,
                }
                # Drop None values so DB defaults apply
                db_payload = {k: v for k, v in db_payload.items() if v is not None}
                self.supabase.table(_TABLE).insert(db_payload).execute()
                imported += 1
            except Exception as exc:
                failed += 1
                errors.append({"row": row_num, "reason": str(exc)})

        return {"imported": imported, "failed": failed, "errors": errors}

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

    # ── Sync trigger ──────────────────────────────────────────────────────────

    def trigger_sync(self) -> dict[str, Any]:
        from app.core.config import get_settings
        from app.modules.scraper.service import ScraperService

        settings = get_settings()
        scraper = ScraperService(self.supabase, settings)
        return scraper.run_for_resource("grants", triggered_by="admin")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_or_create_resource_id_for_change(self, change: dict[str, Any], now: str) -> str:
        territory = change.get("territory")
        source_url = change.get("source")

        query = self.supabase.table(_TABLE).select("id").order("created_at", desc=True).limit(1)
        if territory:
            query = query.eq("territory", territory)
        if source_url:
            query = query.eq("website_url", source_url)
        existing = query.execute().data or []
        if existing:
            return existing[0]["id"]

        row_id = str(uuid4())
        create_payload: dict[str, Any] = {
            "id": row_id,
            "territory": territory,
            "website_url": source_url,
            "created_at": now,
            "updated_at": now,
        }
        self.supabase.table(_TABLE).insert(create_payload).execute()
        return row_id

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
