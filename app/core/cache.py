from __future__ import annotations

import redis.asyncio as redis

from app.core.config import Settings, get_settings

_redis_pool: redis.Redis | None = None

# Fail-fast connection settings. Redis here is a best-effort cache / token
# blocklist — every caller degrades gracefully when it's unavailable (see
# dependencies.get_current_user). Without short timeouts an unreachable or
# slow Redis adds ~4s to EVERY authenticated request (two touchpoints each)
# before the graceful-degradation path is reached. Short timeouts + no retry
# keep auth responsive whether Redis is down locally or blips in production.
_REDIS_KWARGS: dict = {
    "decode_responses": True,
    "socket_connect_timeout": 0.5,
    "socket_timeout": 0.5,
    "retry_on_timeout": False,
    "health_check_interval": 0,
}


def init_redis(settings: Settings | None = None) -> None:
    global _redis_pool
    cfg = settings or get_settings()
    _redis_pool = redis.from_url(cfg.REDIS_URL, **_REDIS_KWARGS)


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
    return redis.from_url(cfg.REDIS_URL, **_REDIS_KWARGS)
