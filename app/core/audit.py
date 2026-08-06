"""Admin audit logging (handoff §4.4/§4.5).

The write path is deliberately at the router layer, not in the endpoints. Every
admin router sets ``route_class = AuditedAPIRoute``, so a mutating admin request
is recorded because it *reached the router*, not because someone remembered to
add a call. Adding a new admin endpoint therefore cannot silently escape the
audit trail, and there is no per-endpoint code to review for coverage.

Three properties matter more than completeness of detail:

1. **Unforgettable.** Coverage follows from router registration. The only way
   to add an unaudited admin mutation is to register a router without the route
   class, which ``tests/test_admin_audit.py`` asserts against for every router
   mounted under ``/api/admin``.
2. **Non-blocking.** An audit failure must never fail the admin action, and an
   admin action rolling back must never take its audit row with it. The row is
   written in its own session, after the response is produced, and any error is
   logged at ERROR rather than raised.
3. **Honest about failures.** A mutation that 4xx/5xx'd is still recorded, with
   the status and error, so "nothing happened" and "someone tried and it broke"
   are distinguishable after the fact.

Retention: rows older than ``settings.ADMIN_AUDIT_RETENTION_DAYS`` are purged by
the daily scheduler job. The default is 730 days (two years) — long enough to
cover an annual review cycle plus the year it audits, which is the horizon that
matters for a service handling payment and personal data. Set the value to 0 to
retain indefinitely.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# Methods that change state. GET/HEAD/OPTIONS are reads and are not recorded —
# the table would fill with dashboard polling and bury the mutations.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Largest JSON blob stored per side. A payload beyond this is replaced with a
# marker rather than truncated into invalid JSON, so the row stays readable.
MAX_JSON_CHARS = 20_000

# Keys whose values never reach the audit table, at any nesting depth. Matched
# case-insensitively as substrings, so `stripe_secret_key` and `newPassword`
# are both caught.
_REDACT_KEY_TOKENS = (
    "password", "token", "secret", "api_key", "apikey", "authorization",
    "credential", "private_key", "signature", "webhook_secret",
)
_REDACTED = "[redacted]"

# Path segment → (resource_type, table). The table is only used to read the
# before-state; an unmapped resource still produces a complete audit row, just
# without a fetched before-state. Keys are matched against the path immediately
# after /api/admin, longest first, so two-segment prefixes win.
RESOURCE_MAP: dict[str, tuple[str, str | None]] = {
    "incentives": ("incentive", "incentive_programs"),
    "festivals": ("festival", "film_festivals"),
    "distributors": ("distributor", "distributors"),
    "grants": ("grant", "grant_opportunities"),
    "comparables": ("comparable", "comparable_productions"),
    "territory-profiles": ("territory_profile", "territory_profiles"),
    "crew-depth": ("territory_profile", "territory_profiles"),
    "users": ("user", "users"),
    "admin-users": ("admin_user", "admins"),
    "subscribers": ("subscriber", "users"),
    "email-gating": ("email_gating_record", "email_gating_records"),
    "data-sources": ("data_source", "data_sources"),
    "reports": ("report", "reports"),
    "pdf-reports": ("pdf_report", "reports"),
    "b2b/subscriptions": ("b2b_subscription", "b2b_subscriptions"),
    "b2b/entitlements": ("b2b_entitlement", "b2b_client_entitlements"),
    "b2b/requests": ("b2b_request", "b2b_intelligence_requests"),
    "b2b/templates": ("b2b_package_template", "b2b_package_templates"),
    "b2b/invites": ("b2b_invite", "b2b_contract_invites"),
    "b2b/signals": ("production_signal", "production_signals"),
    "b2b": ("b2b", None),
    "emails": ("email", None),
}

# Path tails that name the action themselves. Without these, POST
# /b2b/subscriptions/{id}/hold would be recorded as "create.b2b_subscription",
# which is both wrong and indistinguishable from an actual creation.
_VERB_TAILS = frozenset({
    "hold", "unhold", "resume", "block", "unblock", "cancel", "reactivate",
    "resend", "revoke", "issue", "approve", "reject", "sync", "sync-tmdb",
    "refresh", "test", "run", "reissue-pdf", "deliver", "generate", "send",
    "purge", "close", "verify", "unverify", "upgrade", "downgrade", "impersonate",
})

_METHOD_VERBS = {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}

# Path-param names that identify the resource, in priority order.
_ID_PARAM_NAMES = (
    "item_id", "resource_id", "record_id", "id", "user_id", "admin_id",
    "report_id", "subscription_id", "entitlement_id", "request_id",
    "template_id", "invite_id", "territory", "source_id", "slug",
)


# ── Redaction and size limits ────────────────────────────────────────────────

def _is_secret_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(token in lowered for token in _REDACT_KEY_TOKENS)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively replace secret-looking values. Depth-capped so a cyclic or
    pathologically nested payload cannot blow the stack on the audit path."""
    if _depth > 12:
        return "[too deeply nested]"
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _is_secret_key(key) else redact(item, _depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth + 1) for item in value]
    return value


