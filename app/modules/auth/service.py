from datetime import datetime, timezone
from typing import Any

from app.core.database_client import DatabaseClient
from app.core.security import revoke_token

from app.modules.auth.schemas import AuthUser, TokenResponse


class AuthService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def sign_up(
        self,
        email: str,
        password: str,
        redirect_url: str,
        name: str | None = None,
        company: str | None = None,
        role: str | None = None,
    ) -> TokenResponse:
        """Create a new user and issue auth tokens."""
        auth_response = self.supabase.auth.sign_up(
            {
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "name": name or "",
                        "company": company or "",
                        "role": role or "",
                    },
                    "email_redirect_to": f"{redirect_url}/dashboard",
                },
            }
        )

        if not auth_response.user:
            raise ValueError("Failed to create user")

        user = AuthUser(
            id=auth_response.user.id,
            email=email,
            name=name,
            company=company,
            role=role,
            user_type="free",
            credits_remaining=0,
            plan="free",
        )

        session = auth_response.session
        if not session:
            raise ValueError(
                "Registration successful. Please check your email to verify your account."
            )

        return TokenResponse(
            access_token=session.access_token,
            refresh_token=session.refresh_token,
            expires_in=session.expires_in,
            user=user,
        )

    def sign_in(self, email: str, password: str) -> TokenResponse:
        """Sign in with email/password, return tokens + user profile."""
        auth_response = self.supabase.auth.sign_in_with_password(
            {"email": email, "password": password}
        )

        if not auth_response.user or not auth_response.session:
            raise ValueError("Invalid email or password")

        result = (
            self.supabase.table("users")
            .select("*")
            .eq("id", auth_response.user.id)
            .single()
            .execute()
        )

        if not result.data:
            raise ValueError("User profile not found")

        self.supabase.table("users").update(
            {"last_active": datetime.now(timezone.utc).isoformat()}
        ).eq("id", auth_response.user.id).execute()

        user = AuthUser(
            id=result.data["id"],
            email=result.data["email"],
            name=result.data.get("name"),
            company=result.data.get("company"),
            role=result.data.get("role"),
            user_type=result.data.get("user_type", "free"),
            credits_remaining=result.data.get("credits_remaining", 0),
            plan=result.data.get("plan", "free"),
        )

        return TokenResponse(
            access_token=auth_response.session.access_token,
            refresh_token=auth_response.session.refresh_token,
            expires_in=auth_response.session.expires_in,
            user=user,
        )

    def admin_sign_in(self, email: str, password: str) -> TokenResponse:
        """Sign in and verify admin access."""
        token_response = self.sign_in(email, password)
        if token_response.user.user_type != "admin":
            self.supabase.auth.sign_out()
            raise PermissionError("Access denied — admin privileges required")
        return token_response

    async def sign_out(self, token: str, redis_client: Any | None = None) -> None:
        """Sign out the current user and revoke their access token."""
        user_response = self.supabase.auth.get_user(token)
        if not user_response or not user_response.user:
            raise ValueError("Invalid or expired token")
        if redis_client:
            await revoke_token(token, redis_client, self.supabase.settings)

    def get_user(self, token: str) -> AuthUser:
        """Verify token and return user profile."""
        user_response = self.supabase.auth.get_user(token)
        if not user_response or not user_response.user:
            raise ValueError("Invalid or expired token")

        result = (
            self.supabase.table("users")
            .select("*")
            .eq("id", user_response.user.id)
            .single()
            .execute()
        )

        if not result.data:
            raise ValueError("User profile not found")

        return AuthUser(
            id=result.data["id"],
            email=result.data["email"],
            name=result.data.get("name"),
            company=result.data.get("company"),
            role=result.data.get("role"),
            user_type=result.data.get("user_type", "free"),
            credits_remaining=result.data.get("credits_remaining", 0),
            plan=result.data.get("plan", "free"),
        )

    def reset_password(self, email: str, redirect_url: str) -> None:
        """Trigger password reset flow."""
        self.supabase.auth.reset_password_email(
            email, {"redirect_to": f"{redirect_url}/reset-password"}
        )

    def resend_verification(self, email: str, redirect_url: str) -> None:
        """Resend signup verification email."""
        self.supabase.auth.resend(
            {
                "type": "signup",
                "email": email,
                "options": {
                    "email_redirect_to": f"{redirect_url}/dashboard",
                },
            }
        )

    def update_password(self, token: str, new_password: str) -> None:
        """Update the user's password."""
        user_response = self.supabase.auth.get_user(token)
        if not user_response or not user_response.user:
            raise ValueError("Invalid or expired token")
        self.supabase.auth.admin.update_user_by_id(
            user_response.user.id,
            {"password": new_password},
        )

    async def refresh_session(self, refresh_token: str, redis_client: Any | None = None) -> TokenResponse:
        """Refresh an expired access token and rotate the refresh token."""
        auth_response = self.supabase.auth.refresh_session(refresh_token)
        if not auth_response.session:
            raise ValueError("Failed to refresh session")

        # Revoke the consumed refresh token (rotation)
        if redis_client and auth_response.claims:
            await revoke_token(refresh_token, redis_client, self.supabase.settings)

        user_response = self.supabase.auth.get_user(auth_response.session.access_token)
        if not user_response or not user_response.user:
            raise ValueError("Failed to get user after refresh")

        result = (
            self.supabase.table("users")
            .select("*")
            .eq("id", user_response.user.id)
            .single()
            .execute()
        )

        if not result.data:
            raise ValueError("User profile not found")

        user = AuthUser(
            id=result.data["id"],
            email=result.data["email"],
            name=result.data.get("name"),
            company=result.data.get("company"),
            role=result.data.get("role"),
            user_type=result.data.get("user_type", "free"),
            credits_remaining=result.data.get("credits_remaining", 0),
            plan=result.data.get("plan", "free"),
        )

        return TokenResponse(
            access_token=auth_response.session.access_token,
            refresh_token=auth_response.session.refresh_token,
            expires_in=auth_response.session.expires_in,
            user=user,
        )
