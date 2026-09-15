from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

_settings = get_settings()

# Redis-backed so rate-limit counters are shared across all web workers and
# survive process restarts. Development fails open if Redis is unavailable;
# production fails closed so disrupting Redis cannot disable abuse controls.
# `socket_connect_timeout` keeps that failure fast rather than hanging requests.
#
# NOTE: `get_remote_address` returns `request.client.host`, which only reflects
# the true client IP when the ASGI server trusts the connecting proxy. Configure
# UVICORN_FORWARDED_ALLOW_IPS with the exact proxy CIDR(s); never use "*" on an
# origin that can also receive direct traffic.
limiter = Limiter(
    key_func=get_remote_address,
    enabled=_settings.RATE_LIMIT_ENABLED,
    storage_uri=_settings.rate_limit_storage_uri,
    storage_options={"socket_connect_timeout": 2},
    strategy="fixed-window",
    # Availability-biased in development, security-biased in production.
    swallow_errors=not _settings.is_production,
)
