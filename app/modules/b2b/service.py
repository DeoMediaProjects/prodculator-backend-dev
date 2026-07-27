from __future__ import annotations

import base64
import calendar
import logging
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from jinja2 import TemplateNotFound

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient, create_client
from app.modules.email.service import EmailService
from app.modules.reports.pdf_service import PDFService

logger = logging.getLogger(__name__)

PRIVACY_MIN_OVERALL = 10
PRIVACY_MIN_SEGMENT = 5


B2B_PRODUCTS: dict[str, dict[str, Any]] = {
    "camera_equipment": {
        "title": "Camera & Equipment Demand Intelligence",
        "audience": "Equipment Rental & Camera Houses",
        "description": (
            "Aggregated production volume, territory, format, genre, and camera "
            "equipment demand signals from anonymised platform metadata."
        ),
        "features": [
            "Territory-specific production volume trends",
            "Camera and equipment demand mix",
            "Production type distribution",
            "Genre-based equipment implications",
            "Seasonal trend analysis",
        ],
        # Placeholder pricing (~$2 equivalent) pending final B2B price sign-off.
        "price_gbp_cents": 160,
        "price_usd_cents": 200,
        "self_service": True,
        "price_attrs": {
            "gbp": "STRIPE_PRICE_B2B_CAMERA_EQUIPMENT_GBP",
            "usd": "STRIPE_PRICE_B2B_CAMERA_EQUIPMENT_USD",
        },
    },
    "production_services": {
        "title": "Production Services Intelligence",
        "audience": "Payroll, Accounting, Insurance & Logistics",
        "description": (
            "Crew size, cast demand, production scale, format, and budget range "
            "analytics for production service planning."
        ),
        "features": [
            "Crew size trend analytics by territory",
            "Cast demand analytics",
            "Production scale distribution reports",
            "Total headcount trend analysis",
            "Budget range breakdowns",
        ],
        # Placeholder pricing (~$2 equivalent) pending final B2B price sign-off.
        "price_gbp_cents": 160,
        "price_usd_cents": 200,
        "self_service": True,
        "price_attrs": {
            "gbp": "STRIPE_PRICE_B2B_PRODUCTION_SERVICES_GBP",
            "usd": "STRIPE_PRICE_B2B_PRODUCTION_SERVICES_USD",
        },
    },
    "crew_casting": {
        "title": "Crew & Casting Demand Intelligence",
        "audience": "Casting Agencies & Crew Agencies",
        "description": (
            "Aggregated genre, scale, territory, and cast-volume signals for "
            "crew and casting demand planning."
        ),
        "features": [
            "Genre distribution by territory and budget",
            "Principal and supporting cast volume trends",
            "Extras demand by territory",
            "Submission timing clusters",
            "Budget tier breakdown by format",
        ],
        # Placeholder pricing (~$2 equivalent) pending final B2B price sign-off.
        "price_gbp_cents": 160,
        "price_usd_cents": 200,
        "self_service": True,
        "price_attrs": {
            "gbp": "STRIPE_PRICE_B2B_CREW_CASTING_GBP",
            "usd": "STRIPE_PRICE_B2B_CREW_CASTING_USD",
        },
    },
    "production_trend": {
        "title": "Strategic Production Trend Intelligence",
        "audience": "Studios, Streamers, Agencies & Industry Bodies",
        "description": (
            "Strategic trend signals across territory, genre, budget, and format "
            "from anonymised production planning metadata."
        ),
        "features": [
            "Territory demand distribution",
            "Budget range movement by format",
            "Genre and format trend signals",
            "Monthly production planning volume",
            "Emerging territory demand signals",
        ],
        # Placeholder pricing (~$2 equivalent) pending final B2B price sign-off.
        "price_gbp_cents": 160,
        "price_usd_cents": 200,
        "self_service": True,
        "price_attrs": {
            "gbp": "STRIPE_PRICE_B2B_PRODUCTION_TREND_GBP",
            "usd": "STRIPE_PRICE_B2B_PRODUCTION_TREND_USD",
        },
    },
    "enterprise": {
        "title": "Enterprise Slate Intelligence",
        "audience": "Enterprise & Manual Contract Clients",
        "description": (
            "Custom production intelligence agreements with admin-managed "
            "access, recipients, cadence, and reporting scope."
        ),
        "features": [
            "Custom commercial contract",
            "Admin-managed delivery cadence",
            "Enterprise request history",
            "Custom metrics review",
        ],
        "price_gbp_cents": None,
        "price_usd_cents": None,
        "self_service": False,
        "price_attrs": {},
    },
}


# Declarative section layout per product. Both the raw-facts stage and the
# render stage walk this list, so a stored monthly aggregate and a freshly
# queried period always produce the same sections in the same order.
#
# `kind` describes how a section is DERIVED from signal rows. Everything except
# "headcount" reduces to a Counter[str], which is what makes months summable.
_DEFAULT_SECTION_SPECS: tuple[dict[str, Any], ...] = (
    {"kind": "distribution", "key": "territory", "title": "Territory Demand Distribution"},
    {"kind": "distribution", "key": "budget_range", "title": "Budget Range Mix"},
    {"kind": "distribution", "key": "genres", "title": "Genre Trend Signals", "flatten": True},
    {"kind": "distribution", "key": "format", "title": "Format Mix"},
    {"kind": "month", "key": "submission_month", "title": "Monthly Planning Volume"},
)

SECTION_SPECS: dict[str, tuple[dict[str, Any], ...]] = {
    "camera_equipment": (
        {"kind": "distribution", "key": "territory", "title": "Production Volume by Territory"},
        {"kind": "distribution", "key": "camera_equipment", "title": "Camera & Equipment Mix", "flatten": True},
        {"kind": "distribution", "key": "format", "title": "Production Type Distribution"},
        {"kind": "distribution", "key": "genres", "title": "Genre Mix", "flatten": True},
        {"kind": "month", "key": "submission_month", "title": "Monthly Upload Volume"},
    ),
    "production_services": (
        {"kind": "numeric_band", "key": "crew_size", "title": "Crew Size Distribution"},
        {"kind": "headcount", "key": "headcount", "title": "Total Headcount Trend Analysis"},
        {"kind": "distribution", "key": "budget_range", "title": "Budget Range Breakdown"},
        {"kind": "distribution", "key": "territory", "title": "Territory Demand Mix"},
        {"kind": "distribution", "key": "format", "title": "Format Distribution"},
    ),
    "crew_casting": (
        {"kind": "numeric_band", "key": "principal_cast", "title": "Principal Cast Demand"},
        {"kind": "numeric_band", "key": "supporting_cast", "title": "Supporting Cast Demand"},
        {"kind": "numeric_band", "key": "background_extras", "title": "Extras Demand"},
        {"kind": "distribution", "key": "genres", "title": "Genre Mix", "flatten": True},
        {"kind": "month", "key": "submission_month", "title": "Submission Timing Clusters"},
    ),
}

