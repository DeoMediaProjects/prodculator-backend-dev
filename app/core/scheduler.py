import logging
from datetime import datetime, timezone

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.alerts import ALERT_SCHEDULER_JOB, send_admin_alert

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# Shared by every worker process. The one that wins pg_try_advisory_lock owns the
# scheduler; the rest skip it, so jobs run exactly once regardless of worker count.
_SCHEDULER_LOCK_KEY = 4_021_966_811
_lock_conn = None


def _acquire_singleton_lock() -> bool:
    """Become the single scheduler owner across all worker processes (and hosts).

    Holds a Postgres session-level advisory lock for the lifetime of a dedicated
    connection — only one connection cluster-wide can hold it. No-ops (returns
    True) on non-Postgres databases (local/dev/test), where there's a single
    process anyway. Returns False on any error so we fail closed: a missed
    scheduler run is safer than concurrent duplicate dunning/reconciler runs.
    """
    global _lock_conn
    from sqlalchemy import text

    from app.core.db import engine

    if engine.dialect.name != "postgresql":
        return True

    try:
        conn = engine.connect()
        acquired = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _SCHEDULER_LOCK_KEY}
        ).scalar()
        if acquired:
            _lock_conn = conn  # keep open — closing it would release the lock
            return True
        conn.close()
        return False
    except Exception:
        logger.exception("Scheduler: advisory-lock acquisition failed — not starting")
        return False


def _release_singleton_lock() -> None:
    global _lock_conn
    if _lock_conn is None:
        return
    from sqlalchemy import text

    try:
        _lock_conn.execute(
            text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEDULER_LOCK_KEY}
        )
    except Exception:
        logger.warning("Scheduler: advisory-lock release failed", exc_info=True)
    finally:
        try:
            _lock_conn.close()
        except Exception:
            logger.debug("Scheduler: lock connection close failed", exc_info=True)
        _lock_conn = None

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
    finally:
        db.close()


def _run_subscription_dunning() -> None:
    """Hourly: downgrade users past the dunning grace window."""
    from app.core.config import get_settings
    from app.core.database_client import create_client
    from app.modules.payments.dunning import run_dunning_grace_check

    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        return

    db = create_client()
    try:
        run_dunning_grace_check(db, settings)
    finally:
        db.close()


def _run_subscription_reconciler() -> None:
    """Hourly: detect and fix drift between local subscriptions and Stripe."""
    from app.core.config import get_settings
    from app.core.database_client import create_client
    from app.modules.payments.reconciler import run_subscription_reconciler as _run

    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        return

    db = create_client()
    try:
        _run(db, settings)
    finally:
        db.close()


def _run_b2b_monthly_aggregate_close() -> None:
    """Daily: store the closed month's raw aggregate for each B2B product.

    Runs ahead of the delivery job so quarterly/yearly reports can compose from
    stored months rather than re-querying the signal pool.
    """
    from app.core.config import get_settings
    from app.modules.b2b.service import run_b2b_monthly_aggregate_close

    settings = get_settings()
    stored = run_b2b_monthly_aggregate_close(settings)
    if stored:
        logger.info("Scheduler: stored %d B2B monthly aggregate(s)", stored)


def _run_b2b_auto_delivery() -> None:
    """Daily: generate due B2B monthly/quarterly intelligence deliveries."""
    from app.core.config import get_settings
    from app.modules.b2b.service import run_due_b2b_auto_deliveries

    settings = get_settings()
    generated = run_due_b2b_auto_deliveries(settings)
    if generated:
        logger.info("Scheduler: generated %d due B2B intelligence report(s)", generated)


def _run_admin_audit_retention() -> None:
    """Daily: drop admin audit rows past the configured retention window.

    The only path that removes an audit row. Deliberately not reachable over
    HTTP: an admin must not be able to shorten the record of their own actions.
    """
    from app.core.audit import purge_expired_audit_logs
    from app.core.config import get_settings

    retention_days = get_settings().ADMIN_AUDIT_RETENTION_DAYS
    if retention_days <= 0:
        logger.debug("Scheduler: audit retention disabled — retaining indefinitely")
        return
    purge_expired_audit_logs(retention_days)


