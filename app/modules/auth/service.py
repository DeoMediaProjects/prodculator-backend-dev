import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    create_verification_token,
    decode_token,
    revoke_token,
)
from app.modules.admin.schemas import AdminTokenResponse, AdminUser
from app.modules.auth.schemas import AuthUser, SignUpResponse, TokenResponse
from app.modules.email.service import EmailService

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, supabase: DatabaseClient, settings: Settings | None = None):
        self.supabase = supabase
        self.settings = settings or get_settings()
        self.email_service = EmailService(self.settings)

    def sign_up(
        self,
        email: str,
        password: str,
        redirect_url: str,
        name: str | None = None,
        company: str | None = None,
        role: str | None = None,
    ) -> TokenResponse | SignUpResponse:
        """Create a new user account and send a verification email.

        Returns SignUpResponse (no tokens) because the user must confirm their
        email address before they can log in.
        """
        try:
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
                    },
                }
            )
        except Exception as e:
            msg = str(e).lower()
            if "already registered" in msg or "already exists" in msg or "user already" in msg:
                raise ValueError(
                    "An account with this email already exists. Please sign in instead."
                )
            raise

        if not auth_response.user:
            raise ValueError("Failed to create user")

        # auth_response.session is always None here because AuthClient.sign_up()
        # no longer issues tokens — email verification is required first.
        verification_token = create_verification_token(
            auth_response.user.id, email, self.settings
        )
        verification_url = f"{redirect_url}/auth/callback?token={verification_token}"

        try:
            self.email_service.send(
                to_email=email,
                template_name="verify_email",
                context={"name": name, "verification_url": verification_url},
            )
        except Exception:
            logger.warning("Failed to send verification email to %s", email)

        return SignUpResponse(verification_required=True, email=email)

    def verify_email_token(self, token: str) -> TokenResponse:
        """Exchange a valid email-verification JWT for real access/refresh tokens."""
        try:
            claims = decode_token(token, self.settings)
        except ValueError:
            raise ValueError("Verification link is invalid or has expired.")

        if claims.get("type") != "email_verification":
            raise ValueError("Invalid verification token.")

        user_id = claims.get("sub")
        if not user_id:
            raise ValueError("Invalid verification token.")

        result = (
            self.supabase.table("users")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if not result.data:
            raise ValueError("User not found.")

        data = result.data
        user_type = data.get("user_type", "free")

        # Flip the account to verified. This is what makes the magic link meaningful:
        # until it runs, sign-in is rejected by AuthClient.sign_in_with_password.
        if not data.get("email_verified", False):
            self.supabase.table("users").update(
                {"email_verified": True}
            ).eq("id", user_id).execute()

        access_token, expires_in = create_access_token(user_id, user_type, self.settings)
        refresh_token = create_refresh_token(user_id, user_type, self.settings)

        user = AuthUser(
            id=data["id"],
            email=data["email"],
            name=data.get("name"),
            company=data.get("company"),
            role=data.get("role"),
            user_type=user_type,
            credits_remaining=data.get("credits_remaining", 0),
            plan=data.get("plan", "free"),
        )

        try:
            self.email_service.send(
                to_email=data["email"],
                template_name="welcome",
                context={"name": data.get("name") or data["email"]},
            )
        except Exception:
            logger.warning("Failed to send welcome email to %s", data["email"])

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
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

    def admin_sign_in(self, email: str, password: str) -> AdminTokenResponse:
        """Sign in against the admins table. Rejects non-admin credentials."""
        auth_response = self.supabase.auth.sign_in_admin(email, password)

        if not auth_response.user or not auth_response.session:
            raise ValueError("Invalid email or password")

        result = (
            self.supabase.table("admins")
            .select("*")
            .eq("id", auth_response.user.id)
            .single()
            .execute()
        )

        if not result.data:
            raise ValueError("Invalid email or password")

        self.supabase.table("admins").update(
            {"last_login": datetime.now(timezone.utc).isoformat()}
        ).eq("id", auth_response.user.id).execute()

        admin = AdminUser(
            id=result.data["id"],
            email=result.data["email"],
            name=result.data.get("name"),
            role=result.data.get("role", "master_admin"),
        )

        return AdminTokenResponse(
            access_token=auth_response.session.access_token,
            refresh_token=auth_response.session.refresh_token,
            expires_in=auth_response.session.expires_in,
            user=admin,
        )

    async def admin_refresh_session(
        self, refresh_token: str, redis_client: Any | None = None
    ) -> AdminTokenResponse:
        """Refresh an admin session. Rejects tokens not belonging to an admin."""
        auth_response = self.supabase.auth.refresh_admin_session(refresh_token)
        if not auth_response.session:
            raise ValueError("Failed to refresh session")

        if redis_client and auth_response.claims:
            # Best-effort, same rationale as refresh_session: a Redis outage
            # must not 500 every admin session refresh.
            try:
                await revoke_token(refresh_token, redis_client, self.supabase.settings)
            except Exception as exc:
                logger.warning(
                    "Admin refresh-token revocation skipped (Redis unavailable): %s", exc
                )

        if not auth_response.user:
            raise ValueError("Failed to refresh session")

        admin_id = auth_response.user.id

        result = (
            self.supabase.table("admins")
            .select("*")
            .eq("id", admin_id)
            .single()
            .execute()
        )

        if not result.data:
            raise ValueError("Invalid refresh token")

        admin = AdminUser(
            id=result.data["id"],
            email=result.data["email"],
            name=result.data.get("name"),
            role=result.data.get("role", "master_admin"),
        )

        return AdminTokenResponse(
            access_token=auth_response.session.access_token,
            refresh_token=auth_response.session.refresh_token,
            expires_in=auth_response.session.expires_in,
            user=admin,
        )

    async def sign_out(self, token: str, redis_client: Any | None = None) -> None:
        """Sign out the current user and revoke their access token."""
        user_response = self.supabase.auth.get_user(token)
        if not user_response or not user_response.user:
            raise ValueError("Invalid or expired token")
        if redis_client:
            await revoke_token(token, redis_client, self.supabase.settings)

    async def sign_out_admin(self, token: str, redis_client: Any | None = None) -> None:
        """Sign out the current admin and revoke their access token."""
        user_response = self.supabase.auth.get_admin(token)
        if not user_response or not user_response.user:
            raise ValueError("Invalid or expired token")
        if redis_client:
            try:
                await revoke_token(token, redis_client, self.supabase.settings)
            except Exception:
                pass  # Redis unavailable — degrade gracefully, token expires naturally

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

    def sign_in_with_google(self, firebase_claims: dict) -> TokenResponse:
        """Sign in or register a user via a verified Firebase Google ID token."""
        email: str = firebase_claims.get("email", "").strip().lower()
        if not email:
            raise ValueError("Google account has no email address")

        google_uid: str = firebase_claims.get("uid", "")
        name: str | None = firebase_claims.get("name") or None

        auth_response = self.supabase.auth.sign_in_with_google(
            email=email,
            google_uid=google_uid,
            name=name,
        )

        if not auth_response.user or not auth_response.session:
            raise ValueError("Failed to authenticate with Google")

        user_id = auth_response.user.id

        self.supabase.table("users").update(
            {"last_active": datetime.now(timezone.utc).isoformat()}
        ).eq("id", user_id).execute()

        result = (
            self.supabase.table("users")
            .select("*")
            .eq("id", user_id)
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

    def reset_password(self, email: str, redirect_url: str) -> None:
        """Generate a password-reset token and email a reset link via Brevo.

        Silent no-op if the address is not registered — the caller never reveals
        whether an account exists.
        """
        email = email.strip().lower()
        result = (
            self.supabase.table("users")
            .select("id,email,name,password_hash")
            .eq("email", email)
            .single()
            .execute()
        )
        if not result.data:
            return  # Don't reveal whether the address is registered

        data = result.data
        # Accounts created via Google have no password to reset.
        if not data.get("password_hash"):
            return

        reset_token = create_password_reset_token(data["id"], email, self.settings)
        reset_url = f"{redirect_url}/reset-password?token={reset_token}"

        self.email_service.send(
            to_email=email,
            template_name="reset_password",
            context={"name": data.get("name"), "reset_url": reset_url},
        )

    def confirm_password_reset(self, token: str, new_password: str) -> None:
        """Validate a password-reset token and set the account's new password."""
        try:
            claims = decode_token(token, self.settings)
        except ValueError:
            raise ValueError("Reset link is invalid or has expired.")

        if claims.get("type") != "password_reset":
            raise ValueError("Invalid reset token.")

        user_id = claims.get("sub")
        if not user_id:
            raise ValueError("Invalid reset token.")

        result = (
            self.supabase.table("users")
            .select("id")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if not result.data:
            raise ValueError("User not found.")

        self.supabase.auth.admin.update_user_by_id(user_id, {"password": new_password})

    def resend_verification(self, email: str, redirect_url: str) -> None:
        """Re-generate a verification token and re-send the verification email."""
        email = email.strip().lower()
        result = (
            self.supabase.table("users")
            .select("id,email,name,email_verified")
            .eq("email", email)
            .single()
            .execute()
        )
        if not result.data:
            return  # Silently — don't reveal whether the address is registered

        data = result.data
        if data.get("email_verified", False):
            return  # Already verified — nothing to resend

        verification_token = create_verification_token(data["id"], email, self.settings)
        verification_url = f"{redirect_url}/auth/callback?token={verification_token}"

        self.email_service.send(
            to_email=email,
            template_name="verify_email",
            context={"name": data.get("name"), "verification_url": verification_url},
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

        if redis_client and auth_response.claims:
            # Revocation of the rotated-out token is defence-in-depth, not the
            # primary auth control (the JWT still expires on schedule). A Redis
            # outage must not turn every session refresh into a 500.
            try:
                await revoke_token(refresh_token, redis_client, self.supabase.settings)
            except Exception as exc:
                logger.warning(
                    "Refresh-token revocation skipped (Redis unavailable): %s", exc
                )

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
