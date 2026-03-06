import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# Maps sync_settings.schedule values to timedelta days
_SCHEDULE_DAYS = {
    "monthly": 30,
    "quarterly": 90,
    "biannual": 182,
    "annual": 365,
}


def _check_and_run_syncs() -> None:
    """Daily check: for each resource type with sync enabled,
    run the scraper if next_scheduled <= now."""
    from app.core.config import get_settings
    from app.core.database_client import create_client
    from app.modules.scraper.service import ScraperService

    settings = get_settings()
    if not settings.SCRAPER_ENABLED:
        return

    db = create_client()
    try:
        rows = db.table("sync_settings").select("*").eq("enabled", True).execute().data or []
        now = datetime.now(timezone.utc)

        for row in rows:
            next_scheduled = row.get("next_scheduled")
            resource_type = row.get("resource_type")

            if not resource_type:
                continue

            # If next_scheduled is not set, compute it from schedule
            if next_scheduled is None:
                schedule = row.get("schedule", "biannual")
                days = _SCHEDULE_DAYS.get(schedule, 182)
                from datetime import timedelta
                next_dt = now + timedelta(days=days)
                db.table("sync_settings").update({
                    "next_scheduled": next_dt.isoformat(),
                    "updated_at": now.isoformat(),
                }).eq("id", row["id"]).execute()
                logger.info(
                    "Scheduler: set next_scheduled for %s to %s",
                    resource_type, next_dt.isoformat(),
                )
                continue

            # Parse next_scheduled
            if isinstance(next_scheduled, str):
                try:
                    next_dt = datetime.fromisoformat(next_scheduled)
                except ValueError:
                    continue
            else:
                next_dt = next_scheduled

            # Make timezone-aware if needed
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)

            if next_dt > now:
                continue  # Not due yet

            # Due — run the scraper for this resource type
            logger.info("Scheduler: running sync for %s (was due %s)", resource_type, next_dt)
            scraper = ScraperService(db, settings)
            scraper.run_for_resource(resource_type, triggered_by="scheduler")

            # Compute next_scheduled based on the schedule
            schedule = row.get("schedule", "biannual")
            days = _SCHEDULE_DAYS.get(schedule, 182)
            from datetime import timedelta
            new_next = now + timedelta(days=days)
            db.table("sync_settings").update({
                "next_scheduled": new_next.isoformat(),
                "updated_at": now.isoformat(),
            }).eq("id", row["id"]).execute()
            logger.info(
                "Scheduler: next sync for %s scheduled at %s",
                resource_type, new_next.isoformat(),
            )
    except Exception:
        logger.exception("Scheduler: sync check failed")
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.SCRAPER_ENABLED:
        logger.info("SCRAPER_ENABLED=False — scheduler not started")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    # Daily check at 03:00 UTC — reads sync_settings to decide what to run
    _scheduler.add_job(
        _check_and_run_syncs,
        trigger=CronTrigger(hour=3, minute=0),
        id="sync_check",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info("APScheduler started: daily sync check at 03:00 UTC")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
