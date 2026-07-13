from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient

_TABLE = "incentive_programs"
_SYNC_SETTINGS_TABLE = "sync_settings"
_PENDING_CHANGES_TABLE = "pending_changes"
_RESOURCE_TYPE = "incentives"

# ── Incentive field maps (camelCase ↔ snake_case) ────────────────────────────

_CAMEL_TO_SNAKE: dict[str, str] = {
    "lastUpdated": "last_updated",
    "sourceUrl": "source_url",
    "autoSyncEnabled": "auto_sync_enabled",
    "lastAutoCheck": "last_auto_check",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    # Enriched territory data fields
    "rateGross": "rate_gross",
    "rateNet": "rate_net",
    "rateType": "rate_type",
    "rateTierJson": "rate_tier_json",
    "capAmount": "cap_amount",
    "capCurrency": "cap_currency",
    "capPerPerson": "cap_per_person",
    "capPerPersonCurrency": "cap_per_person_currency",
    "qualifyingSpendMin": "qualifying_spend_min",
    "qualifyingSpendCapPct": "qualifying_spend_cap_pct",
    "qualifyingSpendCurrency": "qualifying_spend_currency",
    "paymentTimelineDaysMin": "payment_timeline_days_min",
    "paymentTimelineDaysMax": "payment_timeline_days_max",
    "paymentTimelineNotes": "payment_timeline_notes",
    "eligibilityRulesJson": "eligibility_rules_json",
    "expiryDate": "expiry_date",
    "warningsJson": "warnings_json",
    "lastVerifiedAt": "last_verified_at",
    "sourceName": "source_name",
    # Regional incentive stacking fields
    "parentTerritory": "parent_territory",
    "stackingGroup": "stacking_group",
    "stackableWith": "stackable_with",
    # Producer eligibility / nationality fields
    "nationalityRequirements": "nationality_requirements",
    "coProductionEligible": "co_production_eligible",
    "coProductionTreaties": "co_production_treaties",
    "spvEligible": "spv_eligible",
    # v4 source-of-truth fields (notes/authority/confidence/region are
    # single-word and need no mapping)
    "rateGrossDisplay": "rate_gross_display",
    "rateNetDisplay": "rate_net_display",
    "rebateCapDisplay": "rebate_cap_display",
    "perPersonCapDisplay": "per_person_cap_display",
    "paymentTimeline": "payment_timeline",
    "aiRule": "ai_rule",
    "budgetEligibilityCeiling": "budget_eligibility_ceiling",
    "annualProgrammeCap": "annual_programme_cap",
    "mechanismPattern": "mechanism_pattern",
    "qsBasis": "qs_basis",
    "verificationStatus": "verification_status",
    "calcFormula": "calc_formula",
    "regionalFundsNote": "regional_funds_note",
    "capType": "cap_type",
    "bankPts": "bank_pts",
}
_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _CAMEL_TO_SNAKE.items()}

# Pending changes field maps
_PC_CAMEL_TO_SNAKE: dict[str, str] = {
    "currentValue": "current_value",
    "detectedValue": "detected_value",
    "resourceId": "resource_id",
    "resourceType": "resource_type",
    "createdAt": "created_at",
    "resolvedAt": "resolved_at",
    "resolvedBy": "resolved_by",
    "recordLabel": "record_label",
}
_PC_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _PC_CAMEL_TO_SNAKE.items()}

# Sync settings field maps
_SS_CAMEL_TO_SNAKE: dict[str, str] = {
    "lastSyncAt": "last_sync_at",
    "nextScheduledCheck": "next_scheduled",
    "resourceType": "resource_type",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}
_SS_SNAKE_TO_CAMEL: dict[str, str] = {v: k for k, v in _SS_CAMEL_TO_SNAKE.items()}


