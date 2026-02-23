from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from authlib.jose import JoseError, jwt
from passlib.context import CryptContext

from app.core.config import Settings, get_settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def _encode(claims: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256"}
    token = jwt.encode(header, claims, secret)
    return token.decode("utf-8") if isinstance(token, bytes) else token


def create_access_token(user_id: str, settings: Settings | None = None) -> tuple[str, int]:
    cfg = settings or get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=cfg.JWT_ACCESS_TOKEN_EXPIRES_SECONDS)
    token = _encode(
        {
            "sub": user_id,
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        },
        cfg.JWT_SECRET_KEY,
    )
    return token, cfg.JWT_ACCESS_TOKEN_EXPIRES_SECONDS


def create_refresh_token(user_id: str, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=cfg.JWT_REFRESH_TOKEN_EXPIRES_SECONDS)
    return _encode(
        {
            "sub": user_id,
            "type": "refresh",
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
