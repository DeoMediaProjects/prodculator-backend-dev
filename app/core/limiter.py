from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

_settings = get_settings()

# Redis-backed so rate-limit counters are shared across all web workers and
# survive process restarts. `swallow_errors=True` makes the limiter fail OPEN if
# Redis is briefly unreachable — consistent with the rest of the app's graceful
# Redis degradation, so a cache blip never 500s a request. `socket_connect_timeout`
# keeps that failure fast rather than hanging the request.
#
# NOTE: `get_remote_address` returns `request.client.host`, which only reflects
# the true client IP when the ASGI server is told to trust the proxy. Run uvicorn
# with `--proxy-headers --forwarded-allow-ips="*"` (see Dockerfile / Makefile /
# the systemd unit) so per-client limits aren't collapsed into the proxy's IP.
limiter = Limiter(
    key_func=get_remote_address,
    enabled=_settings.RATE_LIMIT_ENABLED,
    storage_uri=_settings.rate_limit_storage_uri,
    storage_options={"socket_connect_timeout": 2},
    strategy="fixed-window",
    swallow_errors=True,
)