def _on_job_event(event) -> None:
    """Alert on any scheduled job that fails or is missed (handoff §4.5).

    Registered once against APScheduler's own event bus rather than added to
    each job's body. That is what makes it unforgettable: a job added later is
    covered without anyone remembering to wrap it, and the individual jobs are
    free to let their exceptions propagate instead of swallowing them (which is
    what previously left every scheduled failure invisible outside the logs).

    Throttled per job id, so a job failing hourly all weekend sends one email
    per window per job rather than one per run.
    """
    job_id = getattr(event, "job_id", "unknown")
    exception = getattr(event, "exception", None)

    if exception is None and event.code == EVENT_JOB_MISSED:
        logger.warning("Scheduler: job %s missed its run window", job_id)
        send_admin_alert(
            category=ALERT_SCHEDULER_JOB,
            severity="warning",
            heading=f"Scheduled job '{job_id}' missed its run window",
            message=(
                "The job did not start within its misfire grace time, so the "
                "work it does for that period was skipped. A single miss after "
                "a restart is usually benign; a repeating one is not."
            ),
            details={"Job": job_id, "Scheduled for": str(getattr(event, "scheduled_run_time", ""))},
            throttle_key=f"{ALERT_SCHEDULER_JOB}:missed:{job_id}",
        )
        return

    logger.exception(
        "Scheduler: job %s raised", job_id,
        exc_info=exception if isinstance(exception, BaseException) else None,
    )
    send_admin_alert(
        category=ALERT_SCHEDULER_JOB,
        heading=f"Scheduled job '{job_id}' failed",
        message=(
            "The job raised and its work for this run did not complete. "
            "APScheduler will try again at the next trigger time, so a "
            "transient failure resolves itself — a repeating one means the work "
            "is not being done at all."
        ),
        details={
            "Job": job_id,
            "Error": f"{type(exception).__name__}: {exception}"[:300] if exception else "unknown",
            "Scheduled for": str(getattr(event, "scheduled_run_time", "")),
        },
        throttle_key=f"{ALERT_SCHEDULER_JOB}:error:{job_id}",
    )


def start_scheduler() -> None:
    global _scheduler
    from app.core.config import get_settings

    settings = get_settings()
    _scheduler = BackgroundScheduler(timezone="UTC")
    # Attach before any job is added, so nothing can run unwatched.
    _scheduler.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    if settings.SCRAPER_ENABLED:
        # Daily check at 03:00 UTC — reads sync_settings to decide what to run
        _scheduler.add_job(
            _check_and_run_syncs,
            trigger=CronTrigger(hour=3, minute=0),
            id="sync_check",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    if settings.STRIPE_SECRET_KEY:
        # Hourly subscription jobs — Stripe-driven, only register if Stripe configured.
        _scheduler.add_job(
            _run_subscription_dunning,
            trigger=CronTrigger(minute=15),
            id="subscription_dunning",
            replace_existing=True,
            misfire_grace_time=900,
        )
        _scheduler.add_job(
            _run_subscription_reconciler,
            trigger=CronTrigger(minute=45),
            id="subscription_reconciler",
            replace_existing=True,
            misfire_grace_time=900,
        )

    # Always registered — B2B auto-delivery serves manual-contract subscriptions
    # too, so it must run even when Stripe and the scraper are disabled. This means
    # the scheduler always has at least one job (no "no jobs registered" early-out).
    # Store closed months first, so the delivery job below can compose from them.
    _scheduler.add_job(
        _run_b2b_monthly_aggregate_close,
        trigger=CronTrigger(hour=4, minute=0),
        id="b2b_monthly_aggregate_close",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.add_job(
        _run_b2b_auto_delivery,
        trigger=CronTrigger(hour=4, minute=30),
        id="b2b_auto_delivery",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    # Audit retention — always registered, since the audit trail is written
    # regardless of whether Stripe or the scraper are configured.
    _scheduler.add_job(
        _run_admin_audit_retention,
        trigger=CronTrigger(hour=5, minute=0),
        id="admin_audit_retention",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    if not settings.SCHEDULER_ENABLED:
        logger.info("APScheduler: SCHEDULER_ENABLED is false — not starting on this process")
        _scheduler = None
        return

    # Gate to a single worker: only the process that wins the advisory lock runs
    # the jobs. Without this, every Gunicorn/Uvicorn worker would start its own
    # scheduler and run dunning/reconciler N times concurrently.
    if not _acquire_singleton_lock():
        logger.info(
            "APScheduler: another worker owns the scheduler lock — not starting on this process"
        )
        _scheduler = None
        return

    _scheduler.start()
    logger.info(
        "APScheduler started with jobs: %s",
        ", ".join(job.id for job in _scheduler.get_jobs()),
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
    # Release the advisory lock so another worker can take over on next startup.
    _release_singleton_lock()
