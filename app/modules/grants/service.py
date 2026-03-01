import csv
import io
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient

_TABLE = "grant_opportunities"

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


def _grant_to_db(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in payload.items():
        result[_CAMEL_TO_SNAKE.get(k, k)] = v
    if result.get("id") == "":
        result.pop("id")
    return result


def _grant_from_db(row: dict[str, Any]) -> dict[str, Any]:
    return {_SNAKE_TO_CAMEL.get(k, k): v for k, v in row.items()}


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
