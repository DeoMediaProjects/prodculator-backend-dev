"""What-If Calculator scenario computation.

Reuses the existing rebate engine (ReportValidator._compute_corrected_rebate),
FX service and curated territory profiles — zero calculation duplication.
Crew day-rates were removed from platform scope (2026-07, owner-approved);
cost efficiency reads the curated territory_profiles score (neutral 50
when no sourced data exists).
"""
from __future__ import annotations

import logging

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
)
from app.modules.reports.validator import ReportValidator

logger = logging.getLogger(__name__)



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
        # (crew day-rates removed from platform scope 2026-07, owner-approved)
        incentives = self._fetch_table("incentive_programs")
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

        # ── 7. Compute per-territory scenarios ─────────────────────────────
        weights = SCORE_WEIGHTS.get(request.production_priority, SCORE_WEIGHTS["full"])
        territory_scenarios: list[TerritoryScenario] = []

        for territory in primary_territories:
            scenario = self._compute_territory(
                territory=territory,
                territory_incentives=territory_incentives,
                budget_gbp=budget_gbp,
                request=request,
                fx_rates_from_budget=fx_rates_from_budget,
                weights=weights,
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
        budget_gbp: float,
        request: ScenarioRequest,
        fx_rates_from_budget: dict[str, dict],
        weights: dict[str, float],
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

        def _display(gbp_amount: float) -> tuple[float, str]:
            d_amount, sym, _ = budget_to_display(
                gbp_amount, request.budget_currency, request.budget_currency,
                request.budget_amount, budget_gbp, fx_rates_from_budget,
            )
            return d_amount, f"{sym}{d_amount:,.0f}"

        rebate_gbp = corrected["net_rebate"]
        d_rebate, rebate_display = _display(rebate_gbp)
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

        # ── Cost efficiency (curated) ──────────────────────────────────────
        # Day-rate derivation removed (owner-approved). NULL profile score =
        # no sourced data -> neutral 50 (no fabricated numbers).
        territory_profile = self._get_territory_profile(territory, territory_profiles or {})
        cost_efficiency_score = self._profile_score(territory_profile, "cost_efficiency_score")
        cost_efficiency_for_score = cost_efficiency_score if cost_efficiency_score is not None else 50
        crew_depth_score = self._profile_score(territory_profile, "crew_depth_score")
        infrastructure_score = self._profile_score(territory_profile, "infrastructure_score")
        crew_depth_for_score = crew_depth_score if crew_depth_score is not None else 50
        infrastructure_for_score = infrastructure_score if infrastructure_score is not None else 50

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

        # (crew-cost saving component removed with day rates, 2026-07)
        net_saving_gbp = rebate_gbp + max(ca_value_gbp, 0)
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
        reliability, bankability_label = ReportBuilder._compute_reliability(best, territory_profile)
        overall_score = (
            weights.get("costEfficiency", 0) * cost_efficiency_for_score
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
            cost_efficiency_score=cost_efficiency_score,
            crew_depth_score=crew_depth_score,
            crew_depth_tier=self._profile_tier(territory_profile, "crew_depth_tier"),
            infrastructure_score=infrastructure_score,
            infrastructure_tier=self._profile_tier(territory_profile, "infrastructure_tier"),
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
