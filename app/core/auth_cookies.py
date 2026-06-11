"""Cookie-based auth helpers.

Tokens are issued as **httpOnly** cookies so the browser stores them somewhere
JavaScript cannot read — eliminating the XSS token-theft surface that comes with
keeping JWTs in ``localStorage``. The ``Authorization: Bearer`` header is still
honoured everywhere (API clients, the test suite), so cookie auth is purely
additive.

Because cookies are sent automatically by the browser, cookie-authenticated
*state-changing* requests are vulnerable to CSRF. We defend with two layers:

1. ``SameSite`` on the auth cookies (``lax`` by default) so a cross-site form
   POST does not carry them.
2. A double-submit CSRF token: a readable ``pc_csrf_token`` cookie is issued
   alongside the auth cookies; the frontend echoes its value in the
   ``X-CSRF-Token`` header, and the API requires the two to match for unsafe
   methods (enforced in ``app.main``). An attacker on another origin can cause
   the cookie to be sent but cannot read it to set the matching header.
"""
from __future__ import annotations

import secrets

from fastapi import Request, Response

from app.core.config import Settings

# Prefixed so they never collide with other cookies on a shared domain.
ACCESS_COOKIE = "pc_access_token"
REFRESH_COOKIE = "pc_refresh_token"
CSRF_COOKIE = "pc_csrf_token"
CSRF_HEADER = "X-CSRF-Token"


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _normalise_samesite(value: str) -> str:
    candidate = (value or "lax").strip().lower()
    return candidate if candidate in {"lax", "strict", "none"} else "lax"


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    settings: Settings,
    access_max_age: int | None = None,
    refresh_max_age: int | None = None,
) -> str:
    """Attach the access/refresh (httpOnly) and CSRF (readable) cookies.

    Returns the freshly minted CSRF token so callers may also surface it in the
    response body if they wish. A no-op (returns "") when cookie auth is disabled.
    """
    if not settings.AUTH_COOKIE_ENABLED:
        return ""

    samesite = _normalise_samesite(settings.AUTH_COOKIE_SAMESITE)
    # SameSite=None is only valid on Secure cookies; browsers drop it otherwise.
    secure = settings.AUTH_COOKIE_SECURE or samesite == "none"
    domain = settings.AUTH_COOKIE_DOMAIN or None
    access_age = access_max_age if access_max_age is not None else settings.JWT_ACCESS_TOKEN_EXPIRES_SECONDS
    refresh_age = refresh_max_age if refresh_max_age is not None else settings.JWT_REFRESH_TOKEN_EXPIRES_SECONDS

    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=access_age,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=refresh_age,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    csrf_token = generate_csrf_token()
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=refresh_age,
        httponly=False,  # the SPA must read this to echo it back in a header
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    return csrf_token


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Delete all auth cookies (sign-out)."""
    domain = settings.AUTH_COOKIE_DOMAIN or None
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, domain=domain, path="/")


def extract_access_token(request: Request, header_token: str | None) -> str | None:
    """Resolve the access token: explicit Bearer header wins, else the cookie."""
    if header_token:
        return header_token
    return request.cookies.get(ACCESS_COOKIE)


def extract_refresh_token(request: Request, body_token: str | None) -> str | None:
    """Resolve the refresh token: explicit request body wins, else the cookie."""
    if body_token:
        return body_token
    return request.cookies.get(REFRESH_COOKIE)
