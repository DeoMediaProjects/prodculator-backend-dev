from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.modules.data_sources.test_connections import (
    test_anthropic,
    test_bls,
    test_database,
    test_exchange_rate,
    test_not_implemented,
    test_redis,
    test_sendgrid,
    test_stripe,
    test_tmdb,
)

logger = logging.getLogger(__name__)

_SLUG_TO_SETTING: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "database": "DB_URL",
    "tmdb": "TMDB_API_KEY",
    "bls": "BLS_API_KEY",
    "stripe": "STRIPE_SECRET_KEY",
    "sendgrid": "SENDGRID_API_KEY",
    "redis": "REDIS_URL",
    "google_maps": "GOOGLE_MAPS_API_KEY",
    "exchange_rate": "EXCHANGE_RATE_API_KEY",
    "grantify": "GRANTIFY_API_KEY",
}


class DataSourceService:
    def __init__(self, db: DatabaseClient, settings: Settings):
        self.db = db
        self.settings = settings

    def _is_credential_set(self, slug: str) -> bool:
        attr = _SLUG_TO_SETTING.get(slug)
        if not attr:
            return False
        value = getattr(self.settings, attr, "")
        return bool(value)

    def _enrich(self, row: dict[str, Any]) -> dict[str, Any]:
        row["credential_configured"] = self._is_credential_set(row.get("slug", ""))
        for dt_field in ("last_tested_at", "created_at", "updated_at"):
            val = row.get(dt_field)
            if val is not None and not isinstance(val, str):
                row[dt_field] = val.isoformat() if hasattr(val, "isoformat") else str(val)
            elif val is None:
                row[dt_field] = None
        return row

    def list_sources(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        count_result = (
            self.db.table("data_sources")
            .select("*", count="exact", head=True)
            .execute()
        )
        total = count_result.count or 0

        rows_result = (
            self.db.table("data_sources")
            .select("*")
            .order("name")
            .range(offset, offset + limit - 1)
            .execute()
        )
        items = [self._enrich(row) for row in (rows_result.data or [])]
        return items, total

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        result = (
            self.db.table("data_sources")
            .select("*")
            .eq("id", source_id)
            .single()
            .execute()
        )
        if not result.data:
            return None
        return self._enrich(result.data)

    def update_source(
        self, source_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        allowed = {}
        if "enabled" in payload:
            allowed["enabled"] = payload["enabled"]
        if "sync_schedule" in payload:
            allowed["sync_schedule"] = payload["sync_schedule"]
        allowed["updated_at"] = datetime.now(timezone.utc).isoformat()

        result = (
            self.db.table("data_sources")
            .update(allowed)
            .eq("id", source_id)
            .select("*")
            .single()
            .execute()
        )
        return self._enrich(result.data)

    def test_connection(self, source_id: str) -> dict[str, Any]:
        source = self.get_source(source_id)
        if not source:
            raise ValueError("Data source not found")

        slug = source["slug"]
        start = time.perf_counter()

        if slug == "anthropic":
            success, message = test_anthropic(self.settings)
        elif slug == "database":
            success, message = test_database(self.db)
        elif slug == "tmdb":
            success, message = test_tmdb(self.settings)
        elif slug == "bls":
            success, message = test_bls(self.settings)
        elif slug == "stripe":
            success, message = test_stripe(self.settings)
        elif slug == "sendgrid":
            success, message = test_sendgrid(self.settings)
        elif slug == "redis":
            success, message = test_redis(self.settings)
        elif slug == "exchange_rate":
            success, message = test_exchange_rate(self.settings)
        else:
            success, message = test_not_implemented(slug)

        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        now = datetime.now(timezone.utc)
        status = "connected" if success else "disconnected"

        update_payload: dict[str, Any] = {
            "status": status,
            "last_tested_at": now.isoformat(),
            "last_test_result": "success" if success else "failure",
            "last_test_message": message,
            "updated_at": now.isoformat(),
        }
        self.db.table("data_sources").update(update_payload).eq(
            "id", source_id
        ).execute()

        return {
            "slug": slug,
            "status": status,
            "latency_ms": latency_ms,
            "message": message,
            "tested_at": now.isoformat(),
        }

    def bulk_configure(
        self, items: list[dict[str, Any]]
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        updated = 0
        for item in items:
            self.db.table("data_sources").update(
                {"enabled": item["enabled"], "updated_at": now}
            ).eq("id", item["id"]).execute()
            updated += 1
        return updated

    def get_sync_schedule(self) -> list[dict[str, Any]]:
        result = (
            self.db.table("data_sources")
            .select("slug, name, sync_schedule, last_tested_at, enabled")
            .order("name")
            .execute()
        )
        items = []
        for row in result.data or []:
            val = row.get("last_tested_at")
            if val is not None and not isinstance(val, str):
                row["last_tested_at"] = val.isoformat() if hasattr(val, "isoformat") else str(val)
            items.append(row)
        return items
