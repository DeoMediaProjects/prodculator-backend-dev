from app.core.dependencies import get_current_user, get_supabase
from app.modules.auth.router import get_auth_service
from app.modules.auth.schemas import AuthUser, TokenResponse


class FakeAuthService:
    def __init__(self, user_type: str = "free"):
        self.user_type = user_type

    def sign_up(self, **kwargs):
        return self._token_response()

    def sign_in(self, **kwargs):
        return self._token_response()

    def sign_out(self, token: str):
        if token == "invalid":
            raise ValueError("Invalid or expired token")

    def admin_sign_in(self, **kwargs):
        if self.user_type != "admin":
            raise PermissionError("Access denied — admin privileges required")
        return self._token_response(user_type="admin")

    def refresh_session(self, refresh_token: str):
        return self._token_response()

    def reset_password(self, **kwargs):
        return None

    def resend_verification(self, **kwargs):
        return None

    def update_password(self, **kwargs):
        return None

    def _token_response(self, user_type: str | None = None):
        return TokenResponse(
            access_token="access",
            refresh_token="refresh",
            expires_in=3600,
            user=AuthUser(
                id="user-1",
                email="user@example.com",
                user_type=user_type or self.user_type,
                credits_remaining=1,
                plan="free",
            ),
        )


def test_signup(client):
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/signup",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] == "access"


def test_admin_signin_forbidden_for_non_admin(client):
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService(user_type="free")
    response = client.post(
        "/api/auth/admin/signin",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert response.status_code == 403


def test_me_returns_user_profile(client, auth_user):
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json()["id"] == auth_user.id


def test_me_invalid_token_returns_401(client):
    class BadAuth:
        @staticmethod
        def get_user(token):
            raise Exception("bad token")

    class FakeSupabase:
        auth = BadAuth()

    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase()
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401


def test_resend_verification_returns_success(client):
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/resend-verification",
        json={"email": "user@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Verification email sent"
