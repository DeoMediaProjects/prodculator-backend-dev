"""Support services for the admin package composer (SOW 4.4 / 4.5).

Two concerns, both of which exist so an admin can compose and govern a package
without touching the database by hand:

  1. **PackageTemplateService** -- saved section compositions. The five standard
     products keep their canonical layouts in package_service.PRODUCT_TEMPLATES;
     this stores the bespoke ones an admin builds by hand.
  2. **SignalPoolService** -- visibility into, and limited control over, the
     signal pool that feeds every aggregate (SOW 4.5: "signal pool controls
     (consent and internal visibility)").

## The consent rule, and why it is asymmetric

`b2b_consent` may be **revoked** by an admin but never **granted**.

Consent is the user's to give. An admin flipping it False -> True would be
manufacturing consent on someone's behalf, which is exactly the failure mode
the consent flag exists to prevent, and it would silently pull that user's
production into commercial reports they never agreed to. So `set_consent` only
accepts False, and the API rejects an attempt to grant with 422.

Revocation, by contrast, must be possible: it is how a support request or an
erasure request gets honoured, and it only ever removes data from reports.

`is_internal` is symmetric and freely editable. It is operational hygiene
(marking owner and test rows), carries no privacy weight, and marking a row
internal only ever removes it from customer-facing aggregation.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TemplateNameConflict(Exception):
    """Raised when a template name is already taken by a different template."""


class ConsentGrantRefused(Exception):
    """Raised when an admin attempts to grant b2b_consent on a user's behalf."""


