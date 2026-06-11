"""Tests for cookie-based auth issuance and the double-submit CSRF guard."""
from app.core.auth_cookies import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER, REFRESH_COOKIE
from app.modules.auth.router import get_auth_service
from app.modules.auth.schemas import AuthUser, TokenResponse


class CookieAuthService:
    """Minimal auth-service double whose sign_out is async + redis-aware,
    matching what the real signout handler calls."""

    def sign_in(self, **kwargs):
        return TokenResponse(
            access_token="access",
            refresh_token="refresh",
            expires_in=3600,
            user=AuthUser(id="user-1", email="user@example.com"),
        )

    async def sign_out(self, token, redis_client=None):
        return None


def _set_cookie_lines(response):
    return response.headers.get_list("set-cookie")


def test_signin_sets_httponly_auth_cookies(client):
    client.app.dependency_overrides[get_auth_service] = lambda: CookieAuthService()
    response = client.post(
        "/api/auth/signin",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert response.status_code == 200

    lines = _set_cookie_lines(response)
    by_name = {line.split("=", 1)[0]: line for line in lines}
    assert ACCESS_COOKIE in by_name
    assert REFRESH_COOKIE in by_name
    assert CSRF_COOKIE in by_name

    # Access/refresh are httpOnly (JS cannot read them); the CSRF cookie is
    # deliberately readable so the SPA can echo it back in a header.
    assert "HttpOnly" in by_name[ACCESS_COOKIE]
    assert "HttpOnly" in by_name[REFRESH_COOKIE]
    assert "HttpOnly" not in by_name[CSRF_COOKIE]

    # Body still carries the tokens (kept during the transition / for API clients).
    assert response.json()["access_token"] == "access"


def test_csrf_blocks_cookie_request_without_header(client):
    # A cookie-authenticated, state-changing request with no X-CSRF-Token is
    # rejected by the middleware before it ever reaches the handler.
    client.cookies.set(ACCESS_COOKIE, "x")
    client.cookies.set(CSRF_COOKIE, "tok")
    response = client.post("/api/auth/signout")
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token missing or invalid"


def test_csrf_allows_cookie_request_with_matching_header(client):
    client.app.dependency_overrides[get_auth_service] = lambda: CookieAuthService()
    client.cookies.set(ACCESS_COOKIE, "x")
    client.cookies.set(CSRF_COOKIE, "tok")
    response = client.post("/api/auth/signout", headers={CSRF_HEADER: "tok"})
    assert response.status_code == 200
    assert response.json()["message"] == "Signed out successfully"


def test_csrf_rejects_mismatched_header(client):
    client.cookies.set(ACCESS_COOKIE, "x")
    client.cookies.set(CSRF_COOKIE, "tok")
    response = client.post("/api/auth/signout", headers={CSRF_HEADER: "different"})
    assert response.status_code == 403


def test_bearer_request_is_exempt_from_csrf(client):
    # A Bearer Authorization header is not CSRF-vulnerable, so the check is
    # skipped even when auth cookies happen to be present.
    client.app.dependency_overrides[get_auth_service] = lambda: CookieAuthService()
    client.cookies.set(ACCESS_COOKIE, "x")
    client.cookies.set(CSRF_COOKIE, "tok")
    response = client.post("/api/auth/signout", headers={"Authorization": "Bearer x"})
    assert response.status_code == 200
