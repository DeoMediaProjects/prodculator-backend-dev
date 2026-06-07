"""What-If Calculator scenario computation.

Reuses the existing rebate engine (ReportValidator._compute_corrected_rebate),
FX service, and crew-cost data — zero calculation duplication.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.core.territories import resolve_territory
from app.modules.calculator.schemas import (
    ScenarioRequest,
    ScenarioResponse,
    TerritoryScenario,
)
from app.modules.fx.service import FXService, TERRITORY_CURRENCY
from app.modules.reports.builder import ReportBuilder, SCORE_WEIGHTS
from app.modules.reports.helpers import (
    best_incentive,
    index_incentives_by_territory,
    format_rate,
    format_cap,
    to_float,
    currency_symbol,
    budget_to_display,
    is_zero_rate,
    is_domestic_corp_only,
    DEFAULT_ATL_PCT,
)
from app.modules.reports.validator import ReportValidator

logger = logging.getLogger(__name__)

# Crew cost-efficiency anchor constants
_BASELINE_CREW_COUNTRY: dict[str, str] = {
    "GB": "GB",
    "US": "US",
}
_ANCHOR_MID = 50


class CalculatorService:
    """Compute What-If scenario for all covered territories."""

    def __init__(
        self, supabase: DatabaseClient, settings: Settings,
    ) -> None:
        self.supabase = supabase
        self.settings = settings
        self.fx = FXService(settings)

    def compute_scenario(self, request: ScenarioRequest) -> ScenarioResponse:
        # ── 1. Fetch data from DB ──────────────────────────────────────────
        incentives = self._fetch_table("incentive_programs")
        crew_costs_raw = self._fetch_table("crew_costs")
        territory_profiles_raw = self._fetch_table("territory_profiles")
        territory_profiles = {
            row["territory"]: row
            for row in territory_profiles_raw
            if row.get("territory")
        }

        # ── 2. Convert budget to GBP ───────────────────────────────────────
        budget_gbp_info = self.fx.convert_budget(
            request.budget_amount, request.budget_currency, "GBP",
        )
        budget_gbp: float = budget_gbp_info["converted"]
        if budget_gbp <= 0:
            return ScenarioResponse(
                budget_amount=request.budget_amount,
                budget_currency=request.budget_currency,
                budget_gbp=0,
                vfx_pct=request.vfx_pct,
                production_format=request.production_format,
                production_priority=request.production_priority,
                territories=[],
            )

        # ── 3. Index incentives by territory ───────────────────────────────
        territory_incentives = index_incentives_by_territory(incentives)

        # Filter out supplementary-only territories
        primary_territories: list[str] = []
        for t, rows in territory_incentives.items():
            if not t:
                continue
            has_primary = any(not r.get("is_supplementary") for r in rows)
            if has_primary:
                primary_territories.append(t)

        # Apply user territory filter if provided
        if request.territories:
            requested = set(request.territories)
            primary_territories = [
                t for t in primary_territories if t in requested
            ]

        # ── 4. FX-enrich crew costs (cents → GBP) ─────────────────────────
        crew_costs = self._fx_enrich_crew_costs(crew_costs_raw)

        # Index crew costs by territory (multiple key variants)
        crew_by_territory: dict[str, list[dict]] = {}
        for row in crew_costs:
            for raw_key in (row.get("territory") or "", row.get("country") or ""):
                if not raw_key:
                    continue
                crew_by_territory.setdefault(raw_key, []).append(row)
                t_obj = resolve_territory(raw_key)
                if t_obj:
                    canonical = t_obj.label
                    if canonical != raw_key:
                        crew_by_territory.setdefault(canonical, []).append(row)
                    if t_obj.parent and t_obj.parent.label != raw_key:
                        crew_by_territory.setdefault(t_obj.parent.label, []).append(row)

        # ── 5. Compute FX rates for budget → territory currencies ──────────
        target_currencies = set()
        for t in primary_territories:
            ccy = TERRITORY_CURRENCY.get(t)
            if ccy:
                target_currencies.add(ccy)
        fx_rates_batch = self.fx.get_rates_batch(
            request.budget_currency, list(target_currencies),
        )
        # Also get GBP rates for display conversion
        fx_rates_from_budget: dict[str, dict] = {}
        for ccy, (rate, rate_date) in fx_rates_batch.items():
            fx_rates_from_budget[ccy] = {
                "rate": rate,
                "rate_date": rate_date.isoformat() if hasattr(rate_date, "isoformat") else str(rate_date),
            }

        fx_date_str: str | None = None
        for info in fx_rates_from_budget.values():
            fx_date_str = info.get("rate_date")
            break

        # ── 6. Resolve baseline for crew cost scoring ─────────────────────
        baseline = getattr(request, "baseline", "GB")
        baseline_country = _BASELINE_CREW_COUNTRY.get(baseline, baseline)
        baseline_rate_gbp = self._compute_baseline_rate(
            baseline_country, crew_by_territory,
        )
        # ── 7. Compute per-territory scenarios ─────────────────────────────
        weights = SCORE_WEIGHTS.get(request.production_priority, SCORE_WEIGHTS["full"])
        territory_scenarios: list[TerritoryScenario] = []

        for territory in primary_territories:
            scenario = self._compute_territory(
                territory=territory,
                territory_incentives=territory_incentives,
                crew_by_territory=crew_by_territory,
                budget_gbp=budget_gbp,
                request=request,
                fx_rates_from_budget=fx_rates_from_budget,
                weights=weights,
                baseline_rate_gbp=baseline_rate_gbp,
                territory_profiles=territory_profiles,
            )
            if scenario is not None:
                territory_scenarios.append(scenario)

        # Sort by overall_score descending
        territory_scenarios.sort(key=lambda s: s.overall_score, reverse=True)

        return ScenarioResponse(
            budget_amount=request.budget_amount,
            budget_currency=request.budget_currency,
            budget_gbp=budget_gbp,
            vfx_pct=request.vfx_pct,
            production_format=request.production_format,
            production_priority=request.production_priority,
            fx_rates_date=fx_date_str,
            territories=territory_scenarios,
        )

    # ── Per-territory computation ──────────────────────────────────────────

    def _compute_territory(
        self,
        territory: str,
        territory_incentives: dict[str, list[dict]],
        crew_by_territory: dict[str, list[dict]],
        budget_gbp: float,
        request: ScenarioRequest,
        fx_rates_from_budget: dict[str, dict],
        weights: dict[str, float],
        baseline_rate_gbp: float = 900,
        territory_profiles: dict[str, dict] | None = None,
    ) -> TerritoryScenario | None:
        rows = territory_incentives.get(territory, [])
        if not rows:
            return None

        best = best_incentive(rows, request.production_format)
        programme_name = best.get("program_name") or best.get("program") or ""
        if not programme_name:
            return None

        # Territory metadata
        t_obj = resolve_territory(territory)
        iso = t_obj.iso if t_obj else None
        territory_currency = best.get("currency") or TERRITORY_CURRENCY.get(territory, "GBP")
        rate_type = best.get("rate_type") or ""

        # ── Rebate calculation ─────────────────────────────────────────────
        # Resolve FX rate for rebate cap enforcement
        rebate_cap_currency = best.get("rebate_cap_currency")
        fx_rate_to_gbp: float | None = None
        if rebate_cap_currency and rebate_cap_currency != "GBP":
            fx_info = fx_rates_from_budget.get(rebate_cap_currency)
            if fx_info and fx_info.get("rate"):
                fx_rate_to_gbp = fx_info["rate"]

        corrected = ReportValidator._compute_corrected_rebate(
            best, budget_gbp, territory_incentives,
            production_format=request.production_format,
            fx_rate_to_gbp=fx_rate_to_gbp,
        )
        if corrected is None:
            return None

        # Display amounts in budget currency
        budget_symbol = currency_symbol(request.budget_currency)

        def _display(gbp_amount: float) -> tuple[float, str]:
            d_amount, sym, _ = budget_to_display(
                gbp_amount, request.budget_currency, request.budget_currency,
                request.budget_amount, budget_gbp, fx_rates_from_budget,
            )
            return d_amount, f"{sym}{d_amount:,.0f}"

        gross_rebate_gbp = corrected["gross_rebate"]
        d_rebate, rebate_display = _display(gross_rebate_gbp)
        d_qs, qs_display = _display(corrected["qualifying_spend"])
        qs_pct = corrected["qualifying_spend_pct"]

        atl_amount = corrected.get("atl_deduction_amount", 0)
        atl_display: str | None = None
        atl_budget_amount: float | None = None
        if atl_amount > 0:
            atl_budget_amount, atl_display = _display(atl_amount)

        rate_display = format_rate(corrected["rate_gross"], corrected["rate_net"]) or "N/A"
        switched_programme = corrected.get("switched_programme")
        final_programme = switched_programme or programme_name

        # ── Currency advantage ─────────────────────────────────────────────
        ca_score, ca_warning = self.fx.compute_currency_advantage_score(
            request.budget_currency, territory_currency,
        )

        # FX rate for this territory
        fx_info = fx_rates_from_budget.get(territory_currency)
        fx_rate = fx_info["rate"] if fx_info else None
        fx_rate_date = fx_info.get("rate_date") if fx_info else None

        # ── Crew cost index ────────────────────────────────────────────────
        crew_cost_index = self._crew_rate_anchor(territory, crew_by_territory, baseline_rate_gbp)
        territory_profile = self._get_territory_profile(territory, territory_profiles or {})
        crew_depth_score = self._profile_score(territory_profile, "crew_depth_score")
        infrastructure_score = self._profile_score(territory_profile, "infrastructure_score")
        crew_depth_for_score = crew_depth_score if crew_depth_score is not None else 50
        infrastructure_for_score = infrastructure_score if infrastructure_score is not None else 50

        # Build crew rate strings in budget currency
        crew_rates = self._build_crew_rates(
            territory, crew_by_territory,
            request.budget_currency, request.budget_amount,
            budget_gbp, fx_rates_from_budget,
        )

        # ── VFX supplementary credits ──────────────────────────────────────
        vfx_uplift_rate: float | None = None
        vfx_uplift_programme: str | None = None
        vfx_uplift_value: float | None = None
        vfx_uplift_display: str | None = None

        if request.vfx_pct > 0:
            vfx_spend_gbp = budget_gbp * (request.vfx_pct / 100.0)
            supplementary_rows = [
                r for r in rows
                if r.get("is_supplementary")
                and not is_zero_rate(r.get("rate_gross"), r.get("rate_net"))
            ]
            if supplementary_rows:
                supp = max(
                    supplementary_rows,
                    key=lambda r: to_float(r.get("rate_gross")) or to_float(r.get("rate_net")) or 0,
                )
                supp_rate = to_float(supp.get("rate_gross")) or to_float(supp.get("rate_net")) or 0
                if supp_rate > 0:
                    vfx_uplift_rate = supp_rate
                    vfx_uplift_programme = supp.get("program_name") or supp.get("program") or ""
                    vfx_rebate_gbp = vfx_spend_gbp * (supp_rate / 100.0)
                    vfx_uplift_value, vfx_uplift_display = _display(vfx_rebate_gbp)

        # ── Net saving ─────────────────────────────────────────────────────
        # Currency advantage value: approximate monetary benefit from FX
        # differential. (ca_score - 50) maps to a fraction of budget.
        ca_value_gbp = budget_gbp * (ca_score - 50) / 200.0

        # Crew cost saving: difference vs UK baseline anchor (50).
        # Higher crew_cost_index = cheaper = more saving.
        crew_saving_gbp = 0.0
        if crew_cost_index is not None:
            crew_saving_gbp = budget_gbp * (crew_cost_index - _ANCHOR_MID) / 200.0

        net_saving_gbp = gross_rebate_gbp + max(ca_value_gbp, 0) + max(crew_saving_gbp, 0)
        if vfx_uplift_value is not None:
            # Add VFX uplift as GBP before display conversion
            vfx_uplift_gbp = budget_gbp * (request.vfx_pct / 100.0) * ((vfx_uplift_rate or 0) / 100.0)
            net_saving_gbp += vfx_uplift_gbp

        d_net, net_display = _display(net_saving_gbp)

        # ── Cap display ────────────────────────────────────────────────────
        cap_display: str | None = None
        rebate_cap = best.get("rebate_cap_amount")
        rebate_cap_cur = best.get("rebate_cap_currency") or best.get("cap_currency") or "GBP"
        if rebate_cap is not None and to_float(rebate_cap):
            cap_display = format_cap(rebate_cap, rebate_cap_cur)
            if cap_display:
                cap_display = f"{cap_display} per project"
        if not cap_display:
            db_cap = (best.get("cap") or "").strip()
            if db_cap and "no formal cap" not in db_cap.lower():
                cap_display = db_cap
        if not cap_display:
            cap_amount = best.get("cap_amount")
            cap_currency = best.get("cap_currency") or "GBP"
            cap_display = format_cap(cap_amount, cap_currency)

        # ── Min spend ──────────────────────────────────────────────────────
        qs_min = to_float(best.get("qualifying_spend_min"))
        qs_currency = best.get("qualifying_spend_currency") or "GBP"
        min_spend: str | None = None
        if qs_min and qs_min > 0:
            sym = currency_symbol(qs_currency)
            if qs_min >= 1_000_000:
                min_spend = f"{sym}{qs_min / 1_000_000:g}M minimum"
            elif qs_min >= 1_000:
                min_spend = f"{sym}{qs_min / 1_000:g}K minimum"
            else:
                min_spend = f"{sym}{qs_min:g} minimum"
        else:
            min_spend = "No minimum"

        # ── Payment timeline ───────────────────────────────────────────────
        payment_timeline = best.get("payment_timeline_notes")

        # ── Overall score ──────────────────────────────────────────────────
        incentive_strength = ReportBuilder._compute_incentive_strength(best)
        reliability, bankability_label = ReportBuilder._compute_reliability(best)
        overall_score = (
            weights.get("costEfficiency", 0) * (crew_cost_index or 50)
            + weights.get("crewDepth", 0) * crew_depth_for_score
            + weights.get("infrastructure", 0) * infrastructure_for_score
            + weights.get("incentiveStrength", 0) * incentive_strength
            + weights.get("currencyAdvantage", 0) * ca_score
            + weights.get("incentiveReliability", 0) * reliability
        )

        # PR 8 — Bankability and Financial Return Score
        frs = max(0, min(100, int(round((incentive_strength * 0.50) + (reliability * 0.50)))))
        if bankability_label == "NOT BANKABLE" or frs < 45:
            frs_verdict: str = "Caution"
        elif frs >= 70 and bankability_label == "BANKABLE":
            frs_verdict = "Bankable"
        else:
            frs_verdict = "Verify First"

        return TerritoryScenario(
            territory=territory,
            iso=iso,
            programme=final_programme,
            rate_display=rate_display,
            rate_gross=corrected["rate_gross"],
            rate_net=corrected["rate_net"] if corrected.get("rate_net") else None,
            rate_type=rate_type or None,
            estimated_rebate=d_rebate,
            estimated_rebate_display=rebate_display,
            qualifying_spend=d_qs,
            qualifying_spend_display=qs_display,
            qualifying_spend_pct=qs_pct,
            atl_deduction=atl_budget_amount,
            atl_deduction_display=atl_display,
            currency_advantage_score=ca_score,
            currency_advantage_warning=ca_warning,
            territory_currency=territory_currency,
            fx_rate=fx_rate,
            fx_rate_date=fx_rate_date,
            crew_cost_index=crew_cost_index,
            crew_depth_score=crew_depth_score,
            crew_depth_tier=self._profile_tier(territory_profile, "crew_depth_tier"),
            infrastructure_score=infrastructure_score,
            infrastructure_tier=self._profile_tier(territory_profile, "infrastructure_tier"),
            crew_rates=crew_rates,
            net_saving=d_net,
            net_saving_display=net_display,
            payment_timeline=payment_timeline,
            min_spend=min_spend,
            cap=cap_display,
            eligibility_note=corrected.get("qualifying_spend_note"),
            programme_note=corrected.get("programme_note"),
            overall_score=round(overall_score, 1),
            vfx_uplift_rate=vfx_uplift_rate,
            vfx_uplift_programme=vfx_uplift_programme,
            vfx_uplift_value=vfx_uplift_value,
            vfx_uplift_display=vfx_uplift_display,
            bankability_label=bankability_label,
            financial_return_score=frs,
            financial_return_verdict=frs_verdict,
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    def _fetch_table(self, table_name: str) -> list[dict]:
        try:
            result = self.supabase.table(table_name).select("*").execute()
            return result.data or []
        except Exception:
            logger.warning("Calculator: failed to fetch %s", table_name, exc_info=True)
            return []

    def _fx_enrich_crew_costs(self, crew_costs: list[dict]) -> list[dict]:
        """Add union_rate_gbp, non_union_rate_gbp to each row (same logic as ReportService)."""
        if not crew_costs:
            return crew_costs

        currencies = set()
        for c in crew_costs:
            ccy = (c.get("rate_currency") or c.get("currency") or "").upper()
            if ccy and ccy != "GBP":
                currencies.add(ccy)

        if not currencies:
            for c in crew_costs:
                union_cents = c.get("union_rate_cents")
                non_union_cents = c.get("non_union_rate_cents")
                c["union_rate_gbp"] = round(union_cents / 100) if union_cents else None
                c["non_union_rate_gbp"] = round(non_union_cents / 100) if non_union_cents else None
            return crew_costs

        try:
            rates = self.fx.get_rates_batch("GBP", list(currencies))
        except Exception:
            logger.warning("FX enrichment failed for calculator crew costs", exc_info=True)
            rates = {}

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

    def _crew_rate_anchor(
        self, territory: str, crew_by_territory: dict[str, list[dict]],
        baseline_rate_gbp: float = 900,
    ) -> int | None:
        """Compute costEfficiency anchor (0-100). Same formula as ReportBuilder."""
        rows = crew_by_territory.get(territory, [])
        if not rows:
            t_obj = resolve_territory(territory)
            if t_obj and t_obj.parent:
                rows = crew_by_territory.get(t_obj.parent.label, [])
        if not rows:
            return None

        rates_gbp: list[float] = []
        for row in rows:
            union = to_float(row.get("union_rate_gbp"))
            non_union = to_float(row.get("non_union_rate_gbp"))
            rate = union or non_union
            if rate and rate > 0:
                rates_gbp.append(rate)

        if not rates_gbp:
            return None

        avg_rate = sum(rates_gbp) / len(rates_gbp)
        anchor = int(baseline_rate_gbp * _ANCHOR_MID / avg_rate)
        return max(20, min(85, anchor))

    def _get_territory_profile(
        self,
        territory: str,
        territory_profiles: dict[str, dict],
    ) -> dict | None:
        if not territory_profiles:
            return None

        candidates: list[str] = [territory]
        t_obj = resolve_territory(territory)
        if t_obj:
            candidates.extend([t_obj.label, t_obj.iso])
            if t_obj.parent:
                candidates.extend([t_obj.parent.label, t_obj.parent.iso])

        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            profile = territory_profiles.get(candidate)
            if isinstance(profile, dict):
                return profile

        territory_lower = territory.lower()
        for profile in territory_profiles.values():
            if not isinstance(profile, dict):
                continue
            row_territory = str(profile.get("territory") or "").lower()
            row_iso = str(profile.get("iso_code") or "").lower()
            if territory_lower in {row_territory, row_iso}:
                return profile
        return None

    @staticmethod
    def _profile_score(profile: dict | None, key: str) -> int | None:
        if not profile:
            return None
        raw = to_float(profile.get(key))
        if raw is None:
            return None
        return max(0, min(100, int(round(raw))))

    @staticmethod
    def _profile_tier(profile: dict | None, key: str) -> str | None:
        if not profile:
            return None
        raw = str(profile.get(key) or "").strip()
        return raw or None

    def _compute_baseline_rate(
        self, country_code: str, crew_by_territory: dict[str, list[dict]],
    ) -> float:
        """Compute the average crew day rate (GBP) for a baseline country.

        Dynamically derived from actual crew_costs data so the baseline
        stays current as rates are updated.  Falls back to 900 only if
        no crew data exists at all (should not happen in production).
        """
        _FALLBACK = 900  # last-resort if DB has no crew data

        rows = crew_by_territory.get(country_code, [])
        if not rows:
            t_obj = resolve_territory(country_code)
            if t_obj:
                rows = crew_by_territory.get(t_obj.label, [])
        if not rows:
            return _FALLBACK

        rates_gbp: list[float] = []
        for row in rows:
            union = to_float(row.get("union_rate_gbp"))
            non_union = to_float(row.get("non_union_rate_gbp"))
            rate = union or non_union
            if rate and rate > 0:
                rates_gbp.append(rate)

        if not rates_gbp:
            return _FALLBACK

        return sum(rates_gbp) / len(rates_gbp)

    def _build_crew_rates(
        self,
        territory: str,
        crew_by_territory: dict[str, list[dict]],
        budget_currency: str,
        budget_original_amount: float,
        budget_gbp: float,
        fx_rates_from_budget: dict[str, dict],
    ) -> dict[str, str]:
        """Build role → "£350-£500/day" rate strings."""
        rows = crew_by_territory.get(territory, [])
        if not rows:
            t_obj = resolve_territory(territory)
            if t_obj and t_obj.parent:
                rows = crew_by_territory.get(t_obj.parent.label, [])
        if not rows:
            return {}

        crew_rates: dict[str, str] = {}
        for crew_row in rows:
            role = crew_row.get("role_category") or crew_row.get("role") or ""
            if not role or role.startswith("CAST-"):
                continue
            union_gbp = crew_row.get("union_rate_gbp")
            non_union_gbp = crew_row.get("non_union_rate_gbp")
            if union_gbp is None and non_union_gbp is None:
                continue

            def _crew_disp(gbp_val: float) -> str:
                d, s, _ = budget_to_display(
                    gbp_val, budget_currency, budget_currency,
                    budget_original_amount, budget_gbp, fx_rates_from_budget,
                )
                return f"{s}{d:,.0f}"

            if union_gbp and non_union_gbp:
                lo, hi = sorted([union_gbp, non_union_gbp])
                rate_text = f"{_crew_disp(lo)}-{_crew_disp(hi)}/day"
            elif union_gbp:
                rate_text = f"{_crew_disp(union_gbp)}/day"
            else:
                rate_text = f"{_crew_disp(non_union_gbp)}/day"

            if role not in crew_rates:
                crew_rates[role] = rate_text

        return crew_rates

    @staticmethod
    def _compute_incentive_strength(rate_gross: float, rate_net: float | None) -> int:
        """Map incentive rate to a 0-100 strength score."""
        rate = rate_gross or rate_net or 0
        # 0% → 0, 25% → 50, 40% → 80, 50%+ → 95
        if rate <= 0:
            return 0
        if rate >= 50:
            return 95
        return int(rate * 2)
