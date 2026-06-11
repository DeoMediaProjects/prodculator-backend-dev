from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth_cookies import (
    clear_auth_cookies,
    extract_access_token,
    extract_refresh_token,
    set_auth_cookies,
)
from app.core.cache import get_redis_client
from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_admin, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminTokenResponse, AdminUser
from app.modules.auth.schemas import RefreshTokenRequest, SignInRequest
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/api/admin/auth", tags=["Admin Auth"])

# auto_error=False — the token may arrive in an httpOnly cookie instead.
_bearer = HTTPBearer(auto_error=False)


def get_auth_service(supabase: DatabaseClient = Depends(get_supabase)) -> AuthService:
    return AuthService(supabase)


def _set_admin_cookies(response: Response, token: AdminTokenResponse, settings: Settings) -> None:
    set_auth_cookies(
        response,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        settings=settings,
        access_max_age=token.expires_in,
    )


@router.post("/signin", response_model=AdminTokenResponse)
async def admin_signin(
    response: Response,
    body: SignInRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign in as an admin. Rejects credentials not found in the admins table."""
    try:
        token = auth_service.admin_sign_in(email=body.email, password=body.password)
        _set_admin_cookies(response, token, settings)
        return token
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Admin sign in failed")


@router.post("/signout", response_model=SuccessResponse)
async def admin_signout(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign out the current admin, revoke their token, and clear auth cookies."""
    token = extract_access_token(request, credentials.credentials if credentials else None)
    clear_auth_cookies(response, settings)
    if not token:
        return SuccessResponse(message="Signed out successfully")
    redis = get_redis_client(settings)
    try:
        await auth_service.sign_out_admin(token, redis_client=redis)
        return SuccessResponse(message="Signed out successfully")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Sign out failed")
    finally:
        await redis.aclose()


@router.get("/me", response_model=AdminUser)
async def admin_me(admin: AdminUser = Depends(get_current_admin)):
    """Return the currently authenticated admin's profile."""
    return admin


@router.post("/refresh", response_model=AdminTokenResponse)
async def admin_refresh(
    request: Request,
    response: Response,
    body: RefreshTokenRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Refresh an admin session token. Rejects tokens not belonging to an admin."""
    refresh = extract_refresh_token(request, body.refresh_token)
    if not refresh:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    redis = get_redis_client(settings)
    try:
        token = await auth_service.admin_refresh_session(
            refresh_token=refresh, redis_client=redis
        )
        _set_admin_cookies(response, token, settings)
        return token
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Token refresh failed")
    finally:
        await redis.aclose()
