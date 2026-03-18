from __future__ import annotations

import redis.asyncio as redis

from app.core.config import Settings, get_settings

_redis_pool: redis.Redis | None = None


def init_redis(settings: Settings | None = None) -> None:
    global _redis_pool
    cfg = settings or get_settings()
    _redis_pool = redis.from_url(cfg.REDIS_URL, decode_responses=True)


async def close_redis() -> None:
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None


def get_redis() -> redis.Redis:
    if _redis_pool is None:
        raise RuntimeError("Redis not initialised — call init_redis() in lifespan")
    return _redis_pool


# Backwards-compatible factory kept for any callers outside the request lifecycle.
def get_redis_client(settings: Settings | None = None) -> redis.Redis:
    if _redis_pool is not None:
        return _redis_pool
    cfg = settings or get_settings()
    return redis.from_url(cfg.REDIS_URL, decode_responses=True)