def _fit(value: Any) -> Any:
    """Return *value* if it serialises within MAX_JSON_CHARS, else a marker.

    Storing a partial blob would be worse than storing none: it reads as the
    whole state while being silently incomplete.
    """
    if value is None:
        return None
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return {"_audit_note": "value could not be serialised for the audit log"}
    if len(encoded) > MAX_JSON_CHARS:
        return {
            "_audit_note": (
                f"omitted — {len(encoded)} chars exceeds the "
                f"{MAX_JSON_CHARS}-char audit limit"
            )
        }
    # Round-trip so what is stored is exactly what json.dumps produced (dates
    # and Decimals become strings rather than failing at DB write time).
    return json.loads(encoded)


# ── Path → action / resource resolution ──────────────────────────────────────

def _admin_path_segments(path: str) -> list[str]:
    """Segments after /api/admin, with the prefix and empties removed."""
    trimmed = path.split("?", 1)[0].strip("/")
    parts = [p for p in trimmed.split("/") if p]
    if parts[:2] == ["api", "admin"]:
        parts = parts[2:]
    return parts


def resolve_resource(path: str) -> tuple[str, str | None]:
    """Return ``(resource_type, table_or_None)`` for an admin path.

    Falls back to the first path segment as the resource type, so a new admin
    router is described reasonably before anyone adds it to RESOURCE_MAP.
    """
    segments = _admin_path_segments(path)
    if not segments:
        return "admin", None
    two = "/".join(segments[:2])
    if two in RESOURCE_MAP:
        return RESOURCE_MAP[two]
    if segments[0] in RESOURCE_MAP:
        return RESOURCE_MAP[segments[0]]
    return segments[0].replace("-", "_"), None


def resolve_action(method: str, path: str, resource_type: str) -> str:
    """Return a stable ``verb.resource`` action string."""
    segments = _admin_path_segments(path)
    tail = segments[-1] if segments else ""
    verb = tail if tail in _VERB_TAILS else _METHOD_VERBS.get(method.upper(), method.lower())
    return f"{verb}.{resource_type}"


def resolve_resource_id(request: Request) -> str | None:
    """Pick the path parameter that identifies the resource."""
    params = getattr(request, "path_params", None) or {}
    for name in _ID_PARAM_NAMES:
        value = params.get(name)
        if value not in (None, ""):
            return str(value)
    # Any remaining single param is better than nothing.
    for name, value in params.items():
        if value not in (None, "") and not name.startswith("_"):
            return str(value)
    return None


