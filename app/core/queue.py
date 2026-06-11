"""Durable background-job queue (RQ over Redis).

Report generation is a long-running, failure-prone pipeline (script analysis,
production analysis, PDF render/upload, transactional emails). Running it in
FastAPI ``BackgroundTasks`` ties the job to the web process: a deploy, crash,
or restart mid-generation silently loses the work and leaves the report row
stuck in ``processing`` forever.

This module routes that work onto a Redis-backed RQ queue so a *separate*
worker process owns it. The web API only creates jobs and polls status (the
report row's ``status`` column is the durable source of truth for progress).

Behaviour is controlled by ``Settings.REPORT_QUEUE_ENABLED``:

* enabled  -> jobs are enqueued to RQ; a worker (``python -m app.worker``)
             picks them up. Survives web-process restarts.
* disabled -> callers fall back to in-process ``BackgroundTasks`` (the legacy
             behaviour). Fine for local dev / a single web process and for
             the test suite, which never needs a running worker or Redis.

``rq`` is imported lazily so the application imports cleanly even when the
queue is disabled and the package is not installed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis import Redis
    from rq import Queue

logger = logging.getLogger(__name__)

# Single named queue for report generation. Kept as a module constant so the
# worker entrypoint and the enqueue path can never drift apart.
REPORTS_QUEUE_NAME = "reports"


def get_sync_redis(settings: Settings | None = None) -> "Redis":
    """Return a *synchronous* Redis client.

    RQ does not support ``redis.asyncio``; it needs the blocking client. This is
    intentionally separate from ``app.core.cache`` (which holds the async client
    used inside request handlers).
    """
    import redis

    cfg = settings or get_settings()
    return redis.from_url(cfg.REDIS_URL)


def get_report_queue(settings: Settings | None = None) -> "Queue | None":
    """Return the RQ queue for report jobs, or ``None`` when queueing is disabled.

    A ``None`` return is the signal to callers that they should fall back to
    in-process execution (FastAPI ``BackgroundTasks``).
    """
    cfg = settings or get_settings()
    if not cfg.REPORT_QUEUE_ENABLED:
        return None

    from rq import Queue

    return Queue(
        REPORTS_QUEUE_NAME,
        connection=get_sync_redis(cfg),
        default_timeout=cfg.REPORT_QUEUE_JOB_TIMEOUT,
    )