class PackageTemplateService:
    TABLE = "b2b_package_templates"

    def __init__(self, db: Any) -> None:
        self.db = db

    # --- reads -------------------------------------------------------------
    def list_all(self, product_type: str | None = None) -> list[dict[str, Any]]:
        query = self.db.table(self.TABLE).select("*")
        if product_type:
            query = query.eq("product_type", product_type)
        rows = query.execute().data or []
        return sorted(rows, key=lambda r: (r.get("name") or "").lower())

    def get(self, template_id: str) -> dict[str, Any] | None:
        rows = self.db.table(self.TABLE).select("*").eq("id", template_id).execute().data or []
        return rows[0] if rows else None

    def get_by_name(self, name: str) -> dict[str, Any] | None:
        rows = self.db.table(self.TABLE).select("*").eq("name", name).execute().data or []
        return rows[0] if rows else None

    # --- writes ------------------------------------------------------------
    def save(
        self,
        *,
        name: str,
        section_keys: list[str],
        description: str | None = None,
        product_type: str | None = None,
        created_by: str | None = None,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a saved composition.

        Upserting on name would silently overwrite a colleague's template, so a
        name collision with a *different* id is refused instead.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Template name is required")
        if not section_keys:
            raise ValueError("A template must contain at least one section")

        existing_by_name = self.get_by_name(name)
        if existing_by_name and existing_by_name["id"] != (template_id or ""):
            raise TemplateNameConflict(f"A template named '{name}' already exists")

        current = self.get(template_id) if template_id else None
        if template_id and not current:
            raise ValueError(f"Template {template_id} not found")

        record: dict[str, Any] = {
            "id": current["id"] if current else str(uuid4()),
            "name": name,
            "description": description,
            # Order is the render order, so it is preserved exactly as given.
            "section_keys": list(section_keys),
            "product_type": product_type,
            "updated_at": _utcnow(),
        }
        # Reflected tables carry no Python-side defaults (see slice B notes), so
        # created_at is written explicitly and only on first save.
        if not current:
            record["created_by"] = created_by
            record["created_at"] = _utcnow()
            result = self.db.table(self.TABLE).insert(record).execute()
        else:
            result = (
                self.db.table(self.TABLE).update(record).eq("id", record["id"]).execute()
            )
        return (result.data or [record])[0]

    def delete(self, template_id: str) -> bool:
        if not self.get(template_id):
            return False
        self.db.table(self.TABLE).delete().eq("id", template_id).execute()
        return True


class SignalPoolService:
    """Visibility into the pool that feeds every B2B aggregate.

    The counts here answer the question an admin actually has before a delivery:
    "how much of what we hold is even usable?" -- which is not the same as the
    row count, because consent and internal flags exclude rows.
    """

    TABLE = "production_signals"

    def __init__(self, db: Any) -> None:
        self.db = db

    def _all_rows(
        self, period_start: date | None = None, period_end: date | None = None
    ) -> list[dict[str, Any]]:
        query = self.db.table(self.TABLE).select("*")
        if period_start:
            query = query.gte("submission_date", period_start)
        if period_end:
            query = query.lte("submission_date", period_end)
        return query.execute().data or []

    @staticmethod
    def _is_eligible(row: dict[str, Any]) -> bool:
        """Mirrors B2BService._load_signals: consented AND not internal."""
        return bool(row.get("b2b_consent")) and not bool(row.get("is_internal"))

    def summary(
        self, period_start: date | None = None, period_end: date | None = None
    ) -> dict[str, Any]:
        rows = self._all_rows(period_start, period_end)
        total = len(rows)
        consented = sum(1 for r in rows if r.get("b2b_consent"))
        internal = sum(1 for r in rows if r.get("is_internal"))
        eligible = sum(1 for r in rows if self._is_eligible(r))
        return {
            "total": total,
            "consented": consented,
            "not_consented": total - consented,
            "internal": internal,
            # The number that actually reaches a report.
            "eligible": eligible,
            "excluded": total - eligible,
            "excluded_reasons": {
                "no_consent": sum(1 for r in rows if not r.get("b2b_consent")),
                "internal": sum(
                    1 for r in rows if r.get("b2b_consent") and r.get("is_internal")
                ),
            },
            "period_start": period_start.isoformat() if period_start else None,
            "period_end": period_end.isoformat() if period_end else None,
        }

    def list_signals(
        self,
        *,
        period_start: date | None = None,
        period_end: date | None = None,
        consent: bool | None = None,
        internal: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        rows = self._all_rows(period_start, period_end)
        if consent is not None:
            rows = [r for r in rows if bool(r.get("b2b_consent")) is consent]
        if internal is not None:
            rows = [r for r in rows if bool(r.get("is_internal")) is internal]

        rows.sort(key=lambda r: str(r.get("submission_date") or ""), reverse=True)
        total = len(rows)
        page = rows[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            # Deliberately narrow: the pool view is a governance tool, not a data
            # browser. It exposes the flags being governed plus enough context to
            # identify a row, and nothing else about the production.
            "items": [
                {
                    "id": r.get("id"),
                    "script_id": r.get("script_id"),
                    "submission_date": (
                        r["submission_date"].isoformat()
                        if isinstance(r.get("submission_date"), date)
                        else r.get("submission_date")
                    ),
                    "territory": r.get("territory"),
                    "home_country": r.get("home_country"),
                    "format": r.get("format"),
                    "b2b_consent": bool(r.get("b2b_consent")),
                    "is_internal": bool(r.get("is_internal")),
                    "eligible": self._is_eligible(r),
                }
                for r in page
            ],
        }

    def get(self, signal_id: str) -> dict[str, Any] | None:
        rows = self.db.table(self.TABLE).select("*").eq("id", signal_id).execute().data or []
        return rows[0] if rows else None

    def set_internal(self, signal_id: str, is_internal: bool) -> dict[str, Any] | None:
        """Mark a row as internal/test, or restore it to the customer-facing pool."""
        if not self.get(signal_id):
            return None
        self.db.table(self.TABLE).update(
            {"is_internal": bool(is_internal), "updated_at": _utcnow()}
        ).eq("id", signal_id).execute()
        logger.info("Signal %s is_internal set to %s by admin", signal_id, is_internal)
        return self.get(signal_id)

    def set_consent(self, signal_id: str, consent: bool) -> dict[str, Any] | None:
        """Revoke B2B consent. Granting is refused; see the module docstring."""
        if consent:
            raise ConsentGrantRefused(
                "Consent can only be granted by the producer through their own "
                "consent setting. An admin may revoke it, never grant it."
            )
        if not self.get(signal_id):
            return None
        self.db.table(self.TABLE).update(
            {"b2b_consent": False, "updated_at": _utcnow()}
        ).eq("id", signal_id).execute()
        logger.info("Signal %s b2b_consent REVOKED by admin", signal_id)
        return self.get(signal_id)