def client_ip(request: Request) -> str | None:
    """Best-effort client IP, preferring the proxy header the app runs behind.

    Only the left-most entry of X-Forwarded-For is meaningful here, and it is
    client-supplied — recorded as an indicator, never trusted for access
    decisions.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:100]
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()[:100]
    return request.client.host if request.client else None


# ── The write ────────────────────────────────────────────────────────────────

def record_audit_log(
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    actor_id: str | None = None,
    actor_email: str | None = None,
    actor_role: str | None = None,
    before: Any = None,
    after: Any = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    error_message: str | None = None,
) -> str | None:
    """Write one audit row in its own session. Returns the row id, or None if
    the write failed.

    Never raises. An audit failure is an operational problem to be alerted on,
    not a reason to fail the admin action that was already carried out — but it
    is logged at ERROR so it cannot pass unnoticed.
    """
    from app.core.db import get_db_context
    from app.models.sql_models import AdminAuditLog

    row = AdminAuditLog(
        actor_id=actor_id,
        actor_email=actor_email,
        actor_role=actor_role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_json=_fit(redact(before)),
        after_json=_fit(redact(after)),
        method=method,
        path=path,
        status_code=status_code,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:500] or None,
        error_message=(error_message or "")[:1000] or None,
        created_at=datetime.now(timezone.utc),
    )
    # Read the id before the session closes. Touching row.id afterwards raises
    # DetachedInstanceError, which the except below would then report as a
    # failed write — so every successful audit write logged an ERROR.
    row_id = row.id
    try:
        with get_db_context() as session:
            session.add(row)
            session.commit()
        return row_id
    except Exception:
        # The audit trail is the thing that cannot be reconstructed later, so
        # this is an ERROR even though the request itself succeeded.
        logger.error(
            "AUDIT WRITE FAILED — admin action was not recorded: "
            "action=%s resource=%s/%s actor=%s status=%s",
            action, resource_type, resource_id, actor_email or actor_id,
            status_code,
            exc_info=True,
        )
        return None


def _fetch_before_state(table: str | None, resource_id: str | None) -> Any:
    """Read the current row for the resource about to change.

    Best effort by design: a missing table, a non-``id`` primary key or a
    permission problem yields None rather than blocking the request.
    """
    if not table or not resource_id:
        return None
    from app.core.database_client import DatabaseClient
    from app.core.db import get_db_context

    try:
        with get_db_context() as session:
            db = DatabaseClient(session)
            result = (
                db.table(table).select("*").eq("id", resource_id).single().execute()
            )
            return result.data or None
    except Exception:
        logger.debug(
            "Audit before-state unavailable: table=%s id=%s", table, resource_id,
            exc_info=True,
        )
        return None


def _parse_body(raw: bytes | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        # Multipart uploads and form posts land here. The fact of the mutation
        # is still recorded; only the shape of its payload is not.
        return {"_audit_note": "request body was not JSON"}


class AuditActor:
    """Minimal actor for endpoints that authenticate without ``get_current_admin``.

    Admin sign-in is the case that matters: it has no admin dependency (that is
    the point), so without this the successful login would be recorded with no
    actor at all.
    """

    __slots__ = ("id", "email", "role")

    def __init__(self, *, id: str | None = None, email: str | None = None, role: str | None = None):
        self.id = id
        self.email = email
        self.role = role


def set_audit_actor(
    request: Request, *, id: str | None = None, email: str | None = None, role: str | None = None,
) -> None:
    """Attribute this request to an actor the audit route class cannot resolve."""
    request.state.audit_actor = AuditActor(id=id, email=email, role=role)


def _actor_from_request(request: Request) -> tuple[str | None, str | None, str | None]:
    """Read the admin the auth dependency resolved for this request."""
    actor = getattr(request.state, "audit_actor", None)
    if actor is None:
        return None, None, None
    return (
        getattr(actor, "id", None),
        getattr(actor, "email", None),
        getattr(actor, "role", None),
    )


class AuditedAPIRoute(APIRoute):
    """Route class that records every mutating request through it.

    Applied per router via ``router.route_class = AuditedAPIRoute`` (which must
    be set before any endpoint is declared on that router) rather than per
    endpoint, so coverage cannot be forgotten.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def audited_handler(request: Request) -> Response:
            if request.method.upper() not in MUTATING_METHODS:
                return await original(request)

            resource_type, table = resolve_resource(request.url.path)
            action = resolve_action(request.method, request.url.path, resource_type)
            resource_id = resolve_resource_id(request)

            # Read the body before the endpoint does. Starlette caches it on the
            # request, so the endpoint still receives it.
            try:
                submitted = _parse_body(await request.body())
            except Exception:
                submitted = None

            before = _fetch_before_state(table, resource_id)

            status_code: int | None = None
            error_message: str | None = None
            after: Any = submitted
            try:
                response = await original(request)
            except StarletteHTTPException as exc:
                status_code = exc.status_code
                error_message = str(exc.detail)
                self._write(
                    request, action, resource_type, resource_id, before, submitted,
                    status_code, error_message,
                )
                raise
            except Exception as exc:
                status_code = 500
                error_message = f"{type(exc).__name__}: {exc}"
                self._write(
                    request, action, resource_type, resource_id, before, submitted,
                    status_code, error_message,
                )
                raise

            status_code = response.status_code
            # Prefer the response body as the after-state: for the CRUD
            # endpoints it is the persisted row, which is more truthful than the
            # payload that was asked for.
            persisted = self._response_json(response)
            if persisted is not None:
                after = persisted
            if status_code >= 400:
                error_message = self._error_detail(persisted)

            self._write(
                request, action, resource_type, resource_id, before, after,
                status_code, error_message,
            )
            return response

        return audited_handler

    @staticmethod
    def _response_json(response: Response) -> Any:
        """Decode a JSON response body, or None for streams and non-JSON."""
        body = getattr(response, "body", None)
        if not body or not isinstance(body, (bytes, bytearray)):
            return None
        media = (response.media_type or "").lower()
        if media and "json" not in media:
            return None
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None

    @staticmethod
    def _error_detail(payload: Any) -> str | None:
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if detail is not None:
                return str(detail)[:1000]
        return None

    @staticmethod
    def _write(
        request: Request,
        action: str,
        resource_type: str,
        resource_id: str | None,
        before: Any,
        after: Any,
        status_code: int | None,
        error_message: str | None,
    ) -> None:
        # record_audit_log swallows DB failures, but everything around it here
        # (actor read, IP parsing, header access) could still throw, and by this
        # point the admin action has already happened. Nothing in the audit path
        # may turn a completed action into a failed response.
        try:
            actor_id, actor_email, actor_role = _actor_from_request(request)

            # 401 means no identity was established, so there is no admin action
            # to attribute — and recording them would let an unauthenticated
            # caller fill the audit table at will. 403 (authenticated but not
            # permitted) is recorded: a real admin reaching for something they
            # do not hold is exactly what an audit trail is for.
            if status_code == 401 and actor_id is None:
                return

            record_audit_log(
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                actor_id=actor_id,
                actor_email=actor_email,
                actor_role=actor_role,
                before=before,
                after=after,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                ip_address=client_ip(request),
                user_agent=request.headers.get("user-agent"),
                error_message=error_message,
            )
        except Exception:
            logger.error(
                "AUDIT WRITE FAILED before reaching the database — admin action "
                "was not recorded: action=%s resource=%s/%s status=%s",
                action, resource_type, resource_id, status_code, exc_info=True,
            )


# ── Retention ────────────────────────────────────────────────────────────────

def purge_expired_audit_logs(retention_days: int) -> int:
    """Delete audit rows older than *retention_days*. Returns rows removed.

    A retention_days of 0 (or less) means retain indefinitely and purges
    nothing — the safe reading of an unset value.
    """
    if retention_days <= 0:
        return 0

    from sqlalchemy import delete

    from app.core.db import get_db_context
    from app.models.sql_models import AdminAuditLog

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    with get_db_context() as session:
        result = session.execute(
            delete(AdminAuditLog).where(AdminAuditLog.created_at < cutoff)
        )
        session.commit()
        removed = result.rowcount or 0
    if removed:
        logger.info(
            "Audit retention: purged %d row(s) older than %s (%d-day retention)",
            removed, cutoff.date().isoformat(), retention_days,
        )
    return removed
