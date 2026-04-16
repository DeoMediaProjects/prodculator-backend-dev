import redis.asyncio as aioredis
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.cache import get_redis
from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.db import get_db
from app.core.security import is_token_revoked
from app.modules.admin.schemas import AdminUser
from app.modules.auth.schemas import AuthUser

_bearer_scheme = HTTPBearer(auto_error=True)

_USER_PROFILE_TTL = 300  # 5 minutes


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

    # Check token blocklist using the shared Redis pool (no open/close needed).
    if user_response.claims:
        try:
            redis = get_redis()
            if await is_token_revoked(user_response.claims, redis):
                raise HTTPException(status_code=401, detail="Token has been revoked")
        except HTTPException:
            raise
        except Exception:
            pass  # Redis unavailable — degrade gracefully, token still valid

    user_id = user_response.user.id

    # Try cache first to avoid a DB round-trip on every request.
    try:
        redis = get_redis()
        cache_key = f"user_profile:{user_id}"
        cached = await redis.get(cache_key)
        if cached:
            return AuthUser.model_validate_json(cached)
    except Exception:
        pass  # Cache miss — fall through to DB

    result = (
        supabase.table("users")
        .select("*")
        .eq("id", user_id)
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=401, detail="User profile not found")

    if result.data.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Account has been blocked")

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

    try:
        redis = get_redis()
        await redis.setex(cache_key, _USER_PROFILE_TTL, user.model_dump_json())
    except Exception:
        pass  # Best-effort caching

    return user


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> AdminUser:
    """Validate token against the admins table. Rejects user tokens."""
    token = credentials.credentials

    try:
        admin_response = supabase.auth.get_admin(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if not admin_response or not admin_response.user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if admin_response.claims:
        try:
            redis = get_redis()
            if await is_token_revoked(admin_response.claims, redis):
                raise HTTPException(status_code=401, detail="Token has been revoked")
        except HTTPException:
            raise
        except Exception:
            pass  # Redis unavailable — degrade gracefully

    result = (
        supabase.table("admins")
        .select("*")
        .eq("id", admin_response.user.id)
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return AdminUser(
        id=result.data["id"],
        email=result.data["email"],
        name=result.data.get("name"),
        role=result.data.get("role", "master_admin"),
    )


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


class RequirePlan:
    """Dependency that enforces a minimum plan level.

    Usage: Depends(RequirePlan("professional"))
    Allows the specified plan and any higher plan (studio > professional > free).
    """

    _PLAN_HIERARCHY = {"free": 0, "single": 1, "professional": 1, "studio": 2}

    def __init__(self, minimum_plan: str) -> None:
        self.minimum_plan = minimum_plan
        self.min_level = self._PLAN_HIERARCHY.get(minimum_plan, 0)

    async def __call__(self, user: AuthUser = Depends(get_current_user)) -> AuthUser:
        from app.models.enums import normalize_plan

        user_plan = normalize_plan(user.plan)
        user_level = self._PLAN_HIERARCHY.get(user_plan, 0)
        if user_level < self.min_level:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This feature requires a '{self.minimum_plan}' plan or higher. "
                    f"Your current plan is '{user_plan}'."
                ),
            )
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
