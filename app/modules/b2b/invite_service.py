"""Manual-contract invite flow (handoff §4.3/§4.4).

A contracted Business Intelligence client used to have to sign up first and then
be provisioned by hand — ``create_manual_subscription`` refuses outright until a
user row exists. This module removes that step: an admin issues an invite against
an email address, the client signs in and claims it, and the subscription is
created and linked to the invite on claim.

**Token design.** The token is 32 bytes of ``secrets`` randomness, URL-safe, and
only its SHA-256 is stored. That is deliberately not a self-describing signed
token: this flow must support revoke and single-use, both of which need a
database row consulted on every use, so signing would add key management without
removing a lookup. Storing the hash means a leaked database yields no usable
links, and the raw token exists exactly once — in the issuing response and in the
client's email.

**Claim binding.** The signed-in user's email must match the invited address.
An invite is a contractual instrument tied to a named counterparty, and a link
that any signed-in account could redeem would let a forwarded email transfer a
paid entitlement. The refusal names the invited address so a client who signed up
under a different one knows what to do.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

TABLE = "b2b_contract_invites"

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REVOKED = "revoked"
# Derived, never stored: an invite is expired when the clock says so, so a
# status column can never disagree with it.
STATUS_EXPIRED = "expired"

DEFAULT_EXPIRY_DAYS = 30
MAX_EXPIRY_DAYS = 365
TOKEN_BYTES = 32
TOKEN_PREFIX_CHARS = 8


class InviteError(Exception):
    """Base for invite problems that map to a client-visible response."""


class InviteNotFound(InviteError):
    """No invite matches the token."""


class InviteNotClaimable(InviteError):
    """The invite exists but cannot be claimed (expired, revoked, or accepted)."""

    def __init__(self, message: str, *, status: str):
        super().__init__(message)
        self.status = status


class InviteEmailMismatch(InviteError):
    """The signed-in user is not the invited counterparty."""

    def __init__(self, message: str, *, invited_email: str):
        super().__init__(message)
        self.invited_email = invited_email


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any) -> datetime | None:
    """Coerce a stored timestamp to an aware datetime.

    Rows come back from the DB layer as datetimes, but SQLite-backed tests can
    yield naive ones and JSON round-trips yield strings. A naive value is read as
    UTC, which is what every writer in this module stores.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_token() -> tuple[str, str, str]:
    """Return ``(raw_token, token_hash, token_prefix)``."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    return raw, hash_token(raw), raw[:TOKEN_PREFIX_CHARS]


class B2BInviteService:
    def __init__(self, db: Any, settings: Any = None, email_service: Any = None):
        self.db = db
        self.settings = settings
        self._email_service = email_service

    # ── Helpers ──────────────────────────────────────────────────────────────

    @property
    def email_service(self):
        if self._email_service is None:
            from app.modules.email.service import EmailService

            self._email_service = EmailService(self.settings)
        return self._email_service

    def accept_url(self, token: str) -> str:
        base = (getattr(self.settings, "FRONTEND_URL", "") or "").rstrip("/")
        return f"{base}/b2b/invite/{token}"

    @staticmethod
    def effective_status(row: dict[str, Any], *, now: datetime | None = None) -> str:
        """The invite's status with expiry applied.

        Expiry is computed rather than stored, so an invite cannot appear
        claimable merely because no job has run to age it.
        """
        stored = row.get("status") or STATUS_PENDING
        if stored != STATUS_PENDING:
            return stored
        expires_at = _as_datetime(row.get("expires_at"))
        if expires_at and expires_at <= (now or _utcnow()):
            return STATUS_EXPIRED
        return STATUS_PENDING

    def to_api(self, row: dict[str, Any]) -> dict[str, Any]:
        """Admin-facing view. Never includes the token or its hash."""
        return {
            "id": row.get("id"),
            "email": row.get("email"),
            "product_type": row.get("product_type"),
            "status": self.effective_status(row),
            "token_prefix": row.get("token_prefix"),
            "expires_at": row.get("expires_at"),
            "delivery_frequency": row.get("delivery_frequency"),
            "extra_recipient_email": row.get("extra_recipient_email"),
            "company_name": row.get("company_name"),
            "admin_notes": row.get("admin_notes"),
            "created_by": row.get("created_by"),
            "sent_count": row.get("sent_count") or 0,
            "last_sent_at": row.get("last_sent_at"),
            "accepted_at": row.get("accepted_at"),
            "accepted_by_user_id": row.get("accepted_by_user_id"),
            "b2b_subscription_id": row.get("b2b_subscription_id"),
            "revoked_at": row.get("revoked_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    # ── Reads ────────────────────────────────────────────────────────────────

    def get(self, invite_id: str) -> dict[str, Any] | None:
        rows = self.db.table(TABLE).select("*").eq("id", invite_id).execute().data or []
        return rows[0] if rows else None

    def get_by_token(self, token: str) -> dict[str, Any] | None:
        rows = (
            self.db.table(TABLE)
            .select("*")
            .eq("token_hash", hash_token(token))
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None

    def list_invites(
        self,
        *,
        status: str | None = None,
        email: str | None = None,
        product_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self.db.table(TABLE).select("*").execute().data or []

        # Status is filtered in Python because the meaningful value is derived
        # (an invite past its expiry reads as expired while still stored
        # pending), so a SQL predicate on the column would give a wrong answer.
        if email:
            needle = email.strip().lower()
            rows = [r for r in rows if needle in (r.get("email") or "").lower()]
        if product_type:
            rows = [r for r in rows if r.get("product_type") == product_type]
        if status:
            rows = [r for r in rows if self.effective_status(r) == status]

        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        total = len(rows)
        return [self.to_api(r) for r in rows[offset : offset + limit]], total

    def pending_for(self, email: str, product_type: str) -> dict[str, Any] | None:
        """An outstanding, claimable invite for this address and product."""
        needle = email.strip().lower()
        for row in self.db.table(TABLE).select("*").execute().data or []:
            if (row.get("email") or "").lower() != needle:
                continue
            if row.get("product_type") != product_type:
                continue
            if self.effective_status(row) == STATUS_PENDING:
                return row
        return None

    # ── Issue / resend / revoke ──────────────────────────────────────────────

    def issue(
        self,
        *,
        email: str,
        product_type: str,
        delivery_frequency: str = "monthly",
        extra_recipient_email: str | None = None,
        company_name: str | None = None,
        admin_notes: str | None = None,
        expires_in_days: int = DEFAULT_EXPIRY_DAYS,
        created_by: str | None = None,
        send_email: bool = True,
    ) -> tuple[dict[str, Any], str]:
        """Mint an invite. Returns ``(invite_row_api, accept_url)``.

        The accept URL contains the only copy of the raw token the system will
        ever produce for this invite; it is returned so an admin can pass the
        link on directly when email delivery is not an option.
        """
        email = (email or "").strip().lower()
        if not email:
            raise InviteError("An email address is required to issue an invite")
        if not 1 <= expires_in_days <= MAX_EXPIRY_DAYS:
            raise InviteError(
                f"expires_in_days must be between 1 and {MAX_EXPIRY_DAYS}"
            )

        existing = self.pending_for(email, product_type)
        if existing:
            raise InviteError(
                f"{email} already has an outstanding invite for this product. "
                f"Resend or revoke it instead of issuing a second one."
            )

        raw, token_hash, prefix = mint_token()
        now = _utcnow()
        row = {
            "id": str(uuid4()),
            "email": email,
            "product_type": product_type,
            "status": STATUS_PENDING,
            "token_hash": token_hash,
            "token_prefix": prefix,
            "expires_at": now + timedelta(days=expires_in_days),
            "delivery_frequency": delivery_frequency or "monthly",
            "extra_recipient_email": (extra_recipient_email or "").strip().lower() or None,
            "company_name": company_name,
            "admin_notes": admin_notes,
            "created_by": created_by,
            "sent_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        result = self.db.table(TABLE).insert(row).execute()
        stored = (result.data or [row])[0]

        url = self.accept_url(raw)
        if send_email:
            self._send_invite_email(stored, url)
        logger.info(
            "B2B contract invite issued: id=%s email=%s product=%s expires=%s",
            stored.get("id"), email, product_type, row["expires_at"],
        )
        return self.to_api(stored), url

    def resend(self, invite_id: str) -> tuple[dict[str, Any], str]:
        """Rotate the token and email the invite again.

        Rotating rather than resending the same token means a link that went to
        the wrong place (a mistyped address, a forwarded mailbox) stops working
        the moment the invite is resent, which is the reason an admin resends.
        """
        row = self.get(invite_id)
        if not row:
            raise InviteNotFound("Invite not found")
        status = self.effective_status(row)
        if status in (STATUS_ACCEPTED, STATUS_REVOKED):
            raise InviteNotClaimable(
                f"This invite is {status} and cannot be resent.", status=status,
            )

        raw, token_hash, prefix = mint_token()
        now = _utcnow()
        updates = {
            "token_hash": token_hash,
            "token_prefix": prefix,
            # Resending an expired invite revives it, which is what an admin
            # means by resending one.
            "status": STATUS_PENDING,
            "expires_at": now + timedelta(days=DEFAULT_EXPIRY_DAYS),
            "sent_count": (row.get("sent_count") or 0) + 1,
            "last_sent_at": now,
            "updated_at": now,
        }
        result = self.db.table(TABLE).update(updates).eq("id", invite_id).execute()
        stored = (result.data or [{**row, **updates}])[0]
        if not isinstance(stored, dict) or "email" not in stored:
            stored = {**row, **updates}

        url = self.accept_url(raw)
        self._send_invite_email(stored, url)
        logger.info("B2B contract invite resent: id=%s email=%s", invite_id, stored.get("email"))
        return self.to_api(stored), url

    def revoke(self, invite_id: str) -> dict[str, Any]:
        row = self.get(invite_id)
        if not row:
            raise InviteNotFound("Invite not found")
        if self.effective_status(row) == STATUS_ACCEPTED:
            # Revoking would not undo the subscription it created, so refusing
            # is the honest answer: cancel the subscription instead.
            raise InviteNotClaimable(
                "This invite has already been accepted. Cancel the subscription "
                "it created rather than revoking the invite.",
                status=STATUS_ACCEPTED,
            )
        now = _utcnow()
        updates = {"status": STATUS_REVOKED, "revoked_at": now, "updated_at": now}
        self.db.table(TABLE).update(updates).eq("id", invite_id).execute()
        logger.info("B2B contract invite revoked: id=%s", invite_id)
        return self.to_api({**row, **updates})

    # ── Public preview / claim ───────────────────────────────────────────────

    def preview(self, token: str) -> dict[str, Any]:
        """What this invite offers, for the unauthenticated accept page.

        Deliberately thin: the product, the company, the invited address and the
        expiry. It is reachable by anyone holding the link, so it carries nothing
        that is not already in the email that delivered it.
        """
        row = self.get_by_token(token)
        if not row:
            raise InviteNotFound("This invitation link is not valid")
        status = self.effective_status(row)
        return {
            "email": row.get("email"),
            "product_type": row.get("product_type"),
            "company_name": row.get("company_name"),
            "delivery_frequency": row.get("delivery_frequency"),
            "expires_at": row.get("expires_at"),
            "status": status,
            "claimable": status == STATUS_PENDING,
        }

    def accept(
        self,
        *,
        token: str,
        user_id: str,
        user_email: str,
        b2b_service: Any,
    ) -> dict[str, Any]:
        """Bind the invite to *user_id* and create the subscription.

        Returns the created (or previously created) subscription row. Ordering
        matters: the subscription is created first and the invite is only marked
        accepted once that succeeded, so a failure leaves a claimable invite
        rather than a consumed one with nothing to show for it.
        """
        row = self.get_by_token(token)
        if not row:
            raise InviteNotFound("This invitation link is not valid")

        status = self.effective_status(row)

        if status == STATUS_ACCEPTED:
            # Idempotent for the original claimant: a double-submit or a
            # refreshed accept page returns the same subscription rather than a
            # second one.
            if row.get("accepted_by_user_id") == user_id:
                existing = b2b_service.get_subscription(row.get("b2b_subscription_id"))
                if existing:
                    return existing
            raise InviteNotClaimable(
                "This invitation has already been used.", status=status,
            )
        if status == STATUS_REVOKED:
            raise InviteNotClaimable(
                "This invitation has been revoked. Contact your account manager.",
                status=status,
            )
        if status == STATUS_EXPIRED:
            raise InviteNotClaimable(
                "This invitation has expired. Ask your account manager to resend it.",
                status=status,
            )

        invited = (row.get("email") or "").strip().lower()
        if invited and invited != (user_email or "").strip().lower():
            raise InviteEmailMismatch(
                f"This invitation was issued to {invited}. Sign in with that "
                f"address to claim it, or ask your account manager to reissue it "
                f"to the address you use.",
                invited_email=invited,
            )

        subscription = b2b_service.create_manual_subscription_for_user(
            user_id=user_id,
            product_type=row["product_type"],
            delivery_frequency=row.get("delivery_frequency") or "monthly",
            extra_recipient_email=row.get("extra_recipient_email"),
            company_name=row.get("company_name"),
            admin_notes=row.get("admin_notes"),
        )

        now = _utcnow()
        updates = {
            "status": STATUS_ACCEPTED,
            "accepted_at": now,
            "accepted_by_user_id": user_id,
            "b2b_subscription_id": subscription["id"],
            "updated_at": now,
        }
        self.db.table(TABLE).update(updates).eq("id", row["id"]).execute()
        logger.info(
            "B2B contract invite accepted: id=%s user_id=%s subscription=%s",
            row["id"], user_id, subscription["id"],
        )
        return subscription

    # ── Email ────────────────────────────────────────────────────────────────

    def _send_invite_email(self, row: dict[str, Any], accept_url: str) -> None:
        """Best-effort send. Never raises: the invite exists either way, and the
        admin holds the accept URL, so a mail failure must not lose the invite."""
        from app.modules.b2b.service import B2B_PRODUCTS

        product = B2B_PRODUCTS.get(row.get("product_type") or "", {})
        try:
            self.email_service.send(
                row["email"],
                "b2b_contract_invite",
                {
                    "product_title": product.get("title") or row.get("product_type"),
                    "product_description": product.get("description"),
                    "company_name": row.get("company_name"),
                    "delivery_frequency": row.get("delivery_frequency"),
                    "accept_url": accept_url,
                    "expires_at": str(row.get("expires_at") or "")[:10],
                },
            )
        except Exception:
            logger.warning(
                "Failed to email B2B contract invite to %s — the invite is still "
                "valid and the accept link was returned to the issuing admin",
                row.get("email"), exc_info=True,
            )
