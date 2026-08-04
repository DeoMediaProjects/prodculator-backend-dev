import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth_cookies import (
    clear_auth_cookies,
    extract_access_token,
    extract_refresh_token,
    set_auth_cookies,
)
from app.core.cache import get_redis
from app.core.database_client import DatabaseClient, EmailNotVerifiedError
from app.core.config import Settings, get_settings
from app.core.dependencies import get_supabase, get_current_user
from app.core.firebase import verify_firebase_token
from app.core.limiter import limiter
from app.core.schemas import SuccessResponse
from app.core.storage import StorageClient

logger = logging.getLogger(__name__)
from app.modules.auth.logo import LogoRejected, logo_path, normalise_logo
from app.modules.auth.schemas import (
    AuthUser,
    LogoResponse,
    MeResponse,
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

# auto_error=False — the token may arrive in an httpOnly cookie instead of the
# Authorization header, so a missing header is not itself an error.
_bearer = HTTPBearer(auto_error=False)

# Logical storage bucket for account logos, kept apart from report PDFs so the
# two have distinct key prefixes and can be lifecycled separately.
_LOGO_BUCKET = "logos"


def _logo_url(path: str | None) -> str | None:
    """Presign the stored logo for reading.

    Holds the storage-relative path rather than the fully-qualified S3 key,
    because that is what get_public_url signs. Never raises: an unsignable logo
    should drop the masthead back to its placeholder, not 500 /me.
    """
    if not path:
        return None
    try:
        return StorageClient().from_(_LOGO_BUCKET).get_public_url(path)
    except Exception:
        logger.warning("Could not presign logo path=%s", path, exc_info=True)
        return None


async def _bust_profile_cache(user_id: str) -> None:
    """Drop the cached AuthUser so the new logo_key is picked up immediately
    rather than after the profile TTL expires."""
    try:
        redis = get_redis()
        await redis.delete(f"user_profile:{user_id}")
    except Exception:
        pass  # Best-effort: the cache expires on its own.


def _set_token_cookies(response: Response, token: TokenResponse, settings: Settings) -> None:
    """Issue the JWT pair as httpOnly cookies on a successful auth response."""
    set_auth_cookies(
        response,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        settings=settings,
        access_max_age=token.expires_in,
    )


def get_auth_service(
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    return AuthService(supabase, settings)


@router.post("/signup", response_model=TokenResponse | SignUpResponse)
@limiter.limit("10/minute")
async def signup(
    request: Request,
    response: Response,
    body: SignUpRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Register a new user account."""
    try:
        result = auth_service.sign_up(
            email=body.email,
            password=body.password,
            redirect_url=settings.FRONTEND_URL,
            name=body.name,
            company=body.company,
            role=body.role,
        )
        # Most signups require email verification first (no tokens yet). When the
        # service does issue a session immediately, also set the auth cookies.
        if isinstance(result, TokenResponse):
            _set_token_cookies(response, result, settings)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Signup error: {type(e).__name__}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create account")


@router.post("/signin", response_model=TokenResponse)
@limiter.limit("10/minute")
async def signin(
    request: Request,
    response: Response,
    body: SignInRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign in with email and password."""
    try:
        token = auth_service.sign_in(email=body.email, password=body.password)
        _set_token_cookies(response, token, settings)
        return token
    except EmailNotVerifiedError as e:
        # 403 (not 401) so the client can distinguish "credentials are fine but the
        # email isn't verified yet" from "wrong email/password" and offer a resend.
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Sign in failed")


@router.post("/google", response_model=TokenResponse)
@limiter.limit("10/minute")
async def google_auth(
    request: Request,
    response: Response,
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
        token = auth_service.sign_in_with_google(claims)
        _set_token_cookies(response, token, settings)
        return token
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Google sign-in failed")
        raise HTTPException(status_code=500, detail="Google sign-in failed")


@router.post("/signout", response_model=SuccessResponse)
async def signout(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign out the current user, revoke their token, and clear auth cookies."""
    token = extract_access_token(request, credentials.credentials if credentials else None)
    # Always clear cookies, even if there is nothing to revoke (idempotent).
    clear_auth_cookies(response, settings)
    if not token:
        return SuccessResponse(message="Signed out successfully")
    try:
        await auth_service.sign_out(token, redis_client=get_redis())
        return SuccessResponse(message="Signed out successfully")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Sign out failed")


@router.get("/me", response_model=MeResponse)
async def get_me(user: AuthUser = Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    return MeResponse(**user.model_dump(), logo_url=_logo_url(user.logo_key))


# Multipart image upload from an unprivileged caller, so it is rate limited on
# top of the validation in logo.py: re-encoding is CPU work, and a burst of
# large uploads is the cheap way to tie up workers.
@router.post("/me/logo", response_model=LogoResponse)
@limiter.limit("10/minute")
async def upload_logo(
    request: Request,
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
    supabase: DatabaseClient = Depends(get_supabase),
):
    """Upload the account's logo, replacing any existing one."""
    raw = await file.read()
    try:
        payload = normalise_logo(raw)
    except LogoRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    path = logo_path(user.id)
    try:
        StorageClient().from_(_LOGO_BUCKET).upload(
            path, payload, {"content-type": "image/png"},
        )
    except Exception:
        logger.exception("Logo upload to storage failed for user=%s", user.id)
        raise HTTPException(status_code=500, detail="Could not store that logo")

    supabase.table("users").update({"logo_key": path}).eq("id", user.id).execute()
    await _bust_profile_cache(user.id)
    return LogoResponse(logo_url=_logo_url(path))


@router.delete("/me/logo", response_model=LogoResponse)
async def delete_logo(
    user: AuthUser = Depends(get_current_user),
    supabase: DatabaseClient = Depends(get_supabase),
):
    """Remove the account's logo.

    The stored object is left in place rather than deleted: it is overwritten
    on the next upload, and a failed delete must not leave the row still
    pointing at something the user has been told is gone. Clearing the column
    is what makes it invisible.
    """
    supabase.table("users").update({"logo_key": None}).eq("id", user.id).execute()
    await _bust_profile_cache(user.id)
    return LogoResponse(logo_url=None)


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
@limiter.limit("5/minute")
async def resend_verification(
    request: Request,
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
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    response: Response,
    body: VerifyEmailRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Exchange an email-verification token for access/refresh tokens."""
    try:
        token = auth_service.verify_email_token(body.token)
        _set_token_cookies(response, token, settings)
        return token
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Verification failed")


@router.post("/update-password", response_model=SuccessResponse)
async def update_password(
    request: Request,
    body: UpdatePasswordRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Update the current user's password."""
    token = extract_access_token(request, credentials.credentials if credentials else None)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        auth_service.update_password(token=token, new_password=body.new_password)
        return SuccessResponse(message="Password updated successfully")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update password")


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("60/minute")
async def refresh_token(
    request: Request,
    response: Response,
    body: RefreshTokenRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Refresh an expired access token and rotate the refresh token.

    The refresh token comes from the request body (API clients) or the httpOnly
    refresh cookie (browser). On success the rotated pair is re-issued as cookies.
    """
    refresh = extract_refresh_token(request, body.refresh_token)
    if not refresh:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    try:
        token = await auth_service.refresh_session(refresh_token=refresh, redis_client=get_redis())
        _set_token_cookies(response, token, settings)
        return token
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Token refresh failed")
