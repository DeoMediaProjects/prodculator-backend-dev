import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.modules.scraper.scrapers import crew_costs, festivals, grants, incentives
from app.modules.scraper.sources import DEFAULT_SOURCES

logger = logging.getLogger(__name__)

_SCRAPERS = {
    "incentives": incentives.run,
    "crew_costs": crew_costs.run,
    "grants": grants.run,
    "festivals": festivals.run,
}

_DEPRECATED_SOURCE_URLS = {
    "https://www.bectu.org.uk/rates",
    "https://bectu.org.uk/rates",
    "https://nofilmschool.com/film-grants",
    "https://www.bfi.org.uk/get-funding-and-support",
    "https://www.sundance.org/festivals/sundance-film-festival",
    # Removed in favour of better sources
    "https://www.wrapbook.com/blog/film-industry-tax-incentives",
    "https://www.georgia.org/industries/film-entertainment/georgia-film-tv-production",
    "https://nofilmschool.com/topics/grants-contests-awards",
    "https://www.labiennale.org/en/cinema",
    # Commonly blocked/non-scrapeable grant pages
    "https://cmf-fmc.ca/our-programs/",
    "https://nfi.hu/en/national-film-institute/funding",
}


def _is_expected_source_failure(exc: Exception) -> bool:
    message = str(exc).lower()
    expected_patterns = (
        "no text returned for",
        "blocked by robots.txt",
        "403 forbidden",
        "forbidden",
    )
    return any(pattern in message for pattern in expected_patterns)


class ScraperService:
    def __init__(self, db: DatabaseClient, settings: Settings):
        self.db = db
        self.settings = settings

    def seed_sources(self) -> None:
        """Seed missing default sources and refresh known defaults by label."""
        existing_rows = self.db.table("scrape_sources").select("*").execute().data or []
        existing_by_key = {
            (row.get("resource_type"), row.get("label")): row for row in existing_rows
        }
        now = datetime.now(timezone.utc).isoformat()
        for source in DEFAULT_SOURCES:
            key = (source["resource_type"], source["label"])
            existing = existing_by_key.get(key)
            if existing:
                self.db.table("scrape_sources").update({
                    "url": source["url"],
                    "territory": source["territory"],
                    "use_bls_api": source.get("use_bls_api", False),
                    "is_pdf": source.get("is_pdf", False),
                    "updated_at": now,
                }).eq("id", existing["id"]).execute()
                continue

            self.db.table("scrape_sources").insert({
                "id": str(uuid4()),
                **source,
                "enabled": True,
                "last_scraped_at": None,
                "last_status": None,
                "last_error": None,
                "created_at": now,
                "updated_at": now,
            }).execute()

        # Disable known dead legacy sources so they stop failing sync runs.
        for row in existing_rows:
            if row.get("url") in _DEPRECATED_SOURCE_URLS and row.get("enabled", True):
                self.db.table("scrape_sources").update({
                    "enabled": False,
                    "last_status": "error",
                    "last_error": "Disabled deprecated source URL",
                    "updated_at": now,
                }).eq("id", row["id"]).execute()
                logger.info("Disabled deprecated scrape source: %s", row.get("url"))

        logger.info("Scrape source defaults synchronized (%d defaults)", len(DEFAULT_SOURCES))

    def run_all(self, triggered_by: str = "scheduler") -> dict[str, Any]:
        """Run all enabled sources."""
        return self._run(resource_type=None, triggered_by=triggered_by)

    def run_for_resource(self, resource_type: str, triggered_by: str = "admin") -> dict[str, Any]:
        """Run only sources for a specific resource type."""
        return self._run(resource_type=resource_type, triggered_by=triggered_by)

    def _run(self, resource_type: str | None, triggered_by: str) -> dict[str, Any]:
        if not self.settings.SCRAPER_ENABLED:
            logger.info("Scraper disabled via SCRAPER_ENABLED=False — skipping")
            return {"status": "skipped", "reason": "SCRAPER_ENABLED=False"}

        # Log the run start
        run_id = str(uuid4())
        started_at = datetime.now(timezone.utc)
        self.db.table("scrape_runs").insert({
            "id": run_id,
            "triggered_by": triggered_by,
            "resource_type": resource_type,
            "started_at": started_at.isoformat(),
            "status": "running",
        }).execute()

        # Fetch enabled sources
        query = self.db.table("scrape_sources").select("*").eq("enabled", True)
        if resource_type:
            query = query.eq("resource_type", resource_type)
        sources = query.execute().data or []

        pages_scraped = 0
        changes_detected = 0
        errors = 0

        for source in sources:
            scraper_fn = _SCRAPERS.get(source["resource_type"])
            if not scraper_fn:
                continue
            try:
                n = scraper_fn(source, self.db, self.settings)
                changes_detected += n
                pages_scraped += 1
                self._update_source_status(source["id"], "success")
            except Exception as exc:
                errors += 1
                if _is_expected_source_failure(exc):
                    logger.warning("Scraper skipped source %s: %s", source.get("url"), exc)
                else:
                    logger.exception("Scraper failed for source %s: %s", source.get("url"), exc)
                self._update_source_status(source["id"], "error", str(exc))

        finished_at = datetime.now(timezone.utc)
        final_status = "error" if errors > 0 and pages_scraped == 0 else "success"

        self.db.table("scrape_runs").update({
            "status": final_status,
            "finished_at": finished_at.isoformat(),
            "pages_scraped": pages_scraped,
            "changes_detected": changes_detected,
            "error_message": f"{errors} source(s) failed" if errors else None,
        }).eq("id", run_id).execute()

        # Update sync_settings.last_sync_at for affected resource types
        rt_list = [resource_type] if resource_type else list(_SCRAPERS.keys())
        for rt in rt_list:
            self._update_sync_settings_last_sync(rt, finished_at.isoformat())

        summary = {
            "runId": run_id,
            "status": final_status,
            "triggeredBy": triggered_by,
            "pagesScraped": pages_scraped,
            "changesDetected": changes_detected,
            "errors": errors,
            "startedAt": started_at.isoformat(),
            "finishedAt": finished_at.isoformat(),
        }
        logger.info("Scrape run complete: %s", summary)
        return summary

    def _update_source_status(
        self, source_id: str, status: str, error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "last_scraped_at": now,
            "last_status": status,
            "updated_at": now,
        }
        if error:
            payload["last_error"] = error[:500]
        self.db.table("scrape_sources").update(payload).eq("id", source_id).execute()

    def _update_sync_settings_last_sync(self, resource_type: str, ts: str) -> None:
        rows = (
            self.db.table("sync_settings")
            .select("*")
            .eq("resource_type", resource_type)
            .execute()
            .data or []
        )
        if rows:
            self.db.table("sync_settings").update(
                {"last_sync_at": ts, "updated_at": ts},
            ).eq("id", rows[0]["id"]).execute()
