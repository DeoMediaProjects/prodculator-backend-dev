from __future__ import annotations

import base64
import calendar
import logging
from collections import Counter
from datetime import date, datetime, timezone
from statistics import mean
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
        "price_gbp_cents": 60_000,
        "price_usd_cents": 75_000,
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
        "price_gbp_cents": 75_000,
        "price_usd_cents": 95_000,
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
        "price_gbp_cents": 60_000,
        "price_usd_cents": 75_000,
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
        "price_gbp_cents": 150_000,
        "price_usd_cents": 190_000,
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
            metrics = self.build_metrics(
                product_type=request_row["product_type"],
                period_start=self._parse_date(request_row["period_start"]),
                period_end=self._parse_date(request_row["period_end"]),
            )
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

    def build_metrics(self, *, product_type: str, period_start: date, period_end: date) -> dict[str, Any]:
        product = self._product(product_type)
        rows = self._load_signals(period_start, period_end)
        suppressed_segments: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {
            "product_type": product_type,
            "title": product["title"],
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "generated_at": _utcnow().isoformat(),
            "source_signal_count": len(rows),
            "thresholds": {
                "minimum_overall_records": PRIVACY_MIN_OVERALL,
                "minimum_segment_records": PRIVACY_MIN_SEGMENT,
            },
            "insufficient_data": len(rows) < PRIVACY_MIN_OVERALL,
            "sections": [],
            "suppressed_segments": suppressed_segments,
        }
        if len(rows) < PRIVACY_MIN_OVERALL:
            metrics["sections"].append(
                {
                    "title": "Privacy Threshold",
                    "summary": (
                        "The selected period does not contain enough anonymised "
                        "production signals to produce customer-facing segments. "
                        "Choose a wider date range."
                    ),
                    "rows": [],
                }
            )
            return metrics

        sections: list[dict[str, Any]] = []
        if product_type == "camera_equipment":
            sections.extend(
                [
                    self._distribution_section(rows, "territory", "Production Volume by Territory", suppressed_segments),
                    self._distribution_section(rows, "camera_equipment", "Camera & Equipment Mix", suppressed_segments, flatten=True),
                    self._distribution_section(rows, "format", "Production Type Distribution", suppressed_segments),
                    self._distribution_section(rows, "genres", "Genre Mix", suppressed_segments, flatten=True),
                    self._month_section(rows, suppressed_segments),
                ]
            )
        elif product_type == "production_services":
            sections.extend(
                [
                    self._numeric_band_section(rows, "crew_size", "Crew Size Distribution", suppressed_segments),
                    self._headcount_section(rows),
                    self._distribution_section(rows, "budget_range", "Budget Range Breakdown", suppressed_segments),
                    self._distribution_section(rows, "territory", "Territory Demand Mix", suppressed_segments),
                    self._distribution_section(rows, "format", "Format Distribution", suppressed_segments),
                ]
            )
        elif product_type == "crew_casting":
            sections.extend(
                [
                    self._numeric_band_section(rows, "principal_cast", "Principal Cast Demand", suppressed_segments),
                    self._numeric_band_section(rows, "supporting_cast", "Supporting Cast Demand", suppressed_segments),
                    self._numeric_band_section(rows, "background_extras", "Extras Demand", suppressed_segments),
                    self._distribution_section(rows, "genres", "Genre Mix", suppressed_segments, flatten=True),
                    self._month_section(rows, suppressed_segments, title="Submission Timing Clusters"),
                ]
            )
        else:
            sections.extend(
                [
                    self._distribution_section(rows, "territory", "Territory Demand Distribution", suppressed_segments),
                    self._distribution_section(rows, "budget_range", "Budget Range Mix", suppressed_segments),
                    self._distribution_section(rows, "genres", "Genre Trend Signals", suppressed_segments, flatten=True),
                    self._distribution_section(rows, "format", "Format Mix", suppressed_segments),
                    self._month_section(rows, suppressed_segments, title="Monthly Planning Volume"),
                ]
            )

        metrics["sections"] = [section for section in sections if section]
        return metrics

    def render_pdf_html(self, metrics: dict[str, Any]) -> str:
        try:
            template = self.pdf_service.env.get_template("b2b_intelligence.html")
            return template.render(metrics=metrics)
        except TemplateNotFound:
            return f"<html><body><pre>{metrics}</pre></body></html>"

    def deliver_request_pdf(self, request_row: dict[str, Any], pdf_bytes: bytes | None = None) -> list[str]:
        if pdf_bytes is None:
            pdf_bytes = self.download_request_pdf(request_row)
        recipients = [
            request_row.get("recipient_email"),
            self._clean_email(request_row.get("extra_recipient_email")),
        ]
        clean_recipients = [email for email in recipients if email]
        encoded = base64.b64encode(pdf_bytes).decode("ascii")
        metrics = request_row.get("metrics") or {}
        filename = (
            f"{request_row['product_type']}-"
            f"{_as_iso(request_row.get('period_start'))}-"
            f"{_as_iso(request_row.get('period_end'))}.pdf"
        )
        for email in clean_recipients:
            self.email_service.send(
                email,
                "b2b_intelligence_ready",
                {
                    "product_title": metrics.get("title") or self._product(request_row["product_type"])["title"],
                    "period_start": _as_iso(request_row.get("period_start")),
                    "period_end": _as_iso(request_row.get("period_end")),
                    "request_id": request_row["id"],
                    "b2b_url": f"{self.settings.FRONTEND_URL.rstrip('/')}/b2b",
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
                "b2b_url": f"{self.settings.FRONTEND_URL.rstrip('/')}/b2b",
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

    def _load_signals(self, period_start: date, period_end: date) -> list[dict[str, Any]]:
        result = (
            self.db.table("production_signals")
            .select("*")
            .gte("submission_date", period_start)
            .lte("submission_date", period_end)
            .execute()
        )
        return result.data or []

    def _distribution_section(
        self,
        rows: list[dict[str, Any]],
        key: str,
        title: str,
        suppressed_segments: list[dict[str, Any]],
        *,
        flatten: bool = False,
    ) -> dict[str, Any]:
        counter: Counter[str] = Counter()
        for row in rows:
            value = row.get(key)
            values = value if flatten and isinstance(value, list) else [value]
            for entry in values:
                label = self._label(entry)
                counter[label] += 1
        return self._counter_section(counter, title, suppressed_segments, key)

    def _numeric_band_section(
        self,
        rows: list[dict[str, Any]],
        key: str,
        title: str,
        suppressed_segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        counter: Counter[str] = Counter()
        for row in rows:
            counter[self._numeric_band(row.get(key))] += 1
        return self._counter_section(counter, title, suppressed_segments, key)

    def _month_section(
        self,
        rows: list[dict[str, Any]],
        suppressed_segments: list[dict[str, Any]],
        *,
        title: str = "Monthly Upload Volume",
    ) -> dict[str, Any]:
        counter: Counter[str] = Counter()
        for row in rows:
            submitted = self._parse_date(row.get("submission_date"))
            counter[submitted.strftime("%Y-%m")] += 1
        return self._counter_section(counter, title, suppressed_segments, "submission_month")

    def _headcount_section(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        values: list[int] = []
        for row in rows:
            total = sum(
                int(row.get(key) or 0)
                for key in ("crew_size", "principal_cast", "supporting_cast", "background_extras")
            )
            if total > 0:
                values.append(total)
        if not values:
            return {
                "title": "Total Headcount Trend Analysis",
                "summary": "No headcount metadata was available for the selected period.",
                "rows": [],
            }
        return {
            "title": "Total Headcount Trend Analysis",
            "summary": f"Average declared headcount across anonymised productions: {mean(values):.1f}",
            "rows": [
                {"label": "Signals with headcount metadata", "count": len(values), "percentage": round(len(values) / len(rows) * 100, 1)},
                {"label": "Average declared headcount", "count": round(mean(values), 1), "percentage": None},
                {"label": "Maximum declared headcount", "count": max(values), "percentage": None},
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
            period_start = add_months(due_at, -months).date()
            period_end = due_at.date()
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
