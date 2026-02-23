from __future__ import annotations

import redis.asyncio as redis

from app.core.config import Settings, get_settings


def get_redis_client(settings: Settings | None = None) -> redis.Redis:
    cfg = settings or get_settings()
    return redis.from_url(cfg.REDIS_URL, decode_responses=True)
