"""Territory comparison service.

Fetches incentive + crew cost data for selected territories and returns
structured comparison data without requiring a budget input.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.core.territories import Territory, resolve_territory
from app.modules.fx.service import FXService, TERRITORY_CURRENCY
from app.modules.reports.helpers import (
    best_incentive,
    index_incentives_by_territory,
    format_rate,
    format_cap,
    to_float,
    currency_symbol,
)
from app.modules.territories.schemas import (
    TerritoryCompareItem,
    TerritoryCompareResponse,
    TerritoryListItem,
    TerritoryListResponse,
    IncentiveInfo,
    CrewCostInfo,
    TerritoryProfileInfo,
)

logger = logging.getLogger(__name__)

# UK baseline for crew cost display (same as calculator)
_UK_BASELINE_DAY_RATE = 900


class TerritoryService:
    """Provides territory reference data for side-by-side comparison."""

    def __init__(self, supabase: DatabaseClient, settings: Settings) -> None:
        self.supabase = supabase
        self.settings = settings
        self.fx = FXService(settings)

    # ── Public API ────────────────────────────────────────────────────────

    def list_territories(self) -> TerritoryListResponse:
        """Return all territories from the registry."""
        items = _build_territory_list()
        return TerritoryListResponse(territories=items)

    def compare_territories(
        self,
        territory_labels: list[str],
        display_currency: str = "GBP",
    ) -> TerritoryCompareResponse:
        """Return comparison data for up to 4 territories."""
        # Fetch raw data
        incentives = self._fetch_table("incentive_programs")
        crew_costs_raw = self._fetch_table("crew_costs")
        profiles_by_territory = {
            row["territory"]: row
            for row in self._fetch_table("territory_profiles")
            if row.get("territory")
        }

        territory_incentives = index_incentives_by_territory(incentives)

        # FX-enrich crew costs to GBP
        crew_costs = self._fx_enrich_crew_costs(crew_costs_raw)

        # Index crew costs by territory
        crew_by_territory: dict[str, list[dict]] = {}
        for row in crew_costs:
            for key in (row.get("territory") or "", row.get("country") or ""):
                if not key:
                    continue
                crew_by_territory.setdefault(key, []).append(row)
                t_obj = resolve_territory(key)
                if t_obj:
                    canonical = t_obj.label
                    if canonical != key:
                        crew_by_territory.setdefault(canonical, []).append(row)
                    if t_obj.parent and t_obj.parent.label != key:
                        crew_by_territory.setdefault(t_obj.parent.label, []).append(row)

        # Build comparison items
        items: list[TerritoryCompareItem] = []
        for label in territory_labels[:4]:
            t_obj = resolve_territory(label)
            if not t_obj:
                continue
            item = self._build_compare_item(
                t_obj, territory_incentives, crew_by_territory, display_currency,
                profiles_by_territory,
            )
            items.append(item)

        available = _build_territory_list()
        return TerritoryCompareResponse(territories=items, available_territories=available)

    # ── Private helpers ───────────────────────────────────────────────────

    def _build_compare_item(
        self,
        t_obj: Territory,
        territory_incentives: dict[str, list[dict]],
        crew_by_territory: dict[str, list[dict]],
        display_currency: str,
        profiles_by_territory: dict[str, dict] | None = None,
    ) -> TerritoryCompareItem:
        label = t_obj.label
        iso = t_obj.iso
        level: str = "regional" if t_obj.is_sub_territory else "national"
        parent = t_obj.parent.label if t_obj.parent else None
        territory_ccy = TERRITORY_CURRENCY.get(label, "GBP")

        # Incentive info
        incentive_info = self._build_incentive_info(label, territory_incentives, territory_ccy)

        # Crew cost info
        crew_info = self._build_crew_info(label, crew_by_territory, territory_ccy)

        # Maintained profile (crew depth / infrastructure / bankability),
        # falling back to the parent territory's profile for sub-territories
        profile_info = self._build_profile_info(t_obj, profiles_by_territory or {})

        # Derive highlights and restrictions from incentive data
        highlights, restrictions, labor_req = self._derive_highlights_restrictions(
            label, territory_incentives,
        )

        return TerritoryCompareItem(
            label=label,
            iso=iso,
            level=level,
            parent=parent,
            incentive=incentive_info,
            crew_costs=crew_info,
            profile=profile_info,
            labor_requirement=labor_req,
            highlights=highlights,
            restrictions=restrictions,
            currency=territory_ccy,
        )

    @staticmethod
    def _build_profile_info(
        t_obj: Territory,
        profiles_by_territory: dict[str, dict],
    ) -> TerritoryProfileInfo | None:
        row = profiles_by_territory.get(t_obj.label)
        if row is None and t_obj.parent:
            row = profiles_by_territory.get(t_obj.parent.label)
        if row is None:
            return None
        return TerritoryProfileInfo(
            crew_depth_tier=row.get("crew_depth_tier"),
            crew_depth_score=_safe_int(row.get("crew_depth_score")),
            crew_depth_notes=row.get("crew_depth_notes"),
            infrastructure_tier=row.get("infrastructure_tier"),
            infrastructure_score=_safe_int(row.get("infrastructure_score")),
            infrastructure_notes=row.get("infrastructure_notes"),
            cert_weeks_min=_safe_int(row.get("cert_weeks_min")),
            cert_weeks_max=_safe_int(row.get("cert_weeks_max")),
            payment_weeks_min=_safe_int(row.get("payment_weeks_min")),
            payment_weeks_max=_safe_int(row.get("payment_weeks_max")),
            bankability_source_quality=row.get("bankability_source_quality"),
            bankability_source_note=row.get("bankability_source_note"),
            bankability_real_world_confirms=row.get("bankability_real_world_confirms"),
            bankability_suspended=row.get("bankability_suspended"),
            bankability_source_url=row.get("bankability_source_url"),
        )

    def _build_incentive_info(
        self,
        territory: str,
        territory_incentives: dict[str, list[dict]],
        territory_ccy: str,
    ) -> IncentiveInfo | None:
        rows = territory_incentives.get(territory, [])
        if not rows:
            return None

        best = best_incentive(rows, "Feature Film")
        programme = best.get("program_name") or best.get("program") or ""
        if not programme:
            return None

        rate_gross = to_float(best.get("rate_gross"))
        rate_net = to_float(best.get("rate_net"))
        rate_display = format_rate(rate_gross, rate_net) or "N/A"
        rate_type = best.get("rate_type")

        # Post-production / VFX bonus from supplementary rows
        post_prod = None
        all_rows = territory_incentives.get(territory, [])
        supp_rows = [r for r in all_rows if r.get("is_supplementary")]
        if supp_rows:
            supp = max(supp_rows, key=lambda r: to_float(r.get("rate_gross")) or 0)
            supp_rate = to_float(supp.get("rate_gross")) or to_float(supp.get("rate_net"))
            supp_name = supp.get("program_name") or supp.get("program") or "VFX"
            if supp_rate:
                post_prod = f"+{supp_rate}% {supp_name}"

        # Min spend
        qs_min = to_float(best.get("qualifying_spend_min"))
        qs_currency = best.get("qualifying_spend_currency") or territory_ccy
        min_spend_display = None
        if qs_min and qs_min > 0:
            sym = currency_symbol(qs_currency)
            if qs_min >= 1_000_000:
                min_spend_display = f"{sym}{qs_min / 1_000_000:g}M"
            elif qs_min >= 1_000:
                min_spend_display = f"{sym}{qs_min / 1_000:g}K"
            else:
                min_spend_display = f"{sym}{qs_min:g}"
        else:
            min_spend_display = "No minimum"

        # Cap
        cap_display = None
        cap_amount = best.get("cap_amount")
        cap_currency = best.get("cap_currency") or territory_ccy
        if cap_amount and to_float(cap_amount):
            cap_display = format_cap(cap_amount, cap_currency)
        if not cap_display:
            cap_text = (best.get("cap") or "").strip()
            cap_display = cap_text if cap_text else "No cap"

        # Payment timeline
        timeline = best.get("payment_timeline_notes")
        timeline_min = _safe_int(best.get("payment_timeline_days_min"))
        timeline_max = _safe_int(best.get("payment_timeline_days_max"))

        # Eligibility rules
        elig_raw = best.get("eligibility_rules_json")
        eligibility_rules = _parse_json_list(elig_raw)

        # Warnings
        warn_raw = best.get("warnings_json")
        warnings = _parse_json_list(warn_raw)

        last_verified = best.get("last_verified_at")
        if last_verified and hasattr(last_verified, "isoformat"):
            last_verified = last_verified.isoformat()
        elif last_verified:
            last_verified = str(last_verified)[:10]

        expiry = best.get("expiry_date")
        if expiry and hasattr(expiry, "isoformat"):
            expiry = expiry.isoformat()
        elif expiry:
            expiry = str(expiry)[:10]

        return IncentiveInfo(
            programme=programme,
            tax_rebate=rate_display,
            rate_gross=rate_gross,
            rate_net=rate_net,
            rate_type=rate_type,
            post_production_bonus=post_prod,
            min_spend=min_spend_display,
            min_spend_raw=qs_min,
            min_spend_currency=qs_currency,
            cap_display=cap_display,
            payment_timeline=timeline,
            payment_timeline_days_min=timeline_min,
            payment_timeline_days_max=timeline_max,
            eligibility_rules=eligibility_rules,
            warnings=warnings,
            last_verified=last_verified,
            expiry_date=expiry,
        )

    def _build_crew_info(
        self,
        territory: str,
        crew_by_territory: dict[str, list[dict]],
        territory_ccy: str,
    ) -> CrewCostInfo | None:
        rows = crew_by_territory.get(territory, [])
        if not rows:
            t_obj = resolve_territory(territory)
            if t_obj and t_obj.parent:
                rows = crew_by_territory.get(t_obj.parent.label, [])
        if not rows:
            return None

        sym = currency_symbol(territory_ccy)

        # Average day rate in GBP
        rates_gbp: list[float] = []
        sample_roles: dict[str, str] = {}
        for row in rows:
            role = row.get("role_category") or row.get("role") or ""
            if role.startswith("CAST-"):
                continue
            union_gbp = to_float(row.get("union_rate_gbp"))
            non_union_gbp = to_float(row.get("non_union_rate_gbp"))
            rate = union_gbp or non_union_gbp
            if rate and rate > 0:
                rates_gbp.append(rate)
                if role and role not in sample_roles and len(sample_roles) < 5:
                    sample_roles[role] = f"{sym}{rate:,.0f}/day"

        if not rates_gbp:
            return None

        avg = sum(rates_gbp) / len(rates_gbp)
        return CrewCostInfo(
            avg_day_rate=round(avg),
            avg_day_rate_display=f"{sym}{avg:,.0f}/day",
            currency=territory_ccy,
            sample_roles=sample_roles,
        )

    def _derive_highlights_restrictions(
        self,
        territory: str,
        territory_incentives: dict[str, list[dict]],
    ) -> tuple[list[str], list[str], str | None]:
        rows = territory_incentives.get(territory, [])
        if not rows:
            return [], [], None

        best = best_incentive(rows, "Feature Film")
        highlights: list[str] = []
        restrictions: list[str] = []

        # Rate-based highlights
        rate = to_float(best.get("rate_gross")) or to_float(best.get("rate_net")) or 0
        if rate >= 35:
            highlights.append(f"High incentive rate ({rate}%)")
        elif rate >= 25:
            highlights.append(f"Competitive incentive rate ({rate}%)")

        # Cap
        cap = (best.get("cap") or "").lower()
        if "no formal cap" in cap or "no cap" in cap or not cap:
            highlights.append("No cap on rebate amount")
        elif cap:
            restrictions.append(f"Cap: {best.get('cap', '').strip()}")

        # Payment speed
        timeline_max = to_float(best.get("payment_timeline_days_max"))
        if timeline_max and timeline_max <= 90:
            highlights.append("Fast payment processing")
        elif timeline_max and timeline_max > 180:
            restrictions.append("Extended payment timeline")

        # Co-production
        if best.get("co_production_eligible"):
            highlights.append("Co-production eligible")

        # Eligibility rules → restrictions
        elig_raw = best.get("eligibility_rules_json")
        elig_rules = _parse_json_list(elig_raw)
        for rule in elig_rules[:3]:
            if rule and rule not in restrictions:
                restrictions.append(rule)

        # Warnings → restrictions
        warn_raw = best.get("warnings_json")
        warns = _parse_json_list(warn_raw)
        for w in warns[:2]:
            if w and w not in restrictions:
                restrictions.append(w)

        # Labor requirement
        labor_req = None
        nat_req = best.get("nationality_requirements")
        if nat_req:
            if isinstance(nat_req, str):
                try:
                    nat_req = json.loads(nat_req)
                except (json.JSONDecodeError, TypeError):
                    pass
            if isinstance(nat_req, list) and nat_req:
                labor_req = "; ".join(str(r) for r in nat_req[:2])
            elif isinstance(nat_req, str) and nat_req.strip():
                labor_req = nat_req.strip()

        return highlights, restrictions, labor_req

    def _fetch_table(self, table_name: str) -> list[dict]:
        try:
            result = self.supabase.table(table_name).select("*").execute()
            return result.data or []
        except Exception:
            logger.warning("TerritoryService: failed to fetch %s", table_name, exc_info=True)
            # A failed SELECT aborts the Postgres transaction; roll back so
            # subsequent fetches on this session don't fail with
            # InFailedSqlTransaction.
            try:
                self.supabase.session.rollback()
            except Exception:
                pass
            return []

    def _fx_enrich_crew_costs(self, crew_costs: list[dict]) -> list[dict]:
        """Add union_rate_gbp, non_union_rate_gbp to each row."""
        if not crew_costs:
            return crew_costs

        currencies: set[str] = set()
        for c in crew_costs:
            ccy = (c.get("rate_currency") or c.get("currency") or "").upper()
            if ccy and ccy != "GBP":
                currencies.add(ccy)

        rates: dict[str, tuple] = {}
        if currencies:
            try:
                rates = self.fx.get_rates_batch("GBP", list(currencies))
            except Exception:
                logger.warning("FX enrichment failed for territory crew costs", exc_info=True)

        for c in crew_costs:
            currency = (c.get("rate_currency") or c.get("currency") or "").upper()
            union_cents = c.get("union_rate_cents")
            non_union_cents = c.get("non_union_rate_cents")
            if currency == "GBP" or not currency:
                c["union_rate_gbp"] = round(union_cents / 100) if union_cents else None
                c["non_union_rate_gbp"] = round(non_union_cents / 100) if non_union_cents else None
            elif currency in rates:
                rate, _ = rates[currency]
                c["union_rate_gbp"] = round(union_cents / 100 / rate) if union_cents else None
                c["non_union_rate_gbp"] = round(non_union_cents / 100 / rate) if non_union_cents else None
            else:
                c["union_rate_gbp"] = None
                c["non_union_rate_gbp"] = None

        return crew_costs


# ── Module-level helpers ──────────────────────────────────────────────────


def _build_territory_list() -> list[TerritoryListItem]:
    """Build the full available territory list from the enum."""
    items: list[TerritoryListItem] = []
    for t in Territory:
        if t.iso == "EU":
            continue
        items.append(TerritoryListItem(
            label=t.label,
            iso=t.iso,
            level="regional" if t.is_sub_territory else "national",
            parent=t.parent.label if t.parent else None,
        ))
    return items


def _parse_json_list(raw: Any) -> list[str]:
    """Safely parse a JSON string or list into a list of strings."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(r) for r in raw if r]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(r) for r in parsed if r]
        except (json.JSONDecodeError, TypeError):
            return [raw] if raw.strip() else []
    return []


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None