HEADCOUNT_KEYS = ("crew_size", "principal_cast", "supporting_cast", "background_extras")


def section_specs(product_type: str) -> tuple[dict[str, Any], ...]:
    return SECTION_SPECS.get(product_type, _DEFAULT_SECTION_SPECS)


def month_start(value: date | datetime) -> date:
    if isinstance(value, datetime):
        value = value.date()
    return value.replace(day=1)


def month_end(value: date | datetime) -> date:
    start = month_start(value)
    return start.replace(day=calendar.monthrange(start.year, start.month)[1])


def months_in_range(start: date | datetime, end: date | datetime) -> list[date]:
    """Every month-start from `start`'s month through `end`'s month, inclusive."""
    cursor = month_start(start)
    last = month_start(end)
    months: list[date] = []
    while cursor <= last:
        months.append(cursor)
        cursor = month_start(add_months(datetime.combine(cursor, datetime.min.time()), 1))
    return months


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def interval_months(frequency: str) -> int:
    return 3 if frequency == "quarterly" else 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


class B2BService:
    def __init__(self, db: DatabaseClient, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.email_service = EmailService(self.settings)
        self.pdf_service = PDFService()

    def list_products(self) -> list[dict[str, Any]]:
        return [self.product_payload(product_type) for product_type in B2B_PRODUCTS.keys()]

    def product_payload(self, product_type: str) -> dict[str, Any]:
        product = self._product(product_type)
        price_attrs = product.get("price_attrs", {})
        return {
            "product_type": product_type,
            "title": product["title"],
            "audience": product["audience"],
            "description": product["description"],
            "features": product["features"],
            "price_gbp_cents": product.get("price_gbp_cents"),
            "price_usd_cents": product.get("price_usd_cents"),
            "self_service": bool(product.get("self_service")),
            "stripe_price_configured": {
                currency: bool(getattr(self.settings, attr, ""))
                for currency, attr in price_attrs.items()
            },
        }

    def get_price_id(self, product_type: str, currency: str) -> str | None:
        product = self._product(product_type)
        attr = product.get("price_attrs", {}).get(currency.lower())
        if not attr:
            return None
        return getattr(self.settings, attr, "") or None

    def list_user_subscriptions(self, user_id: str) -> list[dict[str, Any]]:
        rows = (
            self.db.table("b2b_subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
        return rows

    def active_subscription(self, user_id: str, product_type: str) -> dict[str, Any] | None:
        rows = (
            self.db.table("b2b_subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .eq("product_type", product_type)
            .in_("status", ["active", "trialing"])
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None

    def create_manual_subscription(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_rows = (
            self.db.table("users")
            .select("id,email,company")
            .eq("email", payload["user_email"].strip().lower())
            .limit(1)
            .execute()
            .data
            or []
        )
        if not user_rows:
            raise ValueError("User must sign up before a manual B2B subscription can be created")

        product_type = payload["product_type"]
        product = self._product(product_type)
        now = _utcnow()
        row = {
            "id": str(uuid4()),
            "user_id": user_rows[0]["id"],
            "product_type": product_type,
            "status": payload.get("status") or "active",
            "source": "manual_contract",
            "amount_cents": product.get("price_gbp_cents"),
            "currency": "gbp" if product.get("price_gbp_cents") else None,
            "delivery_frequency": payload.get("delivery_frequency") or "monthly",
            "extra_recipient_email": self._clean_email(payload.get("extra_recipient_email")),
            "company_name": payload.get("company_name") or user_rows[0].get("company"),
            "admin_notes": payload.get("admin_notes"),
            "current_period_start": now,
            "next_delivery_at": add_months(now, interval_months(payload.get("delivery_frequency") or "monthly")),
            "created_at": now,
            "updated_at": now,
        }
        result = self.db.table("b2b_subscriptions").insert(row).execute()
        return (result.data or [row])[0]

    def update_subscription(self, subscription_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        updates = {
            key: value
            for key, value in payload.items()
            if value is not None
            and key
            in {
                "status",
                "delivery_frequency",
                "next_delivery_at",
                "company_name",
                "admin_notes",
            }
        }
        if "extra_recipient_email" in payload:
            updates["extra_recipient_email"] = self._clean_email(payload.get("extra_recipient_email"))
        if not updates:
            return self.get_subscription(subscription_id)

        updates["updated_at"] = _utcnow()
        result = (
            self.db.table("b2b_subscriptions")
            .update(updates)
            .eq("id", subscription_id)
            .execute()
        )
        rows = result.data or []
        subscription = rows[0] if rows else None
        if subscription:
            self.notify_subscription_updated(subscription, sorted(updates.keys()))
        return subscription

    def set_extra_recipient(
        self, subscription_id: str, user_id: str, email: str | None
    ) -> dict[str, Any] | None:
        """Client-side recipient management for one's OWN subscription.

        Ownership is checked here rather than in the router so no caller can
        edit another company's distribution list. Returns None when the
        subscription does not exist or is not the caller's.

        The data model has two slots -- the account holder (`recipient_email`,
        derived from the user and therefore not removable) and one additional
        address. This manages the additional slot.
        """
        subscription = self.get_subscription(subscription_id)
        if not subscription or subscription.get("user_id") != user_id:
            return None

        cleaned = self._clean_email(email)
        updates = {"extra_recipient_email": cleaned, "updated_at": _utcnow()}
        result = (
            self.db.table("b2b_subscriptions")
            .update(updates)
            .eq("id", subscription_id)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else {**subscription, **updates}

    def get_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        rows = (
            self.db.table("b2b_subscriptions")
            .select("*")
            .eq("id", subscription_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None

    def create_or_update_subscription_from_checkout(self, session: dict) -> dict[str, Any]:
        metadata = session.get("metadata") or {}
        user_id = metadata.get("userId")
        product_type = metadata.get("productType")
        if not user_id or not product_type:
            raise ValueError("B2B checkout metadata is missing userId or productType")

        product = self._product(product_type)
        currency = (metadata.get("currency") or "gbp").lower()
        frequency = metadata.get("deliveryFrequency") or "monthly"
        now = _utcnow()
        period_start = self._stripe_timestamp_to_datetime(session.get("current_period_start")) or now
        row = {
            "id": str(uuid4()),
            "user_id": user_id,
            "product_type": product_type,
            "status": "active",
            "source": "stripe",
            "stripe_customer_id": session.get("customer"),
            "stripe_subscription_id": session.get("subscription"),
            "price_id": metadata.get("priceId") or self.get_price_id(product_type, currency),
            "amount_cents": product.get(f"price_{currency}_cents"),
            "currency": currency,
            "delivery_frequency": frequency,
            "extra_recipient_email": self._clean_email(metadata.get("extraRecipientEmail")),
            "current_period_start": period_start,
            "next_delivery_at": add_months(period_start, interval_months(frequency)),
            "cancel_at_period_end": False,
            "created_at": now,
            "updated_at": now,
        }
        result = self.db.table("b2b_subscriptions").upsert(
            row,
            on_conflict="stripe_subscription_id",
        ).execute()
        subscription = (result.data or [row])[0]
        self.notify_subscription_active(subscription)
        return subscription

    def update_from_stripe_subscription(self, subscription: dict) -> bool:
        subscription_id = subscription.get("id")
        if not subscription_id:
            return False
        existing = (
            self.db.table("b2b_subscriptions")
            .select("id")
            .eq("stripe_subscription_id", subscription_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not existing:
            return False

        period_start = subscription.get("current_period_start")
        period_end = subscription.get("current_period_end")
        if not period_start or not period_end:
            items = (subscription.get("items") or {}).get("data") or []
            if items:
                period_start = period_start or items[0].get("current_period_start")
                period_end = period_end or items[0].get("current_period_end")

        updates: dict[str, Any] = {
            "status": subscription.get("status"),
            "cancel_at_period_end": subscription.get("cancel_at_period_end", False),
            "updated_at": _utcnow(),
        }
        if period_start:
            updates["current_period_start"] = datetime.fromtimestamp(period_start, tz=timezone.utc)
        if period_end:
            updates["current_period_end"] = datetime.fromtimestamp(period_end, tz=timezone.utc)

        self.db.table("b2b_subscriptions").update(updates).eq(
            "stripe_subscription_id", subscription_id
        ).execute()
        return True

    def mark_stripe_subscription_deleted(self, subscription_id: str) -> bool:
        existing = (
            self.db.table("b2b_subscriptions")
            .select("id")
            .eq("stripe_subscription_id", subscription_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not existing:
            return False
        result = (
            self.db.table("b2b_subscriptions")
            .update(
                {
                    "status": "cancelled",
                    "cancelled_at": _utcnow(),
                    "updated_at": _utcnow(),
                }
            )
            .eq("stripe_subscription_id", subscription_id)
            .execute()
        )
        return bool(result.data)

    def create_intelligence_request(
        self,
        *,
        user_id: str,
        user_email: str,
        product_type: str,
        period_start: date,
        period_end: date,
        extra_recipient_email: str | None = None,
        request_type: str = "on_demand",
        subscription: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        subscription = subscription or self.active_subscription(user_id, product_type)
        if not subscription:
            raise PermissionError("An active B2B subscription is required for this intelligence product")

        recipient_email = user_email.strip().lower()
        extra_email = self._clean_email(extra_recipient_email)
        row = {
            "id": str(uuid4()),
            "user_id": user_id,
            "b2b_subscription_id": subscription["id"],
            "product_type": product_type,
            "status": "processing",
            "request_type": request_type,
            "period_start": period_start,
            "period_end": period_end,
            "recipient_email": recipient_email,
            "extra_recipient_email": extra_email,
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
        }
        result = self.db.table("b2b_intelligence_requests").insert(row).execute()
        return (result.data or [row])[0]

    def process_request(self, request_id: str) -> None:
        request_row = self.get_request(request_id, include_metrics=True)
        if not request_row:
            logger.warning("B2B request %s disappeared before processing", request_id)
            return

        try:
            metrics = self.build_period_metrics(
                product_type=request_row["product_type"],
                period_start=self._parse_date(request_row["period_start"]),
                period_end=self._parse_date(request_row["period_end"]),
            )
            # Hold-and-notify: never deliver a thin or empty report. When the
            # period lacks enough consented signals to clear the privacy floor,
            # hold the request and notify the client + ops instead of generating
            # a report with nothing renderable in it.
            if metrics.get("insufficient_data"):
                self._hold_request(request_row, metrics)
                return

            html = self.render_pdf_html(metrics)
            pdf_bytes = self.pdf_service.generate_pdf_bytes(html)
            if not pdf_bytes:
                raise RuntimeError("PDF generation temporarily unavailable")

            storage_path = self.storage_path(request_row)
            self.db.storage.from_("reports").upload(
                storage_path,
                pdf_bytes,
                {
                    "content-type": "application/pdf",
                    "x-upsert": "true",
                },
            )
            pdf_url = self.db.storage.from_("reports").get_s3_key(storage_path)
            completed_at = _utcnow()
            update = {
                "status": "completed",
                "metrics": metrics,
                "pdf_url": pdf_url,
                "completed_at": completed_at,
                "updated_at": completed_at,
            }
            self.db.table("b2b_intelligence_requests").update(update).eq("id", request_id).execute()
            request_row.update(update)
            self.deliver_request_pdf(request_row, pdf_bytes)
        except Exception as exc:
            logger.exception("B2B request processing failed: request_id=%s", request_id)
            self.db.table("b2b_intelligence_requests").update(
                {
                    "status": "failed",
                    "error_message": str(exc),
                    "updated_at": _utcnow(),
                }
            ).eq("id", request_id).execute()
            self._notify_admin_alert(
                heading="A Business Intelligence report failed to generate",
                message=(
                    f"Request {request_id} could not be generated and has been marked failed. "
                    "The client was not charged for delivery; investigate before the next run."
                ),
                details={
                    "Request ID": request_id,
                    "Product": request_row.get("product_type"),
                    "Period": f"{_as_iso(request_row.get('period_start'))} to {_as_iso(request_row.get('period_end'))}",
                    "Error": str(exc)[:300],
                },
            )

    def _hold_request(self, request_row: dict[str, Any], metrics: dict[str, Any]) -> None:
        """Mark a request held (not failed) for insufficient data, persist the
        computed metrics for context, and notify the client and ops."""
        count = metrics.get("source_signal_count", 0)
        floor = (metrics.get("thresholds") or {}).get("minimum_overall_records", PRIVACY_MIN_OVERALL)
        reason = (
            f"On hold: {count} consented production signal(s) in this period, "
            f"below the {floor} required to protect privacy."
        )
        held_at = _utcnow()
        self.db.table("b2b_intelligence_requests").update(
            {
                "status": "held",
                "metrics": metrics,
                "error_message": reason,
                "updated_at": held_at,
            }
        ).eq("id", request_row["id"]).execute()
        request_row.update({"status": "held", "metrics": metrics, "error_message": reason})
        logger.info(
            "B2B request %s held for insufficient data (%s < %s)",
            request_row["id"], count, floor,
        )
        self._notify_request_held(request_row, metrics)
        self._notify_admin_alert(
            heading="A scheduled Business Intelligence report was held",
            message=(
                "There were not enough consented production signals to generate this report "
                "while protecting privacy, so it was held rather than delivered empty."
            ),
            details={
                "Request ID": request_row["id"],
                "Product": request_row.get("product_type"),
                "Period": f"{_as_iso(request_row.get('period_start'))} to {_as_iso(request_row.get('period_end'))}",
                "Signals": f"{count} (need {floor})",
            },
        )

    def _notify_request_held(self, request_row: dict[str, Any], metrics: dict[str, Any]) -> None:
        recipients = [
            request_row.get("recipient_email"),
            self._clean_email(request_row.get("extra_recipient_email")),
        ]
        product_title = metrics.get("title") or self._product(request_row["product_type"])["title"]
        for email in [e for e in recipients if e]:
            try:
                self.email_service.send(
                    email,
                    "b2b_intelligence_held",
                    {
                        "product_title": product_title,
                        "period_start": _as_iso(request_row.get("period_start")),
                        "period_end": _as_iso(request_row.get("period_end")),
                        "request_id": request_row["id"],
                        # Existing clients land on their own dashboard, not the catalogue.
                        "b2b_url": f"{self.settings.FRONTEND_URL.rstrip('/')}/business-intelligence",
                    },
                )
            except Exception:
                logger.warning("Failed to send B2B hold notice to %s", email, exc_info=True)

    def _notify_admin_alert(
        self, *, heading: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        """Best-effort ops alert. Never raises — an alert failure must not affect
        the request's own outcome."""
        recipient = (
            getattr(self.settings, "B2B_ADMIN_ALERT_EMAIL", "") or self.settings.CONTACT_EMAIL or ""
        ).strip()
        if not recipient:
            return
        try:
            self.email_service.send(
                recipient,
                "b2b_admin_alert",
                {"heading": heading, "message": message, "details": details or {}},
            )
        except Exception:
            logger.warning("Failed to send B2B admin alert", exc_info=True)

    def build_metrics(self, *, product_type: str, period_start: date, period_end: date) -> dict[str, Any]:
        rows = self._load_signals(period_start, period_end)
        facts = self._build_raw_facts(product_type, rows)
        return self._facts_to_metrics(
            product_type,
            facts,
            period_start=period_start,
            period_end=period_end,
        )

    def _build_raw_facts(self, product_type: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Raw facts for a standard product's fixed section layout."""
        return self._facts_from_specs(section_specs(product_type), rows)

    def _facts_from_specs(
        self, specs: "list[dict[str, Any]] | tuple[dict[str, Any], ...]", rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Reduce signal rows to RAW, UNSUPPRESSED counters for any spec list.

        No privacy floor is applied here. Sub-threshold counts MUST survive into
        storage so that a segment which is below the floor in any single month
        but above it across a quarter still becomes visible when months are
        composed. Suppression happens in `_facts_to_metrics` (the renderer).

        Bespoke packages route through this too, so an admin-composed report is
        counted and suppressed by exactly the same code as a standard product.
        """
        sections: list[dict[str, Any]] = []
        for spec in specs:
            kind = spec["kind"]
            if kind == "headcount":
                sections.append(
                    {
                        "title": spec["title"],
                        "key": spec["key"],
                        "kind": "headcount",
                        "stats": self._headcount_stats(rows),
                    }
                )
                continue

            counter: Counter[str] = Counter()
            if kind == "distribution":
                flatten = bool(spec.get("flatten"))
                for row in rows:
                    value = row.get(spec["key"])
                    values = value if flatten and isinstance(value, list) else [value]
                    for entry in values:
                        if self._is_missing(entry):
                            continue
                        counter[self._label(entry)] += 1
            elif kind == "numeric_band":
                for row in rows:
                    value = row.get(spec["key"])
                    if self._is_missing(value):
                        continue
                    counter[self._numeric_band(value)] += 1
            elif kind == "month":
                for row in rows:
                    counter[self._parse_date(row.get("submission_date")).strftime("%Y-%m")] += 1
            else:  # pragma: no cover - guards against a malformed spec
                raise ValueError(f"Unknown section kind: {kind}")

            sections.append(
                {
                    "title": spec["title"],
                    "key": spec["key"],
                    "kind": "counter",
                    "counts": dict(counter),
                }
            )

        return {"signal_count": len(rows), "sections": sections}

    def _facts_to_metrics(
        self,
        product_type: str,
        facts: dict[str, Any],
        *,
        period_start: date,
        period_end: date,
        extra: dict[str, Any] | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Apply privacy floors to raw facts and produce the deliverable metrics.

        This is the renderer. The floors live here and there is no bypass.

        `title` is supplied for bespoke packages, which have no entry in
        B2B_PRODUCTS; standard products resolve their title from the catalogue.
        """
        signal_count = int(facts.get("signal_count") or 0)
        suppressed_segments: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {
            "product_type": product_type,
            "title": title or self._product(product_type)["title"],
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "generated_at": _utcnow().isoformat(),
            "source_signal_count": signal_count,
            "thresholds": {
                "minimum_overall_records": PRIVACY_MIN_OVERALL,
                "minimum_segment_records": PRIVACY_MIN_SEGMENT,
            },
            "insufficient_data": signal_count < PRIVACY_MIN_OVERALL,
            "sections": [],
            "suppressed_segments": suppressed_segments,
        }
        if extra:
            metrics.update(extra)
        if signal_count < PRIVACY_MIN_OVERALL:
            metrics["sections"] = [
                {
                    "title": "Privacy Threshold",
                    "summary": (
                        "The selected period does not contain enough anonymised "
                        "production signals to produce customer-facing segments. "
                        "Choose a wider date range."
                    ),
                    "rows": [],
                }
            ]
            return metrics

        sections: list[dict[str, Any]] = []
        for section in facts.get("sections") or []:
            if section.get("kind") == "headcount":
                sections.append(self._headcount_section_from_stats(section))
            else:
                sections.append(
                    self._counter_section(
                        Counter(section.get("counts") or {}),
                        section["title"],
                        suppressed_segments,
                        section["key"],
                    )
                )

        metrics["sections"] = [section for section in sections if section]
        return metrics

    # ------------------------------------------------------------------
    # Monthly aggregates: monthly is the atomic unit. Quarterly composes from
    # three stored months and yearly from twelve, rather than re-querying the
    # signal pool (Implementation Plan section 3).
    # ------------------------------------------------------------------

    def build_monthly_aggregate(self, product_type: str, month: date | datetime) -> dict[str, Any]:
        """Compute and store RAW facts for a single month. Idempotent."""
        start = month_start(month)
        rows = self._load_signals(start, month_end(start))
        facts = self._build_raw_facts(product_type, rows)
        record = {
            "id": str(uuid4()),
            "product_type": product_type,
            # Date/datetime columns take real objects, not ISO strings.
            "period_month": start,
            "signal_count": facts["signal_count"],
            "facts": facts,
            "updated_at": _utcnow(),
        }
        # Reflected tables carry no Python-side defaults, so timestamps are set
        # explicitly. created_at is only sent on first write; a recompute must
        # refresh updated_at without rewriting when the month was first stored.
        if not self.get_monthly_aggregates(product_type, [start]):
            record["created_at"] = _utcnow()
        self.db.table("b2b_monthly_aggregates").upsert(
            record, on_conflict="product_type,period_month"
        ).execute()
        logger.info(
            "Stored B2B monthly aggregate product=%s month=%s signals=%s",
            product_type,
            start.isoformat(),
            facts["signal_count"],
        )
        return record

    def get_monthly_aggregates(
        self, product_type: str, months: list[date] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Fetch stored aggregates keyed by ISO month-start."""
        query = (
            self.db.table("b2b_monthly_aggregates")
            .select("*")
            .eq("product_type", product_type)
        )
        if months:
            query = query.in_("period_month", [month_start(m) for m in months])
        result = query.execute()
        stored: dict[str, dict[str, Any]] = {}
        for row in result.data or []:
            key = _as_iso(self._parse_date(row.get("period_month")))
            if key:
                stored[key] = row
        return stored

    def ensure_monthly_aggregates(
        self, product_type: str, months: list[date]
    ) -> dict[str, dict[str, Any]]:
        """Return stored aggregates for `months`, computing any that are missing."""
        stored = self.get_monthly_aggregates(product_type, months)
        for month in months:
            key = month_start(month).isoformat()
            if key not in stored:
                stored[key] = self.build_monthly_aggregate(product_type, month)
        return stored

    @staticmethod
    def compose_facts(facts_list: list[dict[str, Any]]) -> dict[str, Any]:
        """Sum RAW facts across months.

        Counters are summed label-by-label BEFORE any privacy floor is applied,
        which is what lets a segment that is sub-threshold in each individual
        month surface once the months are combined.
        """
        signal_count = 0
        order: list[tuple[str, str, str]] = []
        counters: dict[tuple[str, str, str], Counter[str]] = {}
        headcounts: dict[tuple[str, str, str], dict[str, int]] = {}

        for facts in facts_list:
            signal_count += int(facts.get("signal_count") or 0)
            for section in facts.get("sections") or []:
                ident = (section.get("kind"), section.get("title"), section.get("key"))
                if ident not in counters and ident not in headcounts:
                    order.append(ident)
                if section.get("kind") == "headcount":
                    stats = section.get("stats") or {}
                    agg = headcounts.setdefault(
                        ident, {"values_count": 0, "sum": 0, "max": 0, "rows_total": 0}
                    )
                    agg["values_count"] += int(stats.get("values_count") or 0)
                    agg["sum"] += int(stats.get("sum") or 0)
                    agg["max"] = max(agg["max"], int(stats.get("max") or 0))
                    agg["rows_total"] += int(stats.get("rows_total") or 0)
                else:
                    counters.setdefault(ident, Counter()).update(section.get("counts") or {})

        sections: list[dict[str, Any]] = []
        for kind, title, key in order:
            ident = (kind, title, key)
            if kind == "headcount":
                sections.append(
                    {"title": title, "key": key, "kind": "headcount", "stats": headcounts[ident]}
                )
            else:
                sections.append(
                    {"title": title, "key": key, "kind": "counter", "counts": dict(counters[ident])}
                )

        return {"signal_count": signal_count, "sections": sections}

    def compose_from_months(
        self,
        product_type: str,
        months: list[date],
        *,
        compare_to: list[date] | None = None,
    ) -> dict[str, Any]:
        """Build deliverable metrics by composing stored monthly aggregates.

        `compare_to` supplies the preceding period for the month-on-month
        comparison section (SOW 4.3).
        """
        if not months:
            raise ValueError("compose_from_months requires at least one month")

        ordered = sorted(month_start(m) for m in months)
        stored = self.ensure_monthly_aggregates(product_type, ordered)
        facts_list = [
            stored[m.isoformat()].get("facts") or {}
            for m in ordered
            if m.isoformat() in stored
        ]
        composed = self.compose_facts(facts_list)

        extra: dict[str, Any] = {
            "composed_from_months": [m.isoformat() for m in ordered],
            "composition": "monthly" if len(ordered) == 1 else f"{len(ordered)}-month",
        }

        metrics = self._facts_to_metrics(
            product_type,
            composed,
            period_start=ordered[0],
            period_end=month_end(ordered[-1]),
            extra=extra,
        )

        if compare_to:
            previous_months = sorted(month_start(m) for m in compare_to)
            previous_stored = self.ensure_monthly_aggregates(product_type, previous_months)
            previous = self.compose_facts(
                [
                    previous_stored[m.isoformat()].get("facts") or {}
                    for m in previous_months
                    if m.isoformat() in previous_stored
                ]
            )
            comparison = self.month_on_month(composed, previous)
            if comparison and not metrics.get("insufficient_data"):
                metrics["sections"].append(comparison)
            metrics["comparison_months"] = [m.isoformat() for m in previous_months]

        return metrics

    def month_on_month(
        self, current_facts: dict[str, Any], previous_facts: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Period-over-period movement, reported only for displayable segments.

        Deltas are computed from raw counts but a row is emitted only when the
        label clears the segment floor in at least one of the two periods --
        otherwise the comparison table would leak sub-threshold counts that the
        main sections deliberately suppress.
        """
        previous_totals: dict[str, int] = {}
        for section in previous_facts.get("sections") or []:
            if section.get("kind") == "headcount":
                continue
            for label, count in (section.get("counts") or {}).items():
                previous_totals[f"{section.get('key')}::{label}"] = int(count)

        rows: list[dict[str, Any]] = []
        for section in current_facts.get("sections") or []:
            if section.get("kind") == "headcount":
                continue
            for label, count in (section.get("counts") or {}).items():
                current = int(count)
                prior = previous_totals.get(f"{section.get('key')}::{label}", 0)
                if current < PRIVACY_MIN_SEGMENT and prior < PRIVACY_MIN_SEGMENT:
                    continue
                delta = current - prior
                rows.append(
                    {
                        "label": f"{section.get('title')} - {label}",
                        "current": current,
                        "previous": prior,
                        "delta": delta,
                        "percentage_change": (
                            round((delta / prior) * 100, 1) if prior else None
                        ),
                    }
                )

        if not rows:
            return None

        rows.sort(key=lambda row: abs(row["delta"]), reverse=True)
        risers = sum(1 for row in rows if row["delta"] > 0)
        fallers = sum(1 for row in rows if row["delta"] < 0)
        return {
            "title": "Period-on-Period Movement",
            "summary": (
                f"{risers} segment(s) grew and {fallers} declined against the "
                "preceding period."
            ),
            "rows": rows,
            "kind": "comparison",
        }

    def build_period_metrics(
        self,
        *,
        product_type: str,
        period_start: date,
        period_end: date,
        compare: bool = True,
    ) -> dict[str, Any]:
        """Delivery entry point.

        Periods that align to whole calendar months compose from stored monthly
        aggregates (so quarterly = three stored months, never a re-query) and
        gain a period-on-period comparison. Ad-hoc ranges that do not align to
        month boundaries fall back to a direct signal query.
        """
        start = self._parse_date(period_start)
        end = self._parse_date(period_end)
        if start != month_start(start) or end != month_end(end) or start > end:
            return self.build_metrics(
                product_type=product_type, period_start=start, period_end=end
            )

        months = months_in_range(start, end)
        compare_to: list[date] | None = None
        if compare:
            span = len(months)
            compare_to = [
                month_start(add_months(datetime.combine(month, datetime.min.time()), -span))
                for month in months
            ]
        return self.compose_from_months(product_type, months, compare_to=compare_to)

    def yearly_available(self, product_type: str, months: list[date]) -> bool:
        """Yearly composition unlocks only once twelve stored months exist."""
        stored = self.get_monthly_aggregates(product_type, months)
        return len(months) >= 12 and len(stored) >= 12

    def backfill_monthly_aggregates(
        self, product_type: str, start: date | datetime, end: date | datetime
    ) -> int:
        """Recompute and store every month between `start` and `end` inclusive."""
        months = months_in_range(start, end)
        for month in months:
            self.build_monthly_aggregate(product_type, month)
        return len(months)

    def generate_bespoke_report(
        self,
        *,
        metrics: dict[str, Any],
        user_id: str,
        recipient_email: str,
        period_start: date,
        period_end: date,
        subscription_id: str | None = None,
        extra_recipient_email: str | None = None,
        deliver: bool = False,
    ) -> dict[str, Any]:
        """Persist and render an admin-composed bespoke package.

        Recorded as an `enterprise` request with `request_type="admin"` so it
        appears in the normal delivery history and reuses the same storage,
        download and resend paths as a standard product.

        Delivery is OPT-IN: an admin composing a package is usually iterating,
        and emailing a client on every generate would be worse than useless.
        """
        if metrics.get("insufficient_data"):
            raise ValueError(
                "The composed package does not clear the privacy floor for this period."
            )

        request_id = str(uuid4())
        now = _utcnow()
        row: dict[str, Any] = {
            "id": request_id,
            "user_id": user_id,
            "b2b_subscription_id": subscription_id,
            "product_type": "enterprise",
            "status": "processing",
            "request_type": "admin",
            "period_start": period_start,
            "period_end": period_end,
            "recipient_email": recipient_email,
            "extra_recipient_email": self._clean_email(extra_recipient_email),
            "created_at": now,
            "updated_at": now,
        }
        inserted = self.db.table("b2b_intelligence_requests").insert(row).execute()
        request_row = (inserted.data or [row])[0]

        try:
            html = self.render_pdf_html(metrics)
            pdf_bytes = self.pdf_service.generate_pdf_bytes(html)
            if not pdf_bytes:
                raise RuntimeError("PDF generation temporarily unavailable")

            storage_path = self.storage_path(request_row)
            self.db.storage.from_("reports").upload(
                storage_path,
                pdf_bytes,
                {"content-type": "application/pdf", "x-upsert": "true"},
            )
            pdf_url = self.db.storage.from_("reports").get_s3_key(storage_path)
            completed_at = _utcnow()
            update = {
                "status": "completed",
                "metrics": metrics,
                "pdf_url": pdf_url,
                "completed_at": completed_at,
                "updated_at": completed_at,
            }
            self.db.table("b2b_intelligence_requests").update(update).eq("id", request_id).execute()
            request_row.update(update)
            if deliver:
                self.deliver_request_pdf(request_row, pdf_bytes)
            return request_row
        except Exception as exc:
            logger.exception("Bespoke B2B package generation failed: request_id=%s", request_id)
            self.db.table("b2b_intelligence_requests").update(
                {
                    "status": "failed",
                    "error_message": str(exc),
                    "updated_at": _utcnow(),
                }
            ).eq("id", request_id).execute()
            raise

    def render_pdf_html(self, metrics: dict[str, Any]) -> str:
        try:
            template = self.pdf_service.env.get_template("b2b_intelligence.html")
            return template.render(metrics=metrics)
        except TemplateNotFound:
            return f"<html><body><pre>{metrics}</pre></body></html>"

    def recipients_for(self, request_row: dict[str, Any]) -> list[str]:
        recipients = [
            request_row.get("recipient_email"),
            self._clean_email(request_row.get("extra_recipient_email")),
        ]
        # De-duplicate while preserving order: the primary and extra recipient can
        # legitimately be the same address, and nobody wants the report twice.
        seen: set[str] = set()
        unique: list[str] = []
        for email in recipients:
            if not email or email.lower() in seen:
                continue
            seen.add(email.lower())
            unique.append(email)
        return unique

    def watermarked_pdf(self, metrics: dict[str, Any], recipient: str) -> bytes | None:
        """Render a copy of the report stamped with the recipient's address.

        Per-recipient watermarking (SOW 4.4): each recipient's PDF carries their
        own address, so a leaked document is traceable to the copy it came from.
        """
        html = self.render_pdf_html({**metrics, "watermark_recipient": recipient})
        return self.pdf_service.generate_pdf_bytes(html)

    def deliver_request_pdf(self, request_row: dict[str, Any], pdf_bytes: bytes | None = None) -> list[str]:
        clean_recipients = self.recipients_for(request_row)
        metrics = request_row.get("metrics") or {}
        filename = (
            f"{request_row['product_type']}-"
            f"{_as_iso(request_row.get('period_start'))}-"
            f"{_as_iso(request_row.get('period_end'))}.pdf"
        )
        fallback_bytes: bytes | None = pdf_bytes
        for email in clean_recipients:
            # Each recipient gets their OWN watermarked render. If watermarking
            # fails we still deliver the un-watermarked copy rather than
            # withholding a paid-for report, but we say so loudly in the log.
            personalised: bytes | None = None
            if metrics:
                try:
                    personalised = self.watermarked_pdf(metrics, email)
                except Exception:
                    logger.warning(
                        "Per-recipient watermarking failed for request=%s; sending unwatermarked copy",
                        request_row.get("id"),
                        exc_info=True,
                    )
            if personalised is None:
                if fallback_bytes is None:
                    fallback_bytes = self.download_request_pdf(request_row)
                personalised = fallback_bytes
            encoded = base64.b64encode(personalised).decode("ascii")
            self.email_service.send(
                email,
                "b2b_intelligence_ready",
                {
                    "product_title": metrics.get("title") or self._product(request_row["product_type"])["title"],
                    "period_start": _as_iso(request_row.get("period_start")),
                    "period_end": _as_iso(request_row.get("period_end")),
                    "request_id": request_row["id"],
                    "b2b_url": f"{self.settings.FRONTEND_URL.rstrip('/')}/business-intelligence",
                },
                attachments=[{"filename": filename, "content": encoded}],
            )
        self.db.table("b2b_intelligence_requests").update(
            {"delivered_at": _utcnow(), "updated_at": _utcnow()}
        ).eq("id", request_row["id"]).execute()
        return clean_recipients

    def download_request_pdf(self, request_row: dict[str, Any]) -> bytes:
        return self.db.storage.from_("reports").download(self.storage_path(request_row))

    def list_requests(
        self,
        *,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_metrics: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        query = self.db.table("b2b_intelligence_requests").select("*", count="exact")
        if user_id:
            query = query.eq("user_id", user_id)
        result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        rows = result.data or []
        if not include_metrics:
            for row in rows:
                row.pop("metrics", None)
        return [self.add_download_url(row) for row in rows], result.count or len(rows)

    def get_request(self, request_id: str, *, user_id: str | None = None, include_metrics: bool = False) -> dict[str, Any] | None:
        query = self.db.table("b2b_intelligence_requests").select("*").eq("id", request_id)
        if user_id:
            query = query.eq("user_id", user_id)
        rows = query.limit(1).execute().data or []
        if not rows:
            return None
        row = rows[0]
        if not include_metrics:
            row.pop("metrics", None)
        return self.add_download_url(row)

    def add_download_url(self, row: dict[str, Any]) -> dict[str, Any]:
        if row.get("pdf_url") and row.get("status") == "completed":
            row["download_url"] = f"/api/b2b/requests/{row['id']}/pdf"
        else:
            row["download_url"] = None
        return row

    def storage_path(self, request_row: dict[str, Any]) -> str:
        return f"b2b/{request_row['user_id']}/{request_row['id']}.pdf"

    def notify_subscription_active(self, subscription: dict[str, Any]) -> None:
        user = self._get_user(subscription.get("user_id"))
        if not user or not user.get("email"):
            return
        self.email_service.send(
            user["email"],
            "b2b_subscription_active",
            {
                "product_title": self._product(subscription["product_type"])["title"],
                "delivery_frequency": subscription.get("delivery_frequency", "monthly"),
                # A newly-active subscriber's home is their dashboard.
                "b2b_url": f"{self.settings.FRONTEND_URL.rstrip('/')}/business-intelligence",
            },
        )

    def notify_subscription_updated(self, subscription: dict[str, Any], changed_fields: list[str]) -> None:
        user = self._get_user(subscription.get("user_id"))
        if not user or not user.get("email"):
            return
        self.email_service.send(
            user["email"],
            "b2b_subscription_updated",
            {
                "product_title": self._product(subscription["product_type"])["title"],
                "changed_fields": ", ".join(changed_fields),
                "delivery_frequency": subscription.get("delivery_frequency", "monthly"),
                "extra_recipient_email": subscription.get("extra_recipient_email") or "None",
                "next_delivery_at": _as_iso(subscription.get("next_delivery_at")) or "Not scheduled",
            },
        )

    def _load_signals(
        self,
        period_start: date,
        period_end: date,
        *,
        include_internal: bool = False,
    ) -> list[dict[str, Any]]:
        """Load consented, non-internal signals for the period.

        Consent gate (CRIT-2) and internal exclusion (R-9) are applied here so that
        EVERY downstream section — standard product or bespoke — inherits them and no
        composition can bypass them.
        """
        query = (
            self.db.table("production_signals")
            .select("*")
            .gte("submission_date", period_start)
            .lte("submission_date", period_end)
            .eq("b2b_consent", True)
        )
        if not include_internal:
            query = query.eq("is_internal", False)
        result = query.execute()
        return result.data or []

    @staticmethod
    def _headcount_stats(rows: list[dict[str, Any]]) -> dict[str, int]:
        """Summable headcount facts.

        Stores `sum` and `values_count` rather than a mean, so composing months
        can recompute the true mean. Averaging monthly averages would weight a
        month with 2 signals the same as a month with 200.
        """
        values: list[int] = []
        for row in rows:
            total = sum(int(row.get(key) or 0) for key in HEADCOUNT_KEYS)
            if total > 0:
                values.append(total)
        return {
            "values_count": len(values),
            "sum": sum(values),
            "max": max(values) if values else 0,
            "rows_total": len(rows),
        }

    def _headcount_section_from_stats(self, section: dict[str, Any]) -> dict[str, Any]:
        stats = section.get("stats") or {}
        title = section.get("title") or "Total Headcount Trend Analysis"
        values_count = int(stats.get("values_count") or 0)
        rows_total = int(stats.get("rows_total") or 0)
        if not values_count:
            return {
                "title": title,
                "summary": "No headcount metadata was available for the selected period.",
                "rows": [],
            }
        average = int(stats.get("sum") or 0) / values_count
        return {
            "title": title,
            "summary": f"Average declared headcount across anonymised productions: {average:.1f}",
            "rows": [
                {
                    "label": "Signals with headcount metadata",
                    "count": values_count,
                    "percentage": round(values_count / rows_total * 100, 1) if rows_total else None,
                },
                {"label": "Average declared headcount", "count": round(average, 1), "percentage": None},
                {"label": "Maximum declared headcount", "count": int(stats.get("max") or 0), "percentage": None},
            ],
        }

    def _counter_section(
        self,
        counter: Counter[str],
        title: str,
        suppressed_segments: list[dict[str, Any]],
        key: str,
    ) -> dict[str, Any]:
        total = sum(counter.values()) or 1
        rows: list[dict[str, Any]] = []
        for label, count in counter.most_common():
            if count < PRIVACY_MIN_SEGMENT:
                suppressed_segments.append(
                    {
                        "section": title,
                        "field": key,
                        "label": label,
                        "count": count,
                        "minimum": PRIVACY_MIN_SEGMENT,
                    }
                )
                continue
            rows.append(
                {
                    "label": label,
                    "count": count,
                    "percentage": round((count / total) * 100, 1),
                }
            )
        summary = (
            f"{len(rows)} segment(s) met the {PRIVACY_MIN_SEGMENT}-record display threshold."
            if rows
            else f"No segment met the {PRIVACY_MIN_SEGMENT}-record display threshold."
        )
        return {"title": title, "summary": summary, "rows": rows}

    @staticmethod
    def _is_missing(value: Any) -> bool:
        """A field the producer never filled in is not a segment.

        Counting blanks as an "Unspecified" bucket let a section with no data at
        all clear the display threshold — a Crew Size Distribution built from 12
        signals that declared no crew size would render "Unspecified: 12 (100%)".
        Excluding them also means percentages are shares of KNOWN values, which
        is what the sufficiency preview has always reported.
        """
        return value is None or (isinstance(value, str) and not value.strip())

    @staticmethod
    def _label(value: Any) -> str:
        if value is None:
            return "Unspecified"
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or "Unspecified"
        return str(value)

    @staticmethod
    def _numeric_band(value: Any) -> str:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return "Unspecified"
        if number <= 10:
            return "1-10"
        if number <= 50:
            return "11-50"
        if number <= 100:
            return "51-100"
        return "100+"

    @staticmethod
    def _parse_date(value: date | datetime | str | None) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not value:
            return _utcnow().date()
        return datetime.fromisoformat(str(value)).date()

    @staticmethod
    def _stripe_timestamp_to_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    @staticmethod
    def _clean_email(value: Any) -> str | None:
        if value is None:
            return None
        email = str(value).strip().lower()
        return email or None

    def _product(self, product_type: str) -> dict[str, Any]:
        product = B2B_PRODUCTS.get(product_type)
        if not product:
            raise ValueError(f"Unknown B2B product: {product_type}")
        return product

    def _get_user(self, user_id: str | None) -> dict[str, Any] | None:
        if not user_id:
            return None
        rows = self.db.table("users").select("id,email,company").eq("id", user_id).limit(1).execute().data or []
        return rows[0] if rows else None


def process_request_task(request_id: str, settings: Settings | None = None) -> None:
    """Background entry point for processing a B2B intelligence request.

    Must open its OWN database session: FastAPI tears down the request-scoped
    ``get_supabase`` session (closing it) when the endpoint returns its Response,
    which happens before Starlette runs background tasks. Reusing that session
    here would leave the task's connection un-managed (released only on GC).
    Mirrors the reports worker and ``run_due_b2b_auto_deliveries``.
    """
    settings = settings or get_settings()
    db = create_client()
    try:
        B2BService(db, settings).process_request(request_id)
    finally:
        db.close()


def run_b2b_monthly_aggregate_close(
    settings: Settings | None = None, *, today: date | None = None
) -> int:
    """Store the closed month's RAW aggregate for every product.

    Monthly is the atomic unit: quarterly and yearly reports compose these
    stored months instead of re-querying the signal pool. Runs before the
    delivery job so the months a delivery needs are already on disk.

    Idempotent — re-running a month upserts rather than duplicating, so a
    missed day self-heals on the next run.
    """
    settings = settings or get_settings()
    db = create_client()
    stored = 0
    try:
        service = B2BService(db, settings)
        reference = today or _utcnow().date()
        closed_month = month_start(add_months(datetime.combine(reference, datetime.min.time()), -1))
        for product_type in B2B_PRODUCTS:
            try:
                service.build_monthly_aggregate(product_type, closed_month)
                stored += 1
            except Exception:
                # One bad product must not stop the rest from being stored.
                logger.exception(
                    "Failed to store B2B monthly aggregate product=%s month=%s",
                    product_type,
                    closed_month.isoformat(),
                )
    finally:
        db.close()
    return stored


def run_due_b2b_auto_deliveries(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    db = create_client()
    generated = 0
    try:
        service = B2BService(db, settings)
        now = _utcnow()
        rows = (
            db.table("b2b_subscriptions")
            .select("*")
            .eq("status", "active")
            .execute()
            .data
            or []
        )
        for subscription in rows:
            due_at_raw = subscription.get("next_delivery_at")
            if not due_at_raw:
                continue
            due_at = datetime.fromisoformat(str(due_at_raw))
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            if due_at > now:
                continue
            user = service._get_user(subscription.get("user_id"))
            if not user or not user.get("email"):
                continue

            months = interval_months(subscription.get("delivery_frequency") or "monthly")
            # Scheduled deliveries cover COMPLETED calendar months. This lets them
            # compose from stored monthly aggregates (monthly is the atomic unit,
            # so quarterly = three stored months) and stops a signal dated on a
            # boundary day being counted in two consecutive reports, which the
            # previous [prev-month-day-N .. this-month-day-N] range allowed.
            period_start = month_start(add_months(due_at, -months))
            period_end = month_end(add_months(due_at, -1))
            request = service.create_intelligence_request(
                user_id=subscription["user_id"],
                user_email=user["email"],
                product_type=subscription["product_type"],
                period_start=period_start,
                period_end=period_end,
                extra_recipient_email=subscription.get("extra_recipient_email"),
                request_type="auto",
                subscription=subscription,
            )
            service.process_request(request["id"])
            next_delivery_at = add_months(due_at, months)
            while next_delivery_at <= now:
                next_delivery_at = add_months(next_delivery_at, months)
            db.table("b2b_subscriptions").update(
                {
                    "next_delivery_at": next_delivery_at,
                    "updated_at": now,
                }
            ).eq("id", subscription["id"]).execute()
            generated += 1
    finally:
        db.close()
    return generated
