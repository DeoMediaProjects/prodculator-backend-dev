"""B2B client entitlement registry (SOW 4.4).

Records what each client is contractually owed and, critically, which modules
are licensed to them EXCLUSIVELY and until when. The contract pack's worked
example is Grey Consortium UK holding the "AI Usage Module" exclusively with a
reversion date of 2028-06-30.

Two jobs:

  1. **Exclusivity enforcement.** While a module is exclusive to subscription A
     and its reversion date has not passed, no OTHER client's package may
     include the sections that module covers. Breaching this is a contractual
     problem, not a cosmetic one, so composition REFUSES rather than silently
     dropping the section -- an admin who asked for it must be told why they
     cannot have it.
  2. **Ad-hoc request scoping.** SOW 4.4 allows client ad-hoc requests "within
     entitlement"; `allowed_section_keys` is what that phrase resolves to.

A module with no `section_keys` is contracted but not yet built. The row still
records the obligation; it simply has nothing to enforce against yet.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


class EntitlementConflict(Exception):
    """Raised when a composition would breach another client's exclusivity."""

    def __init__(self, conflicts: list[dict[str, Any]]):
        self.conflicts = conflicts
        details = "; ".join(
            f"{c['section_key']} is exclusive to {c['held_by_subscription_id']}"
            f"{' until ' + c['reverts_at'] if c.get('reverts_at') else ' (perpetual)'}"
            for c in conflicts
        )
        super().__init__(f"Section(s) unavailable due to client exclusivity: {details}")


class EntitlementService:
    TABLE = "b2b_client_entitlements"

    def __init__(self, db: Any):
        self.db = db

    # ------------------------------------------------------------------ reads

    def list_all(self) -> list[dict[str, Any]]:
        return self.db.table(self.TABLE).select("*").execute().data or []

    def list_for_subscription(self, subscription_id: str) -> list[dict[str, Any]]:
        return (
            self.db.table(self.TABLE)
            .select("*")
            .eq("b2b_subscription_id", subscription_id)
            .execute()
            .data
            or []
        )

    def get(self, entitlement_id: str) -> dict[str, Any] | None:
        rows = self.db.table(self.TABLE).select("*").eq("id", entitlement_id).execute().data or []
        return rows[0] if rows else None

    # ------------------------------------------------------------ exclusivity

    @staticmethod
    def is_in_force(row: dict[str, Any], on_date: date) -> bool:
        """Exclusive, and not yet reverted.

        The reversion date is the date the module becomes generally available,
        so exclusivity holds up to but NOT including it.
        """
        if not row.get("is_exclusive"):
            return False
        reverts_at = _as_date(row.get("reverts_at"))
        if reverts_at is None:
            return True  # perpetual
        return on_date < reverts_at

    def exclusive_section_holders(self, on_date: date | None = None) -> dict[str, dict[str, Any]]:
        """Map each exclusively-held section key to the entitlement holding it."""
        on_date = on_date or _utcnow().date()
        holders: dict[str, dict[str, Any]] = {}
        for row in self.list_all():
            if not self.is_in_force(row, on_date):
                continue
            for section_key in row.get("section_keys") or []:
                holders[section_key] = row
        return holders

    def conflicts_for(
        self,
        *,
        subscription_id: str | None,
        section_keys: list[str],
        on_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Which requested sections are exclusively held by a DIFFERENT client.

        A subscription is never in conflict with its own exclusivity. A
        composition with no subscription (an internal/admin one-off) is still
        blocked, because the output could reach a third party.
        """
        on_date = on_date or _utcnow().date()
        holders = self.exclusive_section_holders(on_date)
        conflicts: list[dict[str, Any]] = []
        for section_key in section_keys:
            holder = holders.get(section_key)
            if not holder:
                continue
            if subscription_id and holder.get("b2b_subscription_id") == subscription_id:
                continue
            reverts_at = _as_date(holder.get("reverts_at"))
            conflicts.append(
                {
                    "section_key": section_key,
                    "module_key": holder.get("module_key"),
                    "module_label": holder.get("module_label"),
                    "held_by_subscription_id": holder.get("b2b_subscription_id"),
                    "reverts_at": reverts_at.isoformat() if reverts_at else None,
                }
            )
        return conflicts

    def assert_available(
        self,
        *,
        subscription_id: str | None,
        section_keys: list[str],
        on_date: date | None = None,
    ) -> None:
        conflicts = self.conflicts_for(
            subscription_id=subscription_id, section_keys=section_keys, on_date=on_date
        )
        if conflicts:
            logger.warning(
                "Blocked B2B composition for subscription=%s on exclusivity: %s",
                subscription_id,
                [c["section_key"] for c in conflicts],
            )
            raise EntitlementConflict(conflicts)

    def allowed_section_keys(
        self,
        *,
        subscription_id: str | None,
        section_keys: list[str],
        on_date: date | None = None,
    ) -> list[str]:
        """The requested sections this client may actually receive."""
        blocked = {
            c["section_key"]
            for c in self.conflicts_for(
                subscription_id=subscription_id, section_keys=section_keys, on_date=on_date
            )
        }
        return [key for key in section_keys if key not in blocked]

    # ----------------------------------------------------------------- writes

    def grant(
        self,
        *,
        subscription_id: str,
        module_key: str,
        module_label: str | None = None,
        section_keys: list[str] | None = None,
        is_exclusive: bool = False,
        reverts_at: date | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Create or update an entitlement. Idempotent per (subscription, module)."""
        existing = [
            row
            for row in self.list_for_subscription(subscription_id)
            if row.get("module_key") == module_key
        ]
        record: dict[str, Any] = {
            "id": existing[0]["id"] if existing else str(uuid4()),
            "b2b_subscription_id": subscription_id,
            "module_key": module_key,
            "module_label": module_label,
            "section_keys": section_keys or [],
            "is_exclusive": is_exclusive,
            "reverts_at": reverts_at,
            "notes": notes,
            "updated_at": _utcnow(),
        }
        # Reflected tables carry no Python-side defaults, so created_at is set
        # explicitly and only on first write.
        if not existing:
            record["created_at"] = _utcnow()
        result = (
            self.db.table(self.TABLE)
            .upsert(record, on_conflict="b2b_subscription_id,module_key")
            .execute()
        )
        return (result.data or [record])[0]

    def revoke(self, entitlement_id: str) -> bool:
        if not self.get(entitlement_id):
            return False
        self.db.table(self.TABLE).delete().eq("id", entitlement_id).execute()
        return True
