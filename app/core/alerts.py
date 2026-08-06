"""Operational alerts to the admin team (handoff §4.5).

Four failure paths must reach a human: Business Intelligence generation, B2C
report generation, Stripe webhook processing, and scheduled jobs. Before this
module only the first did, so a failed payment webhook — including the
already-CRITICAL-logged "user charged but cannot be upgraded" case — was
invisible until a customer complained.

Three rules the callers rely on:

**Never raises.** An alert is a side effect of something that has already gone
wrong. If alerting also fails, that is logged and swallowed; it never turns a
handled failure into an unhandled one, and never changes the outcome of the
operation that triggered it.

**Throttled per alert key.** A systemic outage fires the same failure over and
over. The first occurrence in a window sends immediately; the rest are counted,
and the next send after the window reports how many were suppressed. So an
outage costs a handful of emails that say "and 4,000 more", not 4,000 emails.
Callers choose the key's granularity, which is where the judgement lives: a
report failure keys on the failing step (one alert per broken step, not per
customer), while a payment webhook keys on the Stripe event id (one alert per
charged customer, not per delivery retry).

The throttle is per process. With N workers the ceiling is N emails per key per
window rather than one — bounded, and it needs no shared state that could fail
on the alert path. Correctness here means "cannot flood", not "exactly once".

**Sentry alongside, not instead.** Every alert is also captured for Sentry when
a DSN is configured, so the alert rules there see the same events. Email is for
the ones a person must act on; Sentry is the searchable record.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Any

logger = logging.getLogger(__name__)

# Alert categories. The value is the throttle key prefix and the Sentry tag, so
# adding a category cannot silently share another's throttle bucket.
ALERT_BI_GENERATION = "bi_generation"
ALERT_BI_HELD = "bi_held"
ALERT_REPORT_GENERATION = "report_generation"
ALERT_STRIPE_WEBHOOK = "stripe_webhook"
ALERT_SCHEDULER_JOB = "scheduler_job"

# Default window. 15 minutes is short enough that a resolved-then-recurring
# problem re-alerts within the hour, and long enough that a tight retry loop
# collapses into one email.
DEFAULT_THROTTLE_SECONDS = 900

# Payment webhooks get a long window with an event-scoped throttle key rather
# than the short default. Two facts drive this: each failing event may name a
# different charged customer, so a rollup count is not actionable and the alert
# must not be swallowed; and Stripe retries the same event for days, so an
# event-scoped key is what stops those retries from re-alerting on every
# delivery. One day means a genuinely stuck event re-surfaces daily as a
# reminder instead of either flooding or going quiet.
WEBHOOK_THROTTLE_SECONDS = 86_400

_SEVERITY_LABELS = {"critical": "Critical", "error": "Error", "warning": "Warning"}


@dataclass
class _ThrottleEntry:
    window_started: float
    suppressed: int = 0


@dataclass
class _ThrottleState:
    """Per-process throttle. Bounded in size so a pathological key space (an
    alert key built from a resource id, say) cannot grow without limit."""

    max_keys: int = 512
    entries: dict[str, _ThrottleEntry] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def check(self, key: str, window_seconds: int) -> tuple[bool, int]:
        """Return ``(should_send, suppressed_since_last_send)``."""
        now = monotonic()
        with self.lock:
            entry = self.entries.get(key)
            if entry is None or (now - entry.window_started) >= window_seconds:
                suppressed = entry.suppressed if entry else 0
                if len(self.entries) >= self.max_keys and key not in self.entries:
                    self._evict_oldest(now, window_seconds)
                self.entries[key] = _ThrottleEntry(window_started=now)
                return True, suppressed
            entry.suppressed += 1
            return False, entry.suppressed

    def _evict_oldest(self, now: float, window_seconds: int) -> None:
        """Drop expired entries; if none are expired, drop the oldest.

        Called with the lock held. Evicting an unexpired entry can cost one
        extra email later, which is the right trade against unbounded growth.
        """
        expired = [
            k for k, e in self.entries.items()
            if (now - e.window_started) >= window_seconds
        ]
        if expired:
            for key in expired:
                self.entries.pop(key, None)
            return
        oldest = min(self.entries, key=lambda k: self.entries[k].window_started)
        self.entries.pop(oldest, None)

    def reset(self) -> None:
        with self.lock:
            self.entries.clear()


_throttle = _ThrottleState()


def reset_throttle() -> None:
    """Clear throttle state. For tests, and for a process that wants a clean
    slate after deliberately quietening a known outage."""
    _throttle.reset()


def _recipient(settings: Any) -> str:
    """Ops recipient: the dedicated alert address, else the contact address."""
    return (
        (getattr(settings, "ADMIN_ALERT_EMAIL", "") or "")
        or (getattr(settings, "B2B_ADMIN_ALERT_EMAIL", "") or "")
        or (getattr(settings, "CONTACT_EMAIL", "") or "")
    ).strip()


def _capture_to_sentry(
    category: str, heading: str, severity: str, details: dict[str, Any] | None,
) -> None:
    """Mirror the alert into Sentry so its alert rules see the same event.

    A missing SDK or an unconfigured DSN is not an error — sentry_sdk's capture
    is a no-op without a DSN, and the import guard covers environments that do
    not install it at all.
    """
    try:
        import sentry_sdk
    except ImportError:
        return
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("alert_category", category)
            scope.set_level("error" if severity in ("critical", "error") else "warning")
            for key, value in (details or {}).items():
                scope.set_extra(str(key), value)
            sentry_sdk.capture_message(f"[{category}] {heading}")
    except Exception:
        logger.debug("Sentry capture failed for alert %s", category, exc_info=True)


def send_admin_alert(
    *,
    category: str,
    heading: str,
    message: str,
    details: dict[str, Any] | None = None,
    settings: Any = None,
    email_service: Any = None,
    throttle_key: str | None = None,
    throttle_seconds: int | None = None,
    severity: str = "error",
    recipient: str | None = None,
    template: str = "admin_alert",
) -> bool:
    """Alert the admin team about an operational failure. Returns True if an
    email was sent, False if it was throttled, unconfigured, or failed.

    *category* is one of the ALERT_* constants. *throttle_key* defaults to the
    category, and should be narrowed (e.g. ``f"{category}:{job_id}"``) when
    distinct failures under one category should not silence each other.
    *template* lets a caller keep wording specific to its own failure mode (the
    BI path does); the throttling and Sentry behaviour is identical either way.
    """
    try:
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()

        _capture_to_sentry(category, heading, severity, details)

        key = throttle_key or category
        window = (
            throttle_seconds
            if throttle_seconds is not None
            else getattr(settings, "ADMIN_ALERT_THROTTLE_SECONDS", DEFAULT_THROTTLE_SECONDS)
        )

        should_send, suppressed = _throttle.check(key, window)
        if not should_send:
            logger.info(
                "Admin alert throttled: category=%s key=%s suppressed_in_window=%d",
                category, key, suppressed,
            )
            return False

        recipient = recipient or _recipient(settings)
        if not recipient:
            # Logged at WARNING, not silently dropped: an unroutable alert means
            # the failure it describes has no path to a human.
            logger.warning(
                "Admin alert has no recipient configured (set ADMIN_ALERT_EMAIL) "
                "— not sent: category=%s heading=%s",
                category, heading,
            )
            return False

        body = dict(details or {})
        if suppressed:
            body["Suppressed since last alert"] = (
                f"{suppressed} further occurrence(s) of this alert were "
                f"collapsed into this one"
            )

        if email_service is None:
            from app.modules.email.service import EmailService

            email_service = EmailService(settings)

        email_service.send(
            recipient,
            template,
            {
                "heading": heading,
                "message": message,
                "details": body,
                "severity": _SEVERITY_LABELS.get(severity, "Error"),
                "category": category,
            },
        )
        logger.info(
            "Admin alert sent: category=%s recipient=%s suppressed_rollup=%d",
            category, recipient, suppressed,
        )
        return True
    except Exception:
        # The caller is already handling a failure. Alerting must not add a
        # second one on top of it.
        logger.warning(
            "Failed to send admin alert: category=%s heading=%s",
            category, heading, exc_info=True,
        )
        return False
