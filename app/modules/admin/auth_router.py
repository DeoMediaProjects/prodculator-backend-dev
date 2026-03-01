from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.cache import get_redis_client
from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_admin, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminTokenResponse, AdminUser
from app.modules.auth.schemas import RefreshTokenRequest, SignInRequest
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/api/admin/auth", tags=["Admin Auth"])

_bearer = HTTPBearer(auto_error=True)


def get_auth_service(supabase: DatabaseClient = Depends(get_supabase)) -> AuthService:
    return AuthService(supabase)


@router.post("/signin", response_model=AdminTokenResponse)
async def admin_signin(
    body: SignInRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign in as an admin. Rejects credentials not found in the admins table."""
    try:
        return auth_service.admin_sign_in(email=body.email, password=body.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Admin sign in failed")


@router.post("/signout", response_model=SuccessResponse)
async def admin_signout(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Sign out the current admin and revoke their token."""
    redis = get_redis_client(settings)
    try:
        await auth_service.sign_out_admin(credentials.credentials, redis_client=redis)
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
    body: RefreshTokenRequest,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Refresh an admin session token. Rejects tokens not belonging to an admin."""
    redis = get_redis_client(settings)
    try:
        return await auth_service.admin_refresh_session(
            refresh_token=body.refresh_token, redis_client=redis
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Token refresh failed")
    finally:
        await redis.aclose()
