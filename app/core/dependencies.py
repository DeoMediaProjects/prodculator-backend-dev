from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.cache import get_redis_client
from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.db import get_db
from app.core.security import is_token_revoked
from app.modules.auth.schemas import AuthUser

_bearer_scheme = HTTPBearer(auto_error=True)


def get_supabase(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DatabaseClient:
    """Compatibility dependency that now returns a SQLAlchemy-backed client."""
    return DatabaseClient(db, settings)


def get_db_client(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DatabaseClient:
    return DatabaseClient(db, settings)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> AuthUser:
    """Extract and verify JWT, check blocklist, return user profile."""
    token = credentials.credentials

    try:
        user_response = supabase.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if not user_response or not user_response.user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Check token blocklist (revoked via sign-out)
    if user_response.claims:
        try:
            redis = get_redis_client(settings)
            if await is_token_revoked(user_response.claims, redis):
                raise HTTPException(status_code=401, detail="Token has been revoked")
            await redis.aclose()
        except HTTPException:
            raise
        except Exception:
            pass  # Redis unavailable — degrade gracefully, token still valid

    result = (
        supabase.table("users")
        .select("*")
        .eq("id", user_response.user.id)
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=401, detail="User profile not found")

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


async def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Require admin user_type."""
    if user.user_type != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class RequireRole:
    """Dependency class that enforces a specific role value on the user.

    Usage: Depends(RequireRole("producer"))
    """

    def __init__(self, required_role: str) -> None:
        self.required_role = required_role

    async def __call__(self, user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if user.role != self.required_role:
            raise HTTPException(status_code=403, detail=f"Role '{self.required_role}' required")
        return user


_optional_bearer_scheme = HTTPBearer(auto_error=False)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer_scheme),
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> AuthUser | None:
    """Optional auth; returns None if no token provided."""
    if not credentials:
        return None
    try:
        return await get_current_user(credentials, supabase, settings)
    except HTTPException:
        return None
