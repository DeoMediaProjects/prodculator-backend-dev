"""Firebase Admin SDK initialisation and ID token verification."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import firebase_admin
from firebase_admin import auth, credentials

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_app: firebase_admin.App | None = None


def _get_firebase_app(settings: Settings | None = None) -> firebase_admin.App:
    """Return the singleton Firebase app, initialising it on first call."""
    global _app
    if _app is not None:
        return _app

    cfg = settings or get_settings()

    if not cfg.FIREBASE_PROJECT_ID:
        raise RuntimeError(
            "FIREBASE_PROJECT_ID is not configured. "
            "Set it in your .env to enable Google auth."
        )

    cred: credentials.Base
    svc = cfg.FIREBASE_SERVICE_ACCOUNT_JSON.strip()

    if svc:
        # Accept either an inline JSON string or a file path
        if svc.startswith("{"):
            cred = credentials.Certificate(json.loads(svc))
        elif os.path.isfile(svc):
            cred = credentials.Certificate(svc)
        else:
            raise RuntimeError(
                "FIREBASE_SERVICE_ACCOUNT_JSON must be a valid file path or inline JSON."
            )
    else:
        # Fall back to Application Default Credentials (useful in GCP / CI environments)
        cred = credentials.ApplicationDefault()

    _app = firebase_admin.initialize_app(
        cred,
        options={"projectId": cfg.FIREBASE_PROJECT_ID},
    )
    logger.info("Firebase Admin SDK initialised for project '%s'", cfg.FIREBASE_PROJECT_ID)
    return _app


def verify_firebase_token(id_token: str, settings: Settings | None = None) -> dict[str, Any]:
    """Verify a Firebase ID token and return the decoded claims.

    Returns a dict with at least: ``uid``, ``email``, ``email_verified``,
    and optionally ``name`` and ``picture``.

    Raises ``ValueError`` on any verification failure.
    """
    app = _get_firebase_app(settings)
    try:
        claims = auth.verify_id_token(id_token, app=app, check_revoked=True)
        return dict(claims)
    except auth.RevokedIdTokenError:
        raise ValueError("Firebase token has been revoked")
    except auth.ExpiredIdTokenError:
        raise ValueError("Firebase token has expired")
    except auth.InvalidIdTokenError as exc:
        raise ValueError(f"Invalid Firebase token: {exc}")
    except Exception as exc:
        raise ValueError(f"Firebase token verification failed: {exc}")
