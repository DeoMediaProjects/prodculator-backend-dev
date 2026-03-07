import time

from fastapi import HTTPException

_last_test: dict[str, float] = {}
COOLDOWN_SECONDS = 30


def check_test_rate_limit(slug: str) -> None:
    now = time.time()
    last = _last_test.get(slug, 0.0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        raise HTTPException(
            status_code=429,
            detail=f"Test for '{slug}' on cooldown. Retry in {remaining}s.",
        )
    _last_test[slug] = now
