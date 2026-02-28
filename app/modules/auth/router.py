import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.cache import get_redis_client
from app.core.database_client import DatabaseClient
from app.core.config import Settings, get_settings
from app.core.dependencies import get_supabase, get_current_user
from app.core.schemas import SuccessResponse

logger = logging.getLogger(__name__)
from app.modules.auth.schemas import (
    AuthUser,
    SignUpRequest,
    SignInRequest,
    TokenResponse,
    ResetPasswordRequest,
    ResendVerificationRequest,
    UpdatePasswordRequest,
    RefreshTokenRequest,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

_bearer = HTTPBearer(auto_error=True)


def get_auth_service(supabase: DatabaseClient = Depends(get_supabase)) -> AuthService:
    return AuthService(supabase)


@router.post("/signup", response_model=TokenResponse)
async def signup(
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
async def signin(
    body: SignInRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign in with email and password."""
    try:
        return auth_service.sign_in(email=body.email, password=body.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Sign in failed")


@router.post("/signout", response_model=SuccessResponse)
async def signout(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign out the current user and revoke their token."""
    redis = get_redis_client(settings)
    try:
        await auth_service.sign_out(credentials.credentials, redis_client=redis)
        return SuccessResponse(message="Signed out successfully")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Sign out failed")
    finally:
        await redis.aclose()


@router.get("/me", response_model=AuthUser)
async def get_me(user: AuthUser = Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    return user


@router.post("/reset-password", response_model=SuccessResponse)
async def reset_password(
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
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Refresh an expired access token and rotate the refresh token."""
    redis = get_redis_client(settings)
    try:
        return await auth_service.refresh_session(refresh_token=body.refresh_token, redis_client=redis)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Token refresh failed")
    finally:
        await redis.aclose()
