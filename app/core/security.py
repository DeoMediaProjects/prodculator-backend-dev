from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from authlib.jose import JoseError, jwt
from passlib.context import CryptContext

from app.core.config import Settings, get_settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

_BLOCKLIST_PREFIX = "token_blocklist:"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def _encode(claims: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256"}
    token = jwt.encode(header, claims, secret)
    return token.decode("utf-8") if isinstance(token, bytes) else token


def create_access_token(
    user_id: str,
    user_type: str = "free",
    settings: Settings | None = None,
) -> tuple[str, int]:
    cfg = settings or get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=cfg.JWT_ACCESS_TOKEN_EXPIRES_SECONDS)
    token = _encode(
        {
            "sub": user_id,
            "type": "access",
            "user_type": user_type,
            "jti": str(uuid4()),
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        },
        cfg.JWT_SECRET_KEY,
    )
    return token, cfg.JWT_ACCESS_TOKEN_EXPIRES_SECONDS


def create_refresh_token(
    user_id: str,
    user_type: str = "free",
    settings: Settings | None = None,
) -> str:
    cfg = settings or get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=cfg.JWT_REFRESH_TOKEN_EXPIRES_SECONDS)
    return _encode(
        {
            "sub": user_id,
            "type": "refresh",
            "user_type": user_type,
            "jti": str(uuid4()),
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        },
        cfg.JWT_SECRET_KEY,
    )


def create_verification_token(user_id: str, email: str, settings: Settings | None = None) -> str:
    """Create a 24-hour signed JWT used as the email-verification link token."""
    cfg = settings or get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=24)
    return _encode(
        {
            "sub": user_id,
            "email": email,
            "type": "email_verification",
            "jti": str(uuid4()),
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        },
        cfg.JWT_SECRET_KEY,
    )


def create_password_reset_token(user_id: str, email: str, settings: Settings | None = None) -> str:
    """Create a 1-hour signed JWT used as the password-reset link token."""
    cfg = settings or get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=1)
    return _encode(
        {
            "sub": user_id,
            "email": email,
            "type": "password_reset",
            "jti": str(uuid4()),
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        },
        cfg.JWT_SECRET_KEY,
    )


def decode_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    try:
        claims = jwt.decode(token, cfg.JWT_SECRET_KEY)
        claims.validate()
        return dict(claims)
    except JoseError as exc:
        raise ValueError("Invalid or expired token") from exc


async def revoke_token(token: str, redis_client: Any, settings: Settings | None = None) -> None:
    """Add a token's jti to the Redis blocklist with TTL matching its remaining lifetime."""
    claims = decode_token(token, settings)
    jti = claims.get("jti")
    if not jti:
        return
    exp = claims.get("exp", 0)
    ttl = max(1, exp - int(datetime.now(timezone.utc).timestamp()))
    await redis_client.setex(f"{_BLOCKLIST_PREFIX}{jti}", ttl, "1")


async def is_token_revoked(claims: dict[str, Any], redis_client: Any) -> bool:
    """Return True if the token's jti has been revoked."""
    jti = claims.get("jti")
    if not jti:
        return False
    result = await redis_client.get(f"{_BLOCKLIST_PREFIX}{jti}")
    return result is not None
