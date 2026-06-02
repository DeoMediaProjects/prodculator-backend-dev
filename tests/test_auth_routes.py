from app.core.dependencies import get_current_user, get_supabase
from app.modules.auth.router import get_auth_service
from app.modules.admin.auth_router import get_auth_service as get_admin_auth_service
from app.modules.auth.schemas import AuthUser, SignUpResponse, TokenResponse


class FakeAuthService:
    def __init__(self, user_type: str = "free", requires_verification: bool = True):
        self.user_type = user_type
        self.requires_verification = requires_verification

    def sign_up(self, email: str = "user@example.com", **kwargs):
        if self.requires_verification:
            return SignUpResponse(verification_required=True, email=email)
        return self._token_response()

    def verify_email_token(self, token: str) -> TokenResponse:
        if token == "invalid-token":
            raise ValueError("Verification link is invalid or has expired.")
        return self._token_response()

    def sign_in(self, **kwargs):
        return self._token_response()

    def sign_out(self, token: str):
        if token == "invalid":
            raise ValueError("Invalid or expired token")

    def admin_sign_in(self, **kwargs):
        if self.user_type != "admin":
            raise ValueError("Invalid email or password")
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


# ── signup ────────────────────────────────────────────────────────────────────

def test_signup_returns_verification_required(client):
    """Normal signup → verification email sent, no tokens yet."""
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/signup",
        json={"email": "new@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verification_required"] is True
    assert body["email"] == "new@example.com"
    assert "access_token" not in body


def test_signup_returns_tokens_when_service_does_not_require_verification(client):
    """Edge-case: service returns tokens directly (e.g. Google auth path)."""
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService(requires_verification=False)
    response = client.post(
        "/api/auth/signup",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] == "access"


def test_signup_rejects_weak_password(client):
    """Schema enforces minimum 8-character password before the service is called."""
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/signup",
        json={"email": "user@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_signup_rejects_missing_email(client):
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/signup",
        json={"password": "password123"},
    )
    assert response.status_code == 422


def test_signup_accepts_optional_profile_fields(client):
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/signup",
        json={
            "email": "user@example.com",
            "password": "password123",
            "name": "Jane Doe",
            "company": "Acme Films",
            "role": "producer",
        },
    )
    assert response.status_code == 200


def test_signup_duplicate_email_returns_400_with_friendly_message(client):
    """Duplicate-email error from the custom auth client surfaces as a clear 400."""
    class DuplicateEmailService(FakeAuthService):
        def sign_up(self, **kwargs):
            raise ValueError(
                "An account with this email already exists. Please sign in instead."
            )

    client.app.dependency_overrides[get_auth_service] = lambda: DuplicateEmailService()
    response = client.post(
        "/api/auth/signup",
        json={"email": "dup@example.com", "password": "password123"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "already exists" in detail
    assert "sign in" in detail.lower()


# ── verify-email ──────────────────────────────────────────────────────────────

def test_verify_email_returns_tokens_for_valid_token(client):
    """A valid verification JWT is exchanged for access + refresh tokens."""
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/verify-email",
        json={"token": "valid-jwt-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "access"
    assert body["refresh_token"] == "refresh"
    assert body["user"]["id"] == "user-1"


def test_verify_email_returns_400_for_invalid_token(client):
    """An expired or malformed token is rejected with 400."""
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/verify-email",
        json={"token": "invalid-token"},
    )
    assert response.status_code == 400
    assert "invalid or has expired" in response.json()["detail"].lower()


def test_verify_email_rejects_missing_token(client):
    """Schema rejects requests with no token field."""
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post("/api/auth/verify-email", json={})
    assert response.status_code == 422


# ── other auth routes ─────────────────────────────────────────────────────────

def test_admin_signin_forbidden_for_non_admin(client):
    client.app.dependency_overrides[get_admin_auth_service] = lambda: FakeAuthService(user_type="free")
    response = client.post(
        "/api/admin/auth/signin",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert response.status_code == 401


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


# ── resend verification ───────────────────────────────────────────────────────

def test_resend_verification_returns_success(client):
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/resend-verification",
        json={"email": "user@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Verification email sent"


def test_resend_verification_always_returns_200_even_on_service_error(client):
    """Do not leak whether the email is registered — swallow service errors silently."""
    class BrokenResend(FakeAuthService):
        def resend_verification(self, **kwargs):
            raise RuntimeError("Email service unreachable")

    client.app.dependency_overrides[get_auth_service] = lambda: BrokenResend()
    response = client.post(
        "/api/auth/resend-verification",
        json={"email": "unknown@example.com"},
    )
    assert response.status_code == 200


def test_resend_verification_rejects_invalid_email(client):
    """Schema validation rejects non-email values before the service is called."""
    client.app.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    response = client.post(
        "/api/auth/resend-verification",
        json={"email": "not-an-email"},
    )
    assert response.status_code == 422
