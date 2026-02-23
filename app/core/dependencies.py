from fastapi import Depends, HTTPException, Header
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.db import get_db
from app.modules.auth.schemas import AuthUser


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
    authorization: str = Header(..., description="Bearer <access_token>"),
    supabase: DatabaseClient = Depends(get_supabase),
) -> AuthUser:
    """Extract and verify JWT, return user profile."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization.removeprefix("Bearer ")

    try:
        user_response = supabase.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if not user_response or not user_response.user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

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


async def get_optional_user(
    authorization: str | None = Header(None),
    supabase: DatabaseClient = Depends(get_supabase),
) -> AuthUser | None:
    """Optional auth; returns None if no token provided."""
    if not authorization:
        return None
    try:
        return await get_current_user(authorization, supabase)
    except HTTPException:
        return None
