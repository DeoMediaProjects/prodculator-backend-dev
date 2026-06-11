import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.cache import get_redis
from app.core.database_client import DatabaseClient, EmailNotVerifiedError
from app.core.config import Settings, get_settings
from app.core.dependencies import get_supabase, get_current_user
from app.core.firebase import verify_firebase_token
from app.core.limiter import limiter
from app.core.schemas import SuccessResponse

logger = logging.getLogger(__name__)
from app.modules.auth.schemas import (
    AuthUser,
    SignUpRequest,
    SignInRequest,
    SignUpResponse,
    TokenResponse,
    ResetPasswordRequest,
    ConfirmResetPasswordRequest,
    ResendVerificationRequest,
    UpdatePasswordRequest,
    RefreshTokenRequest,
    GoogleAuthRequest,
    VerifyEmailRequest,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

_bearer = HTTPBearer(auto_error=True)


def get_auth_service(
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    return AuthService(supabase, settings)


@router.post("/signup", response_model=TokenResponse | SignUpResponse)
@limiter.limit("10/minute")
async def signup(
    request: Request,
    body: SignUpRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Register a new user account."""
    try:
        return auth_service.sign_up(
            email=body.email,
            password=body.password,
            redirect_url=settings.FRONTEND_URL,
            name=body.name,
            company=body.company,
            role=body.role,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Signup error: {type(e).__name__}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create account")


@router.post("/signin", response_model=TokenResponse)
@limiter.limit("10/minute")
async def signin(
    request: Request,
    body: SignInRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign in with email and password."""
    try:
        return auth_service.sign_in(email=body.email, password=body.password)
    except EmailNotVerifiedError as e:
        # 403 (not 401) so the client can distinguish "credentials are fine but the
        # email isn't verified yet" from "wrong email/password" and offer a resend.
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Sign in failed")


@router.post("/google", response_model=TokenResponse)
async def google_auth(
    body: GoogleAuthRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Exchange a Firebase Google ID token for a backend JWT pair.

    The frontend should obtain the ID token from Firebase after a
    ``signInWithPopup`` / ``signInWithRedirect`` call, then POST it here.
    The response is identical to ``/signin`` — store the tokens and use
    ``access_token`` as the ``Authorization: Bearer`` header.
    """
    try:
        claims = verify_firebase_token(body.id_token, settings)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except RuntimeError as e:
        # Firebase not configured
        logger.error("Firebase not configured: %s", e)
        raise HTTPException(status_code=503, detail="Google auth is not available")

    try:
        return auth_service.sign_in_with_google(claims)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Google sign-in failed")
        raise HTTPException(status_code=500, detail="Google sign-in failed")


@router.post("/signout", response_model=SuccessResponse)
async def signout(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign out the current user and revoke their token."""
    try:
        await auth_service.sign_out(credentials.credentials, redis_client=get_redis())
        return SuccessResponse(message="Signed out successfully")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Sign out failed")


@router.get("/me", response_model=AuthUser)
async def get_me(user: AuthUser = Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    return user


@router.post("/reset-password", response_model=SuccessResponse)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Send a password reset email."""
    try:
        auth_service.reset_password(email=body.email, redirect_url=settings.FRONTEND_URL)
    except Exception:
        pass  # Don't reveal whether the email exists
    return SuccessResponse(message="Password reset email sent")


@router.post("/reset-password/confirm", response_model=SuccessResponse)
@limiter.limit("5/minute")
async def confirm_reset_password(
    request: Request,
    body: ConfirmResetPasswordRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Complete a password reset using the token from the reset email."""
    try:
        auth_service.confirm_password_reset(token=body.token, new_password=body.new_password)
        return SuccessResponse(message="Password updated successfully")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.error("Password reset confirmation failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reset password")


@router.post("/resend-verification", response_model=SuccessResponse)
async def resend_verification(
    body: ResendVerificationRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Resend account verification email."""
    try:
        auth_service.resend_verification(email=body.email, redirect_url=settings.FRONTEND_URL)
    except Exception:
        pass  # Don't reveal whether the email exists
    return SuccessResponse(message="Verification email sent")


@router.post("/verify-email", response_model=TokenResponse)
async def verify_email(
    body: VerifyEmailRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Exchange an email-verification token for access/refresh tokens."""
    try:
        return auth_service.verify_email_token(body.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Verification failed")


@router.post("/update-password", response_model=SuccessResponse)
async def update_password(
    body: UpdatePasswordRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Update the current user's password."""
    try:
        auth_service.update_password(token=credentials.credentials, new_password=body.new_password)
        return SuccessResponse(message="Password updated successfully")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update password")


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Refresh an expired access token and rotate the refresh token."""
    try:
        return await auth_service.refresh_session(refresh_token=body.refresh_token, redis_client=get_redis())
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Token refresh failed")