def _incentive_to_db(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in payload.items():
        result[_CAMEL_TO_SNAKE.get(k, k)] = v
    if result.get("id") == "":
        result.pop("id")
    return result


def _incentive_from_db(row: dict[str, Any]) -> dict[str, Any]:
    return {_SNAKE_TO_CAMEL.get(k, k): v for k, v in row.items()}


def _pending_change_from_db(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat()
        result[_PC_SNAKE_TO_CAMEL.get(k, k)] = v
    return result


def _sync_settings_from_db(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat()
        result[_SS_SNAKE_TO_CAMEL.get(k, k)] = v
    return result


class IncentivesService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    # ── Admin CRUD ───────────────────────────────────────────────────────────

    def list_for_admin(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        self._materialize_approved_changes_without_resource()
        count = self.supabase.table(_TABLE).select("*", count="exact", head=True).execute().count or 0
        rows = (
            self.supabase.table(_TABLE)
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
            .data
            or []
        )
        return [_incentive_from_db(r) for r in rows], count

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        db_payload = _incentive_to_db(payload)
        db_payload.setdefault("id", str(uuid4()))
        db_payload["created_at"] = now
        db_payload["updated_at"] = now
        result = self.supabase.table(_TABLE).insert(db_payload).select("*").single().execute()
        return _incentive_from_db(result.data)

    def update(self, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db_payload = _incentive_to_db(payload)
        db_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = (
            self.supabase.table(_TABLE)
            .update(db_payload)
            .eq("id", row_id)
            .select("*")
            .single()
            .execute()
        )
        return _incentive_from_db(result.data)

    def delete(self, row_id: str) -> None:
        self.supabase.table(_TABLE).delete().eq("id", row_id).execute()

    # ── Qualifying spend calculator ───────────────────────────────────────────

    def calculate_qualifying_spend(
        self,
        *,
        budget_amount: float,
        budget_currency: str,
        territory: str,
        program: str,
    ) -> dict[str, Any]:
        """What a production can actually claim under one programme.

        SINGLE SOURCE OF TRUTH: the maths is ReportValidator._compute_corrected_rebate
        — the same function the report pipeline calls. This method only loads the
        row, converts currency, and formats the result. No incentive math here.
        """
        from app.modules.reports.helpers import (
            STATIC_FX_TO_GBP,
            currency_symbol,
            format_cap,
        )
        from app.modules.reports.validator import ReportValidator

        rows = (
            self.supabase.table(_TABLE)
            .select("*")
            .eq("territory", territory)
            .execute()
            .data
            or []
        )
        row = next(
            (r for r in rows if (r.get("program") or "") == program), None
        )
        if row is None:
            raise ValueError(f"No programme '{program}' found for {territory}")

        # ── Status gate: suspended / no-programme / verify-required rows never
        # produce a confident number (waterfall_steps rule: defined no-chart states)
        status = (row.get("status") or "").lower()
        base = {
            "territory": territory,
            "program": program,
            "status": status,
            "rateGrossDisplay": row.get("rate_gross_display"),
            "rateNetDisplay": row.get("rate_net_display"),
            "mechanismPattern": row.get("mechanism_pattern"),
            "verificationStatus": row.get("verification_status"),
            "confidence": row.get("confidence"),
            "budgetEligibilityCeiling": row.get("budget_eligibility_ceiling"),
            "qsBasis": row.get("qs_basis"),
            "calcFormula": row.get("calc_formula"),
        }
        if status != "active":
            reason = {
                "suspended": "Programme is SUSPENDED — no claim can be modelled.",
                "no_programme": "No formal incentive programme — nothing to claim.",
                "admin_verify_required": (
                    "Programme requires admin verification before figures can be relied on."
                ),
            }.get(status, f"Programme status is '{status}' — not modelled.")
            return {**base, "available": False, "refusalReason": reason}

        # ── Budget → GBP (validator computes in GBP). Live FX with static fallback.
        ccy = (budget_currency or "GBP").upper()
        fx_note = None
        if ccy == "GBP":
            budget_gbp = budget_amount
            rate_to_ccy = 1.0
        else:
            rate_to_ccy = None
            try:
                from app.core.config import get_settings
                from app.modules.fx.service import FXService

                fetched = FXService(get_settings()).get_rates_batch("GBP", [ccy])
                if ccy in fetched:
                    rate_to_ccy, rate_date = fetched[ccy]
                    fx_note = f"£1 = {rate_to_ccy:g} {ccy} (as of {rate_date})"
            except Exception:
                rate_to_ccy = None
            if not rate_to_ccy:
                rate_to_ccy = STATIC_FX_TO_GBP.get(ccy)
                if not rate_to_ccy:
                    raise ValueError(f"No FX rate available for {ccy}")
                fx_note = f"£1 = {rate_to_ccy:g} {ccy} (static fallback rate)"
            budget_gbp = budget_amount / rate_to_ccy

        # Alternatives for ceiling-switching must be full-budget programmes:
        # PDV/labour-basis and supplementary credits apply to a spend subset
        # and must never be presented as the replacement for a capped-out
        # primary programme.
        full_budget_rows = [
            r for r in rows
            if not r.get("is_supplementary")
            and (r.get("qualifying_spend_type") or "total").lower()
            not in ("pdv", "labour")
        ]
        if row not in full_budget_rows:
            full_budget_rows = full_budget_rows + [row]

        corrected = ReportValidator._compute_corrected_rebate(
            row, budget_gbp, {territory: full_budget_rows}
        )

        # When the ceiling switches programmes, present EXACTLY what selecting
        # the alternative directly would show — recompute on the alternative
        # row alone so "X applies instead" figures always equal a direct
        # calculation of X.
        if corrected and corrected.get("switched_programme"):
            switched_name = corrected["switched_programme"]
            alt_row = next(
                (r for r in full_budget_rows
                 if (r.get("program") or "") == switched_name),
                None,
            )
            if alt_row is not None:
                direct = ReportValidator._compute_corrected_rebate(
                    alt_row, budget_gbp, {territory: [alt_row]}
                )
                if direct is not None:
                    direct["switched_programme"] = switched_name
                    direct["programme_note"] = corrected.get("programme_note")
                    corrected = direct
        if corrected is None:
            return {
                **base,
                "available": False,
                "refusalReason": (
                    "No honest figure can be computed for this programme at this "
                    "budget (missing sourced rate or qualifying-spend basis)."
                ),
            }

        sym = currency_symbol(ccy)

        def money(gbp_val: float | None) -> str | None:
            if gbp_val is None:
                return None
            return f"{sym}{gbp_val * rate_to_ccy:,.0f}"

        switched = corrected.get("switched_programme")
        result: dict[str, Any] = {
            **base,
            "available": not switched,
            "programmeNote": corrected.get("programme_note"),
            "switchedProgramme": switched,
            "fxNote": fx_note,
            "currency": ccy,
            "budget": money(budget_gbp),
            "qualifyingSpend": money(corrected.get("qualifying_spend_before_atl")),
            "netQualifyingSpend": money(corrected.get("qualifying_spend")),
            "qualifyingSpendPct": f"{corrected.get('qualifying_spend_pct', 0):.0f}%",
            "grossRebate": money(corrected.get("gross_rebate")),
            "netRebate": money(corrected.get("net_rebate")),
            "netBudget": money(budget_gbp - (corrected.get("net_rebate") or 0)),
            "rateGross": f"{corrected.get('rate_gross'):g}%" if corrected.get("rate_gross") else None,
            "rateNet": f"{corrected.get('rate_net'):g}%" if corrected.get("rate_net") else None,
            "notes": [
                n for n in (
                    corrected.get("qualifying_spend_note"),
                    corrected.get("atl_deduction_note"),
                    corrected.get("rebate_cap_note"),
                ) if n
            ],
        }
        if switched:
            ceiling = row.get("budget_eligibility_ceiling")
            cap_txt = (
                ceiling
                or format_cap(row.get("cap_amount"), row.get("cap_currency") or "GBP")
            )
            result["refusalReason"] = (
                f"Budget exceeds the eligibility ceiling ({cap_txt}). "
                f"NOT AVAILABLE at this budget — {switched} applies instead; "
                f"the figures below model {switched}."
            )
        return result

    # ── Sync status ──────────────────────────────────────────────────────────

    def get_sync_status(self) -> dict[str, Any]:
        # Count distinct territories
        all_rows = self.supabase.table(_TABLE).select("territory").execute().data or []
        territories = len({r.get("territory") for r in all_rows if r.get("territory")})

        # Count pending changes
        pending_count = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .select("*", count="exact", head=True)
            .eq("resource_type", _RESOURCE_TYPE)
            .eq("status", "pending")
            .execute()
            .count or 0
        )

        # Get sync settings for last check date
        settings_row = self._get_or_create_sync_settings()
        last_sync = settings_row.get("last_sync_at")
        next_scheduled = settings_row.get("next_scheduled")

        days_since = 0
        if last_sync:
            try:
                last_dt = datetime.fromisoformat(str(last_sync))
                days_since = (datetime.now(timezone.utc) - last_dt).days
            except (ValueError, TypeError):
                days_since = 0

        return {
            "territoriesSyncing": territories,
            "pendingChanges": pending_count,
            "daysSinceLastCheck": days_since,
            "nextScheduledCheck": str(next_scheduled) if next_scheduled else None,
        }

    # ── Pending changes ──────────────────────────────────────────────────────

    def get_pending_changes(self) -> list[dict[str, Any]]:
        rows = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .select("*")
            .eq("resource_type", _RESOURCE_TYPE)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .execute()
            .data or []
        )
        return [_pending_change_from_db(r) for r in rows]

    def approve_change(self, change_id: str, admin_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()

        # Fetch the pending change
        change = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .select("*")
            .eq("id", change_id)
            .single()
            .execute()
            .data
        )
        if not change:
            raise ValueError("Pending change not found")

        # Apply the change to the resource row. If resource_id is missing
        # (newly discovered incentive), create/reuse a row first.
        resource_id = change.get("resource_id")
        field = change.get("field")
        detected_value = change.get("detected_value")
        if field and detected_value is not None:
            db_field = _CAMEL_TO_SNAKE.get(field) or field
            if not resource_id:
                resource_id = self._get_or_create_resource_id_for_change(change, now)
            self.supabase.table(_TABLE).update(
                {db_field: detected_value, "updated_at": now}
            ).eq("id", resource_id).execute()

        # Mark the change as approved
        update_payload: dict[str, Any] = {
            "status": "approved",
            "resolved_at": now,
            "resolved_by": admin_id,
        }
        if resource_id:
            update_payload["resource_id"] = resource_id

        result = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .update(update_payload)
            .eq("id", change_id)
            .select("*")
            .single()
            .execute()
        )
        return _pending_change_from_db(result.data)

    def reject_change(self, change_id: str, admin_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        result = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .update({"status": "rejected", "resolved_at": now, "resolved_by": admin_id})
            .eq("id", change_id)
            .select("*")
            .single()
            .execute()
        )
        return _pending_change_from_db(result.data)

    def _get_or_create_resource_id_for_change(self, change: dict[str, Any], now: str) -> str:
        territory = change.get("territory")
        source_url = change.get("source")

        # Reuse a recently created row for the same territory/source to keep
        # multi-field approvals (e.g. rate + cap) on one resource.
        query = self.supabase.table(_TABLE).select("id").order("created_at", desc=True).limit(1)
        if territory:
            query = query.eq("territory", territory)
        if source_url:
            query = query.eq("source_url", source_url)
        existing = query.execute().data or []
        if existing:
            return existing[0]["id"]

        row_id = str(uuid4())
        create_payload: dict[str, Any] = {
            "id": row_id,
            "territory": territory,
            "source_url": source_url,
            "created_at": now,
            "updated_at": now,
        }
        self.supabase.table(_TABLE).insert(create_payload).execute()
        return row_id

    def _materialize_approved_changes_without_resource(self) -> None:
        rows = (
            self.supabase.table(_PENDING_CHANGES_TABLE)
            .select("*")
            .eq("resource_type", _RESOURCE_TYPE)
            .eq("status", "approved")
            .eq("resource_id", None)
            .order("created_at", desc=False)
            .execute()
            .data
            or []
        )
        if not rows:
            return

        for change in rows:
            field = change.get("field")
            detected_value = change.get("detected_value")
            if not field or detected_value is None:
                continue

            now = datetime.now(timezone.utc).isoformat()
            resource_id = self._get_or_create_resource_id_for_change(change, now)
            db_field = _CAMEL_TO_SNAKE.get(field) or field
            self.supabase.table(_TABLE).update(
                {db_field: detected_value, "updated_at": now}
            ).eq("id", resource_id).execute()
            self.supabase.table(_PENDING_CHANGES_TABLE).update({"resource_id": resource_id}).eq(
                "id", change["id"]
            ).execute()

    # ── Sync trigger ─────────────────────────────────────────────────────────

    def trigger_sync(self) -> dict[str, Any]:
        from app.core.config import get_settings
        from app.modules.scraper.service import ScraperService

        settings = get_settings()
        scraper = ScraperService(self.supabase, settings)
        return scraper.run_for_resource(_RESOURCE_TYPE, triggered_by="admin")

    # ── Sync settings ────────────────────────────────────────────────────────

    def get_sync_settings(self) -> dict[str, Any]:
        row = self._get_or_create_sync_settings()
        return _sync_settings_from_db(row)

    def update_sync_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self._get_or_create_sync_settings()
        now = datetime.now(timezone.utc).isoformat()

        update_data: dict[str, Any] = {"updated_at": now}
        if "schedule" in payload:
            update_data["schedule"] = payload["schedule"]
        if "enabled" in payload:
            update_data["enabled"] = payload["enabled"]

        # Compute next_scheduled based on schedule
        if "schedule" in payload and payload["schedule"]:
            update_data["next_scheduled"] = self._compute_next_scheduled(payload["schedule"])

        result = (
            self.supabase.table(_SYNC_SETTINGS_TABLE)
            .update(update_data)
            .eq("id", settings["id"])
            .select("*")
            .single()
            .execute()
        )
        return _sync_settings_from_db(result.data)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_or_create_sync_settings(self) -> dict[str, Any]:
        rows = (
            self.supabase.table(_SYNC_SETTINGS_TABLE)
            .select("*")
            .eq("resource_type", _RESOURCE_TYPE)
            .execute()
            .data or []
        )
        if rows:
            return rows[0]

        # Auto-create default settings row
        now = datetime.now(timezone.utc).isoformat()
        default = {
            "id": str(uuid4()),
            "resource_type": _RESOURCE_TYPE,
            "schedule": "monthly",
            "enabled": True,
            "last_sync_at": None,
            "next_scheduled": None,
            "created_at": now,
            "updated_at": now,
        }
        result = self.supabase.table(_SYNC_SETTINGS_TABLE).insert(default).select("*").single().execute()
        return result.data

    @staticmethod
    def _compute_next_scheduled(schedule: str) -> str:
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        days_map = {
            "monthly": 30,
            "quarterly": 90,
            "biannual": 182,
            "annual": 365,
        }
        days = days_map.get(schedule, 30)
        return (now + timedelta(days=days)).isoformat()
