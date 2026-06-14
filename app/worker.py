"""RQ worker entrypoint for durable background jobs (report generation).

Run alongside the web process:

    python -m app.worker

(or, equivalently, ``rq worker -u "$REDIS_URL" reports`` once the app package
is importable on the worker's ``PYTHONPATH``).

The worker must import the application package so that:
  * job functions, enqueued by dotted path, can be resolved, and
  * ``Settings`` / DB engines initialise from the *same* environment as the
    web process (so the worker writes to the same database).

Requires ``REDIS_URL`` to point at the same Redis the API enqueues to, and
``REPORT_QUEUE_ENABLED=true`` on the API so jobs are actually queued.
"""
from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.queue import REPORTS_QUEUE_NAME, get_sync_redis

logger = logging.getLogger(__name__)


def _init_sentry(settings) -> None:
    """Initialise Sentry for the worker. No-op when SENTRY_DSN is unset or the SDK
    isn't installed — report-generation failures here are exactly what we want
    visibility into, separately from the web process."""
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; skipping init.")
        return
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT or settings.APP_ENV,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
    )


def main() -> None:
    from rq import Worker

    from app.core.logging_config import configure_logging

    settings = get_settings()
    configure_logging(settings)
    _init_sentry(settings)
    connection = get_sync_redis(settings)
    worker = Worker([REPORTS_QUEUE_NAME], connection=connection)
    logger.info("Starting RQ worker — queues=%s redis=%s", REPORTS_QUEUE_NAME, settings.REDIS_URL)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
