import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from app.core.database_client import DatabaseClient
from app.core.security import hash_password
from app.models.enums import AdminRole

TABLE = "admins"

VALID_ROLES = {r.value for r in AdminRole}


def _strip_hash(row: dict[str, Any]) -> dict[str, Any]:
    """Remove password_hash from a row dict before returning."""
    result = dict(row)
    result.pop("password_hash", None)
    for field in ("created_at", "last_login"):
        val = result.get(field)
        if val is not None and not isinstance(val, str):
            result[field] = val.isoformat() if hasattr(val, "isoformat") else str(val)
    return result


class AdminUsersService:
    def __init__(self, db: DatabaseClient):
        self.db = db

    def list_admins(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        count_result = (
            self.db.table(TABLE).select("*", count="exact", head=True).execute()
        )
        total = count_result.count or 0

        rows_result = (
            self.db.table(TABLE)
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        items = [_strip_hash(r) for r in (rows_result.data or [])]
        return items, total

    def get_admin(self, admin_id: str) -> dict[str, Any] | None:
        result = (
            self.db.table(TABLE)
            .select("*")
            .eq("id", admin_id)
            .single()
            .execute()
        )
        if not result.data:
            return None
        return _strip_hash(result.data)

    def create_admin(
        self, *, email: str, name: str | None, role: str
    ) -> tuple[dict[str, Any], str]:
        if role not in VALID_ROLES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_ROLES))}",
            )

        temp_password = secrets.token_urlsafe(16)
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "id": str(uuid4()),
            "email": email.strip().lower(),
            "password_hash": hash_password(temp_password),
            "name": name,
            "role": role,
            "created_at": now,
        }

        try:
            result = (
                self.db.table(TABLE)
                .insert(payload)
                .select("*")
                .single()
                .execute()
            )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise HTTPException(
                    status_code=409,
                    detail="An admin with this email already exists",
                )
            raise

        return _strip_hash(result.data), temp_password

    def update_admin(
        self,
        admin_id: str,
        payload: dict[str, Any],
        current_admin_id: str,
    ) -> dict[str, Any]:
        if "role" in payload and payload["role"] is not None:
            if admin_id == current_admin_id:
                raise HTTPException(
                    status_code=400,
                    detail="You cannot change your own role",
                )
            if payload["role"] not in VALID_ROLES:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_ROLES))}",
                )

        updates: dict[str, Any] = {}
        if "name" in payload and payload["name"] is not None:
            updates["name"] = payload["name"]
        if "email" in payload and payload["email"] is not None:
            updates["email"] = payload["email"].strip().lower()
        if "role" in payload and payload["role"] is not None:
            updates["role"] = payload["role"]
        if "password" in payload and payload["password"] is not None:
            updates["password_hash"] = hash_password(payload["password"])

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        try:
            result = (
                self.db.table(TABLE)
                .update(updates)
                .eq("id", admin_id)
                .select("*")
                .single()
                .execute()
            )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise HTTPException(
                    status_code=409,
                    detail="An admin with this email already exists",
                )
            raise

        if not result.data:
            raise HTTPException(status_code=404, detail="Admin not found")

        return _strip_hash(result.data)

    def delete_admin(self, admin_id: str, current_admin_id: str) -> None:
        if admin_id == current_admin_id:
            raise HTTPException(
                status_code=400, detail="You cannot delete your own account"
            )

        existing = (
            self.db.table(TABLE)
            .select("id")
            .eq("id", admin_id)
            .single()
            .execute()
        )
        if not existing.data:
            raise HTTPException(status_code=404, detail="Admin not found")

        self.db.table(TABLE).delete().eq("id", admin_id).execute()
