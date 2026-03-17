"""Territory data integrity validator for production analysis reports.

Overrides hallucinated fields in AI-generated report data with ground-truth
values from the datasets that were injected into the prompt.  Runs post-
sanitisation so the structure is already clean.

Usage::

    from app.modules.reports.validator import ReportValidator

    sanitized, warnings = ReportValidator.validate(sanitized, datasets)
"""
from __future__ import annotations

import calendar
import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# Deviation tolerance before flagging rebate arithmetic (15 %)
_REBATE_TOLERANCE = 0.15

# Data freshness threshold — flag incentives older than this many days
_STALE_DAYS = 365


class ReportValidator:
    """Post-process a sanitised report dict against the source datasets."""

    @classmethod
    def validate(cls, report: dict, datasets: dict) -> tuple[dict, list[str]]:
        """Validate and patch *report* in-place using *datasets*.

        Returns the patched report and a list of human-readable warning strings
        (useful for logging / debug endpoints).
        """
        warnings: list[str] = []
        incentives_by_program = _index_incentives(datasets.get("incentives", []))

        # Extract budget_gbp early — needed by multiple patch methods
        budget_gbp_override = None
        budget_gbp_data = datasets.get("_budget_gbp")
        if isinstance(budget_gbp_data, dict):
            budget_gbp_override = budget_gbp_data.get("converted")

        cls._patch_incentive_estimates(
            report, incentives_by_program, warnings,
            budget_gbp=budget_gbp_override,
        )
        cls._patch_location_rankings(
            report, incentives_by_program, warnings,
            budget_gbp=budget_gbp_override,
        )
        # NOTE: _patch_territory_deep_dives is called later (after score
        # recalculation) so deep dives get authoritative scores + rebates.
        cls._patch_executive_summary(report, incentives_by_program, warnings)
        cls._patch_crew_insights(report, datasets.get("crew_costs", []), warnings)
        cls._patch_crew_cost_territories(report, warnings)
        cls._patch_cast_insights(report, datasets.get("cast_costs", []), warnings)
        cls._patch_attributions(report, datasets, warnings)

        # New gap-fix patches
        cls._patch_stacking_logic(report, incentives_by_program, warnings)
        cls._patch_weather_risk(
            report,
            datasets.get("weather", []),
            datasets.get("_shoot_months"),
            datasets.get("_ext_int_ratio"),
            warnings,
        )
        cls._patch_eligibility(
            report,
            incentives_by_program,
            datasets.get("_producer_country"),
            datasets.get("_co_production_status"),
            warnings,
        )

        # Financial accuracy patches — generic, data-driven enforcement
        # of qualifying spend rules, rate tier thresholds, and rebate arithmetic
        cls._patch_financial_calculations(
            report, incentives_by_program, warnings,
            budget_gbp_override=budget_gbp_override,
        )
        cls._patch_reliability_warnings(report, incentives_by_program, warnings)
        cls._patch_operational_requirements(report, incentives_by_program, warnings)
        cls._patch_comparable_relevance(report, datasets.get("comparables", []), warnings)
        cls._patch_grant_labelling(report, warnings)

        # v3 patches
        cls._patch_incentive_reliability(report, incentives_by_program, warnings)
        cls._patch_currency_advantage(report, datasets.get("_currency_advantage_scores"), warnings)
        cls._recalculate_overall_score(
            report, datasets.get("_production_priority", "full"), warnings
        )

        # Propagate recalculated scores + corrected rebates to territoryDeepDives
        # (must run AFTER _recalculate_overall_score so scores are authoritative)
        ranking_scores: dict[str, int] = {}
        rankings = report.get("locationRankings")
        if isinstance(rankings, list):
            for loc in rankings:
                if isinstance(loc, dict) and loc.get("name") and isinstance(loc.get("score"), int):
                    ranking_scores[loc["name"]] = loc["score"]
        cls._patch_territory_deep_dives(
            report, incentives_by_program, warnings,
            budget_gbp=budget_gbp_override,
            ranking_scores=ranking_scores or None,
        )

        cls._patch_narrative_spend_percentages(
            report, incentives_by_program, warnings,
            budget_gbp_override=budget_gbp_override,
        )
        cls._patch_production_format(
            report, datasets.get("_production_format"), warnings
        )
        cls._inject_section_explainers(report, datasets)

        if warnings:
            logger.info(
                "ReportValidator patched %d fields: %s",
                len(warnings),
                "; ".join(warnings[:10]),
            )
        return report, warnings

    # ── incentiveEstimates ────────────────────────────────────────────────────

    @classmethod
    def _patch_incentive_estimates(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
        *,
        budget_gbp: float | None = None,
    ) -> None:
        estimates = report.get("incentiveEstimates")
        if not isinstance(estimates, list):
            return

        territory_incentives = _index_incentives_by_territory(
            list(incentives_by_program.values())
        )

        for est in estimates:
            if not isinstance(est, dict):
                continue
            program_name = est.get("program", "")
            db_row = incentives_by_program.get(program_name)
            if db_row is None:
                continue

            territory = est.get("territory") or db_row.get("territory", "")

            # --- Budget-cap disqualification ---
            cap_amount = _to_float(db_row.get("cap_amount"))
            if (
                budget_gbp is not None
                and cap_amount is not None
                and cap_amount > 0
                and budget_gbp > cap_amount
            ):
                alt_rows = territory_incentives.get(territory, [])
                alternatives = [
                    r for r in alt_rows
                    if _prog_name(r) != _prog_name(db_row)
                    and (
                        _to_float(r.get("cap_amount")) is None
                        or (_to_float(r.get("cap_amount")) or 0) >= budget_gbp
                    )
                    and not _is_zero_rate(r.get("rate_gross"), r.get("rate_net"))
                ]
                if alternatives:
                    cap_currency = db_row.get("cap_currency") or "GBP"
                    cap_str = _format_cap(cap_amount, cap_currency) or f"{cap_currency} {cap_amount:,.0f}"
                    alt_name = _prog_name(_best_incentive(alternatives)) or "alternative programme"
                    disqualified_note = (
                        f"£0 — DISQUALIFIED. Total production budget exceeds "
                        f"the {program_name} {cap_str} budget cap. "
                        f"{alt_name} applies instead."
                    )
                    if est.get("estimatedRebate") != disqualified_note:
                        warnings.append(
                            f"[incentiveEstimates] {territory}/{program_name}: "
                            f"disqualified — budget exceeds cap of {cap_str}"
                        )
                        est["estimatedRebate"] = disqualified_note
                    # Still patch other fields (rate, cap, etc.) for reference
                    # but skip further rebate corrections below

            # --- Rate ---
            rate_gross = db_row.get("rate_gross")
            rate_net = db_row.get("rate_net")
            canonical_rate = _format_rate(rate_gross, rate_net)
            if canonical_rate and est.get("rate") != canonical_rate:
                warnings.append(
                    f"[incentiveEstimates] {territory}/{program_name}: "
                    f"rate overridden {est.get('rate')!r} → {canonical_rate!r}"
                )
                est["rate"] = canonical_rate

            # --- Zero-rate guard: any programme with 0% rate → no rebate ---
            if _is_zero_rate(rate_gross, rate_net):
                if est.get("estimatedRebate") not in (None, "", "0", "£0"):
                    warnings.append(
                        f"[incentiveEstimates] {territory}/{program_name}: "
                        "rate is 0 — clearing estimatedRebate"
                    )
                    est["estimatedRebate"] = "£0 (programme not currently active)"

            # --- Cap ---
            cap_amount = db_row.get("cap_amount")
            cap_currency = db_row.get("cap_currency") or "GBP"
            canonical_cap = _format_cap(cap_amount, cap_currency)
            if canonical_cap is not None and est.get("cap") != canonical_cap:
                warnings.append(
                    f"[incentiveEstimates] {territory}/{program_name}: "
                    f"cap overridden {est.get('cap')!r} → {canonical_cap!r}"
                )
                est["cap"] = canonical_cap

            # --- Payment timeline ---
            timeline_notes = db_row.get("payment_timeline_notes")
            if timeline_notes:
                if est.get("paymentSpeed") and est["paymentSpeed"] != timeline_notes:
                    warnings.append(
                        f"[incentiveEstimates] {territory}/{program_name}: "
                        f"paymentSpeed overridden → from dataset"
                    )
                est["paymentSpeed"] = timeline_notes
            elif not est.get("paymentSpeed"):
                est["paymentSpeed"] = "Data not available"

            # --- Qualifying spend ---
            qs_min = db_row.get("qualifying_spend_min")
            qs_currency = db_row.get("qualifying_spend_currency") or "GBP"
            if qs_min is not None:
                est["qualifyingSpend"] = _format_money(qs_min, qs_currency)

            # --- Eligibility rules ---
            rules_json = db_row.get("eligibility_rules_json")
            if isinstance(rules_json, list) and rules_json:
                est["requirements"] = rules_json

            # --- Source attribution ---
            source_name = db_row.get("source_name")
            if source_name:
                est["dataSource"] = source_name
            elif not est.get("dataSource"):
                est["dataSource"] = "Prodculator admin database"

            # --- Staleness badge ---
            freshness = db_row.get("data_freshness_days")
            if isinstance(freshness, int) and freshness > _STALE_DAYS:
                risk_msg = "Incentive data may be outdated — verify before committing"
                # Also add to corresponding locationRankings keyRisks (handled separately)
                est.setdefault("stalenessWarning", risk_msg)
                warnings.append(
                    f"[incentiveEstimates] {territory}/{program_name}: stale ({freshness} days)"
                )

            # --- last_updated ---
            lv = db_row.get("last_verified_at") or db_row.get("last_updated")
            if lv:
                est["lastUpdated"] = str(lv)

    # ── locationRankings ──────────────────────────────────────────────────────

    @classmethod
    def _patch_location_rankings(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
        *,
        budget_gbp: float | None = None,
    ) -> None:
        rankings = report.get("locationRankings")
        if not isinstance(rankings, list):
            return

        territory_incentives = _index_incentives_by_territory(
            list(incentives_by_program.values())
        )

        for loc in rankings:
            if not isinstance(loc, dict):
                continue
            territory = loc.get("name", "")
            rows = territory_incentives.get(territory, [])
            if not rows:
                continue

            # Use the best (highest rate_gross) incentive for the territory
            best = _best_incentive(rows)

            # Compute corrected rebate (handles budget-cap programme switching)
            corrected = None
            if budget_gbp is not None:
                corrected = cls._compute_corrected_rebate(
                    best, budget_gbp, territory_incentives
                )

            # --- rebatePercent (use corrected rates when a programme switch occurred) ---
            if corrected is not None and corrected.get("switched_programme"):
                effective_rate = _format_rate(corrected["rate_gross"], corrected["rate_net"])
            else:
                effective_rate = _format_rate(best.get("rate_gross"), best.get("rate_net"))
            if effective_rate and loc.get("rebatePercent") != effective_rate:
                warnings.append(
                    f"[locationRankings] {territory}: "
                    f"rebatePercent overridden {loc.get('rebatePercent')!r} → {effective_rate!r}"
                )
                loc["rebatePercent"] = effective_rate

            # --- Zero-rate guard ---
            if _is_zero_rate(
                best.get("rate_gross"), best.get("rate_net")
            ):
                loc["incentiveStrength"] = 0
                loc["rebateAmount"] = "£0"
            elif corrected is not None:
                # --- rebateAmount correction (uses same logic as financialAnalysis) ---
                currency = best.get("currency") or "GBP"
                symbol = _currency_symbol(currency)
                formatted = f"~{symbol}{corrected['net_rebate']:,.0f}"
                if corrected.get("programme_note"):
                    programme_label = corrected["programme_note"].split("—")[-1].strip() if "—" in corrected["programme_note"] else ""
                    if programme_label:
                        formatted += f" ({programme_label})"
                ai_rebate = _parse_money_string(loc.get("rebateAmount"))
                if ai_rebate is not None and corrected["net_rebate"] > 0:
                    deviation = abs(ai_rebate - corrected["net_rebate"]) / corrected["net_rebate"]
                    if deviation > _REBATE_TOLERANCE:
                        warnings.append(
                            f"[locationRankings] {territory}: rebateAmount corrected "
                            f"from {symbol}{ai_rebate:,.0f} to {symbol}{corrected['net_rebate']:,.0f}"
                        )
                        loc["rebateAmount"] = formatted
                elif ai_rebate is None:
                    loc["rebateAmount"] = formatted

            # --- paymentSpeed ---
            timeline_notes = best.get("payment_timeline_notes")
            if timeline_notes:
                if loc.get("paymentSpeed") and loc["paymentSpeed"] != timeline_notes:
                    warnings.append(
                        f"[locationRankings] {territory}: paymentSpeed overridden → from dataset"
                    )
                loc["paymentSpeed"] = timeline_notes
            elif not loc.get("paymentSpeed"):
                loc["paymentSpeed"] = "Data not available"

            # --- Staleness badge in keyRisks ---
            freshness = best.get("data_freshness_days")
            if isinstance(freshness, int) and freshness > _STALE_DAYS:
                stale_risk = "Incentive data may be outdated — verify before committing"
                key_risks = loc.setdefault("keyRisks", [])
                if isinstance(key_risks, list) and stale_risk not in key_risks:
                    key_risks.append(stale_risk)

    # ── territoryDeepDives ────────────────────────────────────────────────────

    @classmethod
    def _patch_territory_deep_dives(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
        *,
        budget_gbp: float | None = None,
        ranking_scores: dict[str, int] | None = None,
    ) -> None:
        dives = report.get("territoryDeepDives")
        if not isinstance(dives, list):
            return

        territory_incentives = _index_incentives_by_territory(
            list(incentives_by_program.values())
        )

        for dive in dives:
            if not isinstance(dive, dict):
                continue
            territory = dive.get("name", "")

            # --- Score propagation from locationRankings ---
            if ranking_scores and territory in ranking_scores:
                old_score = dive.get("score")
                new_score = ranking_scores[territory]
                if isinstance(old_score, (int, float)) and old_score != new_score:
                    warnings.append(
                        f"[territoryDeepDives] {territory}: score aligned "
                        f"from {old_score} to {new_score} (from locationRankings)"
                    )
                dive["score"] = new_score

            rows = territory_incentives.get(territory, [])
            if not rows:
                continue

            best = _best_incentive(rows)

            # Compute corrected rebate (handles budget-cap programme switching)
            corrected = None
            if budget_gbp is not None:
                corrected = cls._compute_corrected_rebate(
                    best, budget_gbp, territory_incentives
                )

            # paymentSpeed
            timeline_notes = best.get("payment_timeline_notes")
            if timeline_notes:
                if dive.get("paymentSpeed") and dive["paymentSpeed"] != timeline_notes:
                    warnings.append(
                        f"[territoryDeepDives] {territory}: paymentSpeed overridden → from dataset"
                    )
                dive["paymentSpeed"] = timeline_notes
            elif not dive.get("paymentSpeed"):
                dive["paymentSpeed"] = "Data not available"

            # rebate string (use corrected rates when a programme switch occurred)
            if corrected is not None and corrected.get("switched_programme"):
                effective_rate = _format_rate(corrected["rate_gross"], corrected["rate_net"])
            else:
                effective_rate = _format_rate(best.get("rate_gross"), best.get("rate_net"))
            if effective_rate:
                existing_rebate = dive.get("rebate", "")
                if existing_rebate and not existing_rebate.startswith(effective_rate.rstrip("%")):
                    warnings.append(
                        f"[territoryDeepDives] {territory}: rebate rate component overridden"
                    )
                    # Patch only the rate prefix portion, keep any estimated amount
                    parts = existing_rebate.split("/", 1)
                    dive["rebate"] = (
                        f"{effective_rate} / {parts[1].strip()}" if len(parts) == 2 else effective_rate
                    )

            # --- estimatedRebate correction ---
            if corrected is not None:
                currency = best.get("currency") or "GBP"
                symbol = _currency_symbol(currency)
                formatted = (
                    f"{symbol}{corrected['net_rebate']:,.0f} net"
                    f" ({symbol}{corrected['gross_rebate']:,.0f} gross)"
                )
                ai_rebate = _parse_money_string(dive.get("estimatedRebate"))
                if ai_rebate is not None and corrected["net_rebate"] > 0:
                    deviation = abs(ai_rebate - corrected["net_rebate"]) / corrected["net_rebate"]
                    if deviation > _REBATE_TOLERANCE:
                        warnings.append(
                            f"[territoryDeepDives] {territory}: estimatedRebate corrected "
                            f"from {symbol}{ai_rebate:,.0f} to {symbol}{corrected['net_rebate']:,.0f}"
                        )
                        dive["estimatedRebate"] = formatted
                elif ai_rebate is None:
                    dive["estimatedRebate"] = formatted

    # ── executiveSummary ──────────────────────────────────────────────────────

    @classmethod
    def _patch_executive_summary(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
    ) -> None:
        summary = report.get("executiveSummary")
        if not isinstance(summary, dict):
            return

        territory = summary.get("recommendedTerritory", "")
        if not territory:
            return

        territory_incentives = _index_incentives_by_territory(
            list(incentives_by_program.values())
        )
        rows = territory_incentives.get(territory, [])
        if not rows:
            return

        best = _best_incentive(rows)
        timeline_notes = best.get("payment_timeline_notes")
        if timeline_notes:
            if (
                summary.get("recommendedTerritoryPaymentSpeed")
                and summary["recommendedTerritoryPaymentSpeed"] != timeline_notes
            ):
                warnings.append(
                    f"[executiveSummary] {territory}: "
                    "recommendedTerritoryPaymentSpeed overridden → from dataset"
                )
            summary["recommendedTerritoryPaymentSpeed"] = timeline_notes
        elif not summary.get("recommendedTerritoryPaymentSpeed"):
            summary["recommendedTerritoryPaymentSpeed"] = "Data not available"

    # ── crewCostComparison territory filter ────────────────────────────────

    @classmethod
    def _patch_crew_cost_territories(
        cls,
        report: dict,
        warnings: list[str],
    ) -> None:
        """Remove territories from crewCostComparison that don't appear in
        locationRankings.  Ensures the crew table only shows territories
        the user selected for analysis.
        """
        rankings = report.get("locationRankings")
        if not isinstance(rankings, list):
            return
        ranked_territories = {
            loc.get("name")
            for loc in rankings
            if isinstance(loc, dict) and loc.get("name")
        }
        if not ranked_territories:
            return

        fin = report.get("financialAnalysis")
        if not isinstance(fin, dict):
            return
        crew_comp = fin.get("crewCostComparison")
        if not isinstance(crew_comp, list):
            return

        for row in crew_comp:
            if not isinstance(row, dict):
                continue
            territories = row.get("territories")
            if not isinstance(territories, dict):
                continue
            extra = set(territories.keys()) - ranked_territories
            for t in extra:
                del territories[t]
                warnings.append(
                    f"[crewCostComparison] removed {t!r} — not in locationRankings"
                )

    # ── crewInsights ──────────────────────────────────────────────────────────

    @classmethod
    def _patch_crew_insights(
        cls,
        report: dict,
        crew_costs: list[dict],
        warnings: list[str],
    ) -> None:
        """Ensure crewInsights.costVsUSD uses FX-converted GBP values where available."""
        insights = report.get("crewInsights")
        if not isinstance(insights, list):
            return

        # Index by both ISO country code and full territory name
        crew_by_territory: dict[str, list[dict]] = {}
        for row in crew_costs:
            country = row.get("country") or ""
            territory = row.get("territory") or ""
            if country:
                crew_by_territory.setdefault(country, []).append(row)
            if territory:
                crew_by_territory.setdefault(territory, []).append(row)

        # Reverse lookup: ISO → full name (for matching)
        from app.modules.reports.service import _TERRITORY_TO_ISO, _ISO_TO_TERRITORY

        for insight in insights:
            if not isinstance(insight, dict):
                continue
            territory = insight.get("territory", "")
            # Try direct match, then ISO conversion
            rows = crew_by_territory.get(territory, [])
            if not rows:
                iso = _TERRITORY_TO_ISO.get(territory, "")
                rows = crew_by_territory.get(iso, [])
            if not rows:
                full = _ISO_TO_TERRITORY.get(territory, "")
                rows = crew_by_territory.get(full, [])
            if not rows:
                continue

            # Find a row with union_rate_gbp or non_union_rate_gbp
            for row in rows:
                union_gbp = row.get("union_rate_gbp")
                non_union_gbp = row.get("non_union_rate_gbp")
                if union_gbp or non_union_gbp:
                    fx_rate = row.get("fx_rate")
                    fx_date = row.get("fx_date")
                    insight["fxRate"] = fx_rate
                    insight["fxDate"] = fx_date
                    insight["currency"] = "GBP"
                    break

    # ── castInsights ─────────────────────────────────────────────────────────

    @classmethod
    def _patch_cast_insights(
        cls,
        report: dict,
        cast_costs: list[dict],
        warnings: list[str],
    ) -> None:
        """Enrich castInsights with FX data from cast_costs dataset."""
        insights = report.get("castInsights")
        if not isinstance(insights, list):
            return

        from app.modules.reports.service import _TERRITORY_TO_ISO, _ISO_TO_TERRITORY

        cast_by_territory: dict[str, list[dict]] = {}
        for row in cast_costs:
            country = row.get("country") or ""
            territory = row.get("territory") or ""
            if country:
                cast_by_territory.setdefault(country, []).append(row)
            if territory:
                cast_by_territory.setdefault(territory, []).append(row)

        for insight in insights:
            if not isinstance(insight, dict):
                continue
            territory = insight.get("territory", "")
            rows = cast_by_territory.get(territory, [])
            if not rows:
                iso = _TERRITORY_TO_ISO.get(territory, "")
                rows = cast_by_territory.get(iso, [])
            if not rows:
                full = _ISO_TO_TERRITORY.get(territory, "")
                rows = cast_by_territory.get(full, [])
            if not rows:
                continue

            for row in rows:
                union_gbp = row.get("union_rate_gbp")
                non_union_gbp = row.get("non_union_rate_gbp")
                if union_gbp or non_union_gbp:
                    insight["fxRate"] = row.get("fx_rate")
                    insight["fxDate"] = row.get("fx_date")
                    break

    # ── attributions ─────────────────────────────────────────────────────────

    @classmethod
    def _patch_attributions(
        cls,
        report: dict,
        datasets: dict,
        warnings: list[str],
    ) -> None:
        """Inject territory-specific data attributions and mandatory disclaimer."""
        from app.modules.reports.attributions import (
            MANDATORY_DISCLAIMER,
            TERRITORY_ATTRIBUTIONS,
        )
        from app.modules.reports.service import _TERRITORY_TO_ISO

        # Collect all territories referenced in crew/cast insights
        referenced_territories: set[str] = set()
        for key in ("crewInsights", "castInsights"):
            items = report.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("territory"):
                        referenced_territories.add(item["territory"])

        if not referenced_territories:
            return

        attributions: list[dict] = []
        seen: set[str] = set()
        for territory in sorted(referenced_territories):
            # Try ISO code directly, then convert from full name
            iso = territory if len(territory) == 2 else _TERRITORY_TO_ISO.get(territory, "")
            if iso and iso not in seen:
                text = TERRITORY_ATTRIBUTIONS.get(iso)
                if text:
                    attributions.append({"territory": territory, "text": text})
                    seen.add(iso)

        if attributions:
            report["attributions"] = attributions
        report["crewCostDisclaimer"] = MANDATORY_DISCLAIMER

    # ── Gap-fix patches ───────────────────────────────────────────────────────

    @classmethod
    def _patch_stacking_logic(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
    ) -> None:
        """Validate that AI-generated stacking combinations are supported by the DB.

        Strips hallucinated stacking that isn't confirmed by the ``stackable_with``
        field and ensures regional incentives carry the correct ``scope`` and
        ``parentTerritory`` from the dataset.
        """
        estimates = report.get("incentiveEstimates")
        if not isinstance(estimates, list):
            return

        for est in estimates:
            if not isinstance(est, dict):
                continue
            program_name = est.get("program", "")
            db_row = incentives_by_program.get(program_name)
            if db_row is None:
                continue

            # Patch scope / parentTerritory from DB truth
            db_scope = db_row.get("scope")
            if db_scope and not est.get("scope"):
                est["scope"] = db_scope

            db_parent = db_row.get("parent_territory")
            if db_parent and not est.get("parentTerritory"):
                est["parentTerritory"] = db_parent

            # Validate stackableWith against DB stackable_with
            import json as _json
            db_stackable_raw = db_row.get("stackable_with")
            if db_stackable_raw:
                try:
                    db_stackable: list[str] = (
                        _json.loads(db_stackable_raw)
                        if isinstance(db_stackable_raw, str)
                        else list(db_stackable_raw)
                    )
                except (ValueError, TypeError):
                    db_stackable = []

                ai_stackable = est.get("stackableWith")
                if isinstance(ai_stackable, list) and ai_stackable:
                    # Strip any AI-invented entries not in DB
                    validated = [p for p in ai_stackable if p in db_stackable]
                    if len(validated) != len(ai_stackable):
                        removed = set(ai_stackable) - set(validated)
                        warnings.append(
                            f"[incentiveEstimates] {program_name}: stripped hallucinated stacking {removed}"
                        )
                        est["stackableWith"] = validated or None
                elif not ai_stackable:
                    # AI didn't populate it — fill from DB
                    est["stackableWith"] = db_stackable or None

    @classmethod
    def _patch_weather_risk(
        cls,
        report: dict,
        weather_data: list[dict],
        shoot_months: list[int] | None,
        ext_int_ratio: float | None,
        warnings: list[str],
    ) -> None:
        """Cross-reference weather data against shoot months and ext/int ratio.

        - Injects weather risk into ``keyRisks`` at the TOP of the array.
        - Applies a score penalty if ext/int ratio is high.
        - Populates ``weatherRiskImpact`` on the location ranking.
        """
        if not shoot_months or not weather_data:
            return

        # Build index: (territory_lower, month) → weather row
        weather_index: dict[tuple[str, int], dict] = {}
        for w in weather_data:
            key = (str(w.get("territory", "")).lower(), int(w.get("month") or 0))
            weather_index[key] = w

        rankings = report.get("locationRankings", [])
        for loc in rankings:
            if not isinstance(loc, dict):
                continue
            territory = loc.get("name", "")
            territory_lower = territory.lower()

            high_risk_months: list[int] = []
            for month in shoot_months:
                w = weather_index.get((territory_lower, month))
                if not w:
                    continue
                storm = str(w.get("storm_risk") or "").lower()
                rainfall = float(w.get("avg_rainfall_mm") or 0)
                if storm == "high" or rainfall > 100:
                    high_risk_months.append(month)

            if not high_risk_months:
                continue

            month_names = [calendar.month_abbr[m] for m in high_risk_months]
            risk_msg = (
                f"Weather risk: shooting in {', '.join(month_names)} overlaps with "
                f"adverse conditions in {territory}"
            )

            key_risks = loc.setdefault("keyRisks", [])
            if not any("weather risk" in r.lower() for r in key_risks if isinstance(r, str)):
                key_risks.insert(0, risk_msg)
                warnings.append(
                    f"[locationRankings] {territory}: weather risk injected into keyRisks"
                )

            # Exterior exposure amplification
            if ext_int_ratio is not None and ext_int_ratio >= 0.7:
                exposure_msg = (
                    f"{ext_int_ratio * 100:.0f}% exterior scenes — "
                    f"weather delays will affect majority of schedule in {territory}"
                )
                if not any("exterior" in r.lower() for r in key_risks if isinstance(r, str)):
                    key_risks.insert(0, exposure_msg)

            # Score penalty
            if ext_int_ratio is not None and ext_int_ratio >= 0.5:
                penalty = min(10, len(high_risk_months) * 3)
                current_score = loc.get("score", 50)
                loc["score"] = max(0, current_score - penalty)
                loc["weatherRiskImpact"] = -penalty
                warnings.append(
                    f"[locationRankings] {territory}: score penalised by {penalty} "
                    f"(weather + {ext_int_ratio:.0%} exterior ratio)"
                )

    @classmethod
    def _patch_eligibility(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        producer_country: str | None,
        co_production_status: str | None,
        warnings: list[str],
    ) -> None:
        """Ensure eligibility assumptions are explicit, not silent.

        - If ``producer_country`` is provided, checks it against
          ``nationality_requirements`` in the DB and sets ``eligibilityStatus``.
        - If ``producer_country`` is absent, appends assumption notes to
          ``requirements`` so the reader knows to verify jurisdiction.
        """
        import json as _json

        estimates = report.get("incentiveEstimates")
        if not isinstance(estimates, list):
            return

        for est in estimates:
            if not isinstance(est, dict):
                continue
            program_name = est.get("program", "")
            db_row = incentives_by_program.get(program_name)
            if not db_row:
                continue

            nat_reqs_raw = db_row.get("nationality_requirements")
            if not nat_reqs_raw:
                # No nationality restriction — mark as qualified if not already set
                if not est.get("eligibilityStatus"):
                    est["eligibilityStatus"] = "qualified"
                continue

            try:
                nat_reqs: list[str] = (
                    _json.loads(nat_reqs_raw)
                    if isinstance(nat_reqs_raw, str)
                    else list(nat_reqs_raw)
                )
            except (ValueError, TypeError):
                nat_reqs = []

            if not nat_reqs:
                continue

            if producer_country:
                qualifies = producer_country.upper() in [n.upper() for n in nat_reqs]
                if qualifies:
                    if not est.get("eligibilityStatus"):
                        est["eligibilityStatus"] = "qualified"
                        est.setdefault("eligibilityNote", f"{producer_country} registered entity qualifies directly.")
                else:
                    co_prod_ok = bool(db_row.get("co_production_eligible"))
                    spv_ok = bool(db_row.get("spv_eligible"))
                    if not est.get("eligibilityStatus"):
                        if spv_ok:
                            est["eligibilityStatus"] = "requires_spv"
                        elif co_prod_ok:
                            est["eligibilityStatus"] = "requires_co_production"
                        else:
                            est["eligibilityStatus"] = "ineligible"

                    if not est.get("eligibilityNote"):
                        options = []
                        if spv_ok:
                            options.append("establish a local SPV")
                        if co_prod_ok:
                            options.append("qualify via co-production treaty")
                        note = (
                            f"{producer_country} entity — {program_name} requires "
                            f"{'/'.join(nat_reqs)} tax liability."
                        )
                        if options:
                            note += f" Options: {'; '.join(options)}."
                        est["eligibilityNote"] = note
                    warnings.append(
                        f"[incentiveEstimates] {est.get('territory')}/{program_name}: "
                        f"producer_country={producer_country} not in nationality_requirements"
                    )
            else:
                # No producer country — add assumption note to requirements
                reqs = est.setdefault("requirements", [])
                territory = db_row.get("territory", "")
                assumption = (
                    f"Eligibility assumes a qualifying {territory} entity — "
                    f"verify company jurisdiction before committing."
                )
                if not any("eligibility assumes" in r.lower() for r in reqs if isinstance(r, str)):
                    reqs.append(assumption)

    # ── Financial calculation enforcement ─────────────────────────────────

    @classmethod
    def _patch_financial_calculations(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
        *,
        budget_gbp_override: float | None = None,
    ) -> None:
        """Enforce qualifying-spend deductions and rate-tier logic on rebate
        estimates.  Entirely data-driven — uses ``qualifying_spend_cap_pct``,
        ``cap_per_person``, ``cap_amount``, and ``rate_tier_json`` from the
        dataset without hardcoding any territory names.
        """
        import json as _json

        budget_gbp = budget_gbp_override or cls._extract_budget_gbp(report)
        if budget_gbp is None:
            return  # Cannot validate without a budget figure

        territory_incentives = _index_incentives_by_territory(
            list(incentives_by_program.values())
        )

        # ── Patch incentiveEstimates ──────────────────────────────────────
        estimates = report.get("incentiveEstimates")
        if isinstance(estimates, list):
            for est in estimates:
                if not isinstance(est, dict):
                    continue
                program_name = est.get("program", "")
                db_row = incentives_by_program.get(program_name)
                if db_row is None:
                    continue

                territory = est.get("territory") or db_row.get("territory", "")
                corrected = cls._compute_corrected_rebate(db_row, budget_gbp, territory_incentives)
                if corrected is None:
                    continue

                # Override if the AI-generated figure is materially overstated
                ai_rebate = _parse_money_string(est.get("estimatedRebate"))
                if ai_rebate is not None and corrected["net_rebate"] > 0:
                    deviation = abs(ai_rebate - corrected["net_rebate"]) / corrected["net_rebate"]
                    if deviation > _REBATE_TOLERANCE:
                        currency = db_row.get("currency") or "GBP"
                        symbol = _currency_symbol(currency)
                        est["estimatedRebate"] = (
                            f"{symbol}{corrected['net_rebate']:,.0f} net"
                            f" ({symbol}{corrected['gross_rebate']:,.0f} gross)"
                        )
                        est["qualifyingSpendApplied"] = (
                            f"{symbol}{corrected['qualifying_spend']:,.0f}"
                            f" ({corrected['qualifying_spend_pct']:.0f}% of budget)"
                        )
                        if corrected.get("programme_note"):
                            est["programmeNote"] = corrected["programme_note"]
                        warnings.append(
                            f"[incentiveEstimates] {territory}/{program_name}: "
                            f"rebate corrected from {symbol}{ai_rebate:,.0f} "
                            f"to {symbol}{corrected['net_rebate']:,.0f} "
                            f"(qualifying spend rule)"
                        )

        # ── Patch financialAnalysis.budgetScenarios ───────────────────────
        fin = report.get("financialAnalysis")
        if isinstance(fin, dict):
            scenarios = fin.get("budgetScenarios")
            if isinstance(scenarios, list):
                for scenario in scenarios:
                    if not isinstance(scenario, dict):
                        continue
                    territory = scenario.get("territory", "")
                    rows = territory_incentives.get(territory, [])
                    if not rows:
                        continue
                    best = _best_incentive(rows)
                    corrected = cls._compute_corrected_rebate(best, budget_gbp, territory_incentives)
                    if corrected is None:
                        continue

                    currency = best.get("currency") or "GBP"
                    symbol = _currency_symbol(currency)

                    # Overwrite with corrected breakdown
                    scenario["totalBudget"] = f"{symbol}{budget_gbp:,.0f}"
                    scenario["qualifyingSpendPct"] = f"{corrected['qualifying_spend_pct']:.0f}%"
                    scenario["qualifyingSpend"] = f"{symbol}{corrected['qualifying_spend_before_atl']:,.0f}"
                    scenario["rateGross"] = f"{corrected['rate_gross']:g}%"
                    if corrected["rate_net"] and corrected["rate_net"] != corrected["rate_gross"]:
                        scenario["rateNet"] = f"{corrected['rate_net']:g}%"
                    else:
                        scenario.pop("rateNet", None)
                    scenario["grossRebate"] = f"{symbol}{corrected['gross_rebate']:,.0f}"
                    scenario["netRebate"] = f"{symbol}{corrected['net_rebate']:,.0f}"
                    scenario["netBudget"] = f"{symbol}{budget_gbp - corrected['net_rebate']:,.0f}"

                    # Patch intermediate calculation fields rendered by the PDF template
                    atl_amount = corrected.get("atl_deduction_amount", 0)
                    if atl_amount > 0:
                        scenario["atlDeduction"] = f"{symbol}{atl_amount:,.0f}"
                        scenario["netQualifyingSpend"] = f"{symbol}{corrected['qualifying_spend']:,.0f}"
                    else:
                        scenario.pop("atlDeduction", None)
                        scenario["netQualifyingSpend"] = f"{symbol}{corrected['qualifying_spend_before_atl']:,.0f}"

                    # Correct the programme name when budget-cap triggered a switch
                    if corrected.get("switched_programme"):
                        scenario["programme"] = corrected["switched_programme"]

                    if corrected.get("programme_note"):
                        scenario["notes"] = corrected["programme_note"]
                    warnings.append(
                        f"[budgetScenarios] {territory}: corrected to "
                        f"qualifying spend {symbol}{corrected['qualifying_spend']:,.0f}, "
                        f"net rebate {symbol}{corrected['net_rebate']:,.0f}"
                    )

    @classmethod
    def _compute_corrected_rebate(
        cls,
        db_row: dict,
        budget_gbp: float,
        territory_incentives: dict[str, list[dict]],
    ) -> dict | None:
        """Compute the correct rebate for a single incentive programme,
        applying qualifying-spend caps, rate-tier thresholds, and ATL
        per-person deductions.  Returns None if the rate is zero/absent.

        All logic is driven by dataset fields — no territory names are
        referenced.
        """
        import json as _json

        rate_gross = _to_float(db_row.get("rate_gross"))
        rate_net = _to_float(db_row.get("rate_net"))
        if (rate_gross is None or rate_gross == 0) and (rate_net is None or rate_net == 0):
            return None

        # Step 1 — qualifying spend cap percentage (e.g. 80%)
        qs_cap_pct = _to_float(db_row.get("qualifying_spend_cap_pct"))
        if qs_cap_pct is not None and 0 < qs_cap_pct <= 100:
            qualifying_spend = budget_gbp * (qs_cap_pct / 100.0)
        else:
            qualifying_spend = budget_gbp
        qualifying_spend_pct = (qualifying_spend / budget_gbp * 100) if budget_gbp > 0 else 100

        # Step 2 — rate-tier logic and budget-cap programme selection
        rate_tier_raw = db_row.get("rate_tier_json")
        cap_amount = _to_float(db_row.get("cap_amount"))
        programme_note: str | None = None
        switched_programme: str | None = None

        # If budget exceeds the programme's hard cap, this programme may not
        # apply.  Check whether the territory has an alternative programme.
        if cap_amount is not None and cap_amount > 0 and budget_gbp > cap_amount:
            territory = db_row.get("territory", "")
            alt_rows = territory_incentives.get(territory, [])
            # Find a programme without a cap (or with a higher cap)
            alternatives = [
                r for r in alt_rows
                if _prog_name(r) != _prog_name(db_row)
                and (_to_float(r.get("cap_amount")) is None or (_to_float(r.get("cap_amount")) or 0) >= budget_gbp)
                and not _is_zero_rate(r.get("rate_gross"), r.get("rate_net"))
            ]
            if alternatives:
                alt = _best_incentive(alternatives)
                switched_programme = _prog_name(alt)
                programme_note = (
                    f"Budget exceeds {_prog_name(db_row) or 'programme'} cap "
                    f"of {_format_cap(cap_amount, db_row.get('cap_currency') or 'GBP')} — "
                    f"{switched_programme or 'alternative programme'} applies instead"
                )
                # Use the alternative programme's rates
                rate_gross = _to_float(alt.get("rate_gross")) or rate_gross
                rate_net = _to_float(alt.get("rate_net")) or rate_net
                rate_tier_raw = alt.get("rate_tier_json")
                # Re-apply qualifying spend cap from alternative
                alt_qs_pct = _to_float(alt.get("qualifying_spend_cap_pct"))
                if alt_qs_pct is not None and 0 < alt_qs_pct <= 100:
                    qualifying_spend = budget_gbp * (alt_qs_pct / 100.0)
                    qualifying_spend_pct = alt_qs_pct

        # If rate tiers exist and the budget does NOT exceed the cap,
        # calculate a blended rate
        effective_rate_gross = rate_gross or 0
        effective_rate_net = rate_net or 0
        if rate_tier_raw and (cap_amount is None or cap_amount == 0 or budget_gbp <= (cap_amount or 0)):
            try:
                tiers = (
                    _json.loads(rate_tier_raw)
                    if isinstance(rate_tier_raw, str)
                    else list(rate_tier_raw)
                )
            except (ValueError, TypeError):
                tiers = []

            if isinstance(tiers, list) and len(tiers) >= 2:
                # Tiers are ordered: first tier has a spend ceiling,
                # second tier is the rate above that ceiling
                # e.g. [{"rate_gross":53,"rate_net":39.75}, {"rate_gross":34,"rate_net":25.5}]
                # The boundary is typically the cap_amount or a spend threshold
                tier_boundary = cap_amount or qualifying_spend
                # Try to determine boundary from tier label
                for tier in tiers:
                    label = str(tier.get("label", "")).lower()
                    # Extract amount from label like "First £15M qualifying spend"
                    import re as _re
                    amount_match = _re.search(r'[£$€](\d+(?:\.\d+)?)\s*[mM]', label)
                    if amount_match:
                        tier_boundary = float(amount_match.group(1)) * 1_000_000
                        break

                if qualifying_spend <= tier_boundary:
                    # All spend is in the first (enhanced) tier
                    effective_rate_gross = _to_float(tiers[0].get("rate_gross")) or effective_rate_gross
                    effective_rate_net = _to_float(tiers[0].get("rate_net")) or effective_rate_net
                else:
                    # Blended: first tier up to boundary, second tier for remainder
                    t1_gross = _to_float(tiers[0].get("rate_gross")) or 0
                    t1_net = _to_float(tiers[0].get("rate_net")) or 0
                    t2_gross = _to_float(tiers[1].get("rate_gross")) or 0
                    t2_net = _to_float(tiers[1].get("rate_net")) or 0
                    spend_t1 = tier_boundary
                    spend_t2 = qualifying_spend - tier_boundary
                    effective_rate_gross = (
                        (t1_gross * spend_t1 + t2_gross * spend_t2) / qualifying_spend
                    ) if qualifying_spend > 0 else t2_gross
                    effective_rate_net = (
                        (t1_net * spend_t1 + t2_net * spend_t2) / qualifying_spend
                    ) if qualifying_spend > 0 else t2_net

        # Step 3 — ATL per-person cap deduction (generic: uses cap_per_person)
        # This is an approximate deduction — we flag it rather than computing
        # an exact figure because we don't know individual pay figures.
        cap_per_person = _to_float(db_row.get("cap_per_person"))
        atl_deduction_note: str | None = None
        atl_deduction_amount: float = 0.0
        qualifying_spend_before_atl = qualifying_spend
        if cap_per_person is not None and cap_per_person > 0:
            # ATL is typically 20-35% of budget; per-person cap may reduce
            # qualifying spend.  We apply 25% (midpoint of 20-35%) budget
            # deduction and flag it for the reader.
            atl_est = budget_gbp * 0.25
            atl_deduction_amount = atl_est
            qualifying_spend = max(0, qualifying_spend - atl_est)
            cap_currency = db_row.get("cap_per_person_currency") or db_row.get("currency") or "GBP"
            atl_deduction_note = (
                f"ATL deduction estimated at 25% of budget "
                f"({_currency_symbol(cap_currency)}{atl_est:,.0f}). "
                f"Per-person ATL fee cap of "
                f"{_currency_symbol(cap_currency)}{cap_per_person:,.0f} "
                f"may further reduce qualifying spend — verify individual fee allocations"
            )

        # Step 4 — compute rebate
        gross_rebate = qualifying_spend * (effective_rate_gross / 100.0)
        net_rebate = qualifying_spend * (effective_rate_net / 100.0) if effective_rate_net else gross_rebate

        result: dict = {
            "qualifying_spend": qualifying_spend,
            "qualifying_spend_before_atl": qualifying_spend_before_atl,
            "qualifying_spend_pct": qualifying_spend_pct,
            "atl_deduction_amount": atl_deduction_amount,
            "rate_gross": effective_rate_gross,
            "rate_net": effective_rate_net,
            "gross_rebate": gross_rebate,
            "net_rebate": net_rebate,
            "programme_note": programme_note,
            "switched_programme": switched_programme,
        }
        if atl_deduction_note:
            result["atl_deduction_note"] = atl_deduction_note
        return result

    @staticmethod
    def _extract_budget_gbp(report: dict) -> float | None:
        """Best-effort extraction of the budget in GBP from the report.

        In v3, budget_gbp_override is always passed from the actual
        budget_amount field, so this is only a last-resort fallback
        for parsing the AI-generated budget string.
        """
        summary = report.get("executiveSummary")
        if isinstance(summary, dict):
            # Try explicit budget string like "£22.5M" or "£6,500,000"
            raw = summary.get("budget")
            if raw:
                parsed = _parse_money_string(str(raw))
                if parsed is not None and parsed > 0:
                    return parsed

        return None

    # ── Reliability warnings (data-driven from warnings_json) ─────────────

    @classmethod
    def _patch_reliability_warnings(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
    ) -> None:
        """Inject warnings from the dataset's ``warnings_json`` into
        ``keyRisks`` for every territory.  Also flags long payment timelines
        (> 180 days) as reliability concerns.  Entirely data-driven — no
        territory names are hardcoded.
        """
        import json as _json

        territory_incentives = _index_incentives_by_territory(
            list(incentives_by_program.values())
        )

        rankings = report.get("locationRankings")
        if not isinstance(rankings, list):
            return

        for loc in rankings:
            if not isinstance(loc, dict):
                continue
            territory = loc.get("name", "")
            rows = territory_incentives.get(territory, [])
            if not rows:
                continue

            key_risks = loc.setdefault("keyRisks", [])
            if not isinstance(key_risks, list):
                loc["keyRisks"] = []
                key_risks = loc["keyRisks"]

            for db_row in rows:
                # Inject dataset warnings that the AI may have omitted
                warn_raw = db_row.get("warnings_json")
                if warn_raw:
                    try:
                        db_warnings: list[str] = (
                            _json.loads(warn_raw) if isinstance(warn_raw, str)
                            else list(warn_raw)
                        )
                    except (ValueError, TypeError):
                        db_warnings = []

                    for w in db_warnings:
                        if not isinstance(w, str):
                            continue
                        # Skip if a similar warning is already present
                        w_lower = w.lower()
                        if any(w_lower[:40] in existing.lower() for existing in key_risks if isinstance(existing, str)):
                            continue
                        key_risks.append(w)
                        warnings.append(
                            f"[locationRankings] {territory}: injected dataset warning"
                        )

                # Flag long payment timelines as reliability concern
                pay_max = _to_float(db_row.get("payment_timeline_days_max"))
                if pay_max is not None and pay_max > 180:
                    months_max = int(pay_max / 30)
                    pay_min = _to_float(db_row.get("payment_timeline_days_min"))
                    months_min = int((pay_min or pay_max) / 30)
                    reliability_msg = (
                        f"Payment timeline {months_min}-{months_max} months — "
                        f"this incentive should not be treated as investor-bankable. "
                        f"Budget cash flow independently."
                    )
                    if not any("investor-bankable" in r.lower() or "payment timeline" in r.lower()
                               for r in key_risks if isinstance(r, str)):
                        key_risks.insert(0, reliability_msg)
                        warnings.append(
                            f"[locationRankings] {territory}: long payment timeline warning injected"
                        )

    # ── Operational requirements (data-driven from eligibility_rules_json) ─

    @classmethod
    def _patch_operational_requirements(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
    ) -> None:
        """Ensure critical operational requirements from ``eligibility_rules_json``
        are surfaced in ``keyRisks`` or ``keyAdvantages``.  For example, if a
        programme requires a local production service company, this MUST appear
        in the report.  Entirely data-driven.
        """
        import json as _json

        territory_incentives = _index_incentives_by_territory(
            list(incentives_by_program.values())
        )

        # Phrases in eligibility rules that indicate mandatory operational
        # requirements a producer may not expect.  These are generic patterns,
        # not country-specific strings.
        _OPERATIONAL_PATTERNS = [
            "production service company",
            "service company required",
            "local entity required",
            "must apply",
            "before principal photography",
            "minimum qualifying",
            "minimum spend",
        ]

        rankings = report.get("locationRankings")
        if not isinstance(rankings, list):
            return

        for loc in rankings:
            if not isinstance(loc, dict):
                continue
            territory = loc.get("name", "")
            rows = territory_incentives.get(territory, [])
            if not rows:
                continue

            key_risks = loc.setdefault("keyRisks", [])
            if not isinstance(key_risks, list):
                loc["keyRisks"] = []
                key_risks = loc["keyRisks"]

            for db_row in rows:
                rules_raw = db_row.get("eligibility_rules_json")
                if not rules_raw:
                    continue
                try:
                    rules: list = (
                        _json.loads(rules_raw) if isinstance(rules_raw, str)
                        else list(rules_raw)
                    )
                except (ValueError, TypeError):
                    continue

                for rule_item in rules:
                    # rules can be strings or dicts like {"rule": "...", "required": true}
                    if isinstance(rule_item, dict):
                        rule_text = str(rule_item.get("rule", ""))
                        is_required = bool(rule_item.get("required", False))
                    elif isinstance(rule_item, str):
                        rule_text = rule_item
                        is_required = True
                    else:
                        continue

                    if not rule_text or not is_required:
                        continue

                    rule_lower = rule_text.lower()
                    is_operational = any(pat in rule_lower for pat in _OPERATIONAL_PATTERNS)
                    if not is_operational:
                        continue

                    # Check if this requirement is already mentioned
                    already = any(
                        rule_lower[:30] in existing.lower()
                        for existing in key_risks
                        if isinstance(existing, str)
                    )
                    if already:
                        continue

                    key_risks.append(rule_text)
                    warnings.append(
                        f"[locationRankings] {territory}: operational requirement injected: "
                        f"{rule_text[:60]}"
                    )

        # Also inject into incentiveEstimates.requirements
        estimates = report.get("incentiveEstimates")
        if isinstance(estimates, list):
            for est in estimates:
                if not isinstance(est, dict):
                    continue
                program_name = est.get("program", "")
                db_row = incentives_by_program.get(program_name)
                if not db_row:
                    continue

                # Merge eligibility_notes as an additional requirement
                notes = db_row.get("eligibility_notes")
                if notes and isinstance(notes, str):
                    reqs = est.setdefault("requirements", [])
                    if isinstance(reqs, list) and not any(
                        notes.lower()[:30] in r.lower()
                        for r in reqs if isinstance(r, str)
                    ):
                        reqs.append(notes)

    # ── Comparable relevance check ────────────────────────────────────────

    @classmethod
    def _patch_comparable_relevance(
        cls,
        report: dict,
        comparables_dataset: list[dict],
        warnings: list[str],
    ) -> None:
        """Flag comparables whose budget is wildly out of range for the
        production being analysed.  Does not remove them (the AI picked them
        for a reason) but adds a caveat to the relevanceDescription.
        """
        budget_gbp = cls._extract_budget_gbp(report)
        if budget_gbp is None:
            return

        comps = report.get("comparables")
        if not isinstance(comps, list):
            return

        for comp in comps:
            if not isinstance(comp, dict):
                continue

            # Try to parse the comparable's budget from the report field
            comp_budget = _parse_money_string(comp.get("budgetRange") or "")
            if comp_budget is None:
                continue

            # Flag if the comparable is >5x or <0.2x the production budget
            ratio = comp_budget / budget_gbp if budget_gbp > 0 else 0
            if ratio > 5.0 or ratio < 0.2:
                existing_desc = comp.get("relevanceDescription", "")
                caveat = (
                    f" [Note: budget gap — this comparable is "
                    f"{'significantly larger' if ratio > 5 else 'significantly smaller'} "
                    f"than the production being analysed]"
                )
                if "budget gap" not in existing_desc.lower():
                    comp["relevanceDescription"] = existing_desc + caveat
                    warnings.append(
                        f"[comparables] {comp.get('title', '?')}: "
                        f"budget ratio {ratio:.1f}x — caveat added"
                    )

    # ── Grant labelling ───────────────────────────────────────────────────

    @classmethod
    def _patch_grant_labelling(
        cls,
        report: dict,
        warnings: list[str],
    ) -> None:
        """Ensure grant amounts are labelled as 'Up to' since they are
        competitive awards, not entitlements.
        """
        opportunities = report.get("fundingOpportunities")
        if not isinstance(opportunities, list):
            return

        for opp in opportunities:
            if not isinstance(opp, dict):
                continue
            opp_type = str(opp.get("type", "")).lower()
            if opp_type != "fund":
                continue

            notes = opp.get("notes") or ""
            if not isinstance(notes, str):
                continue

            # If notes contain a monetary amount without "up to", prefix it
            if notes and not notes.lower().startswith("up to"):
                # Check if it looks like a monetary figure
                import re as _re
                if _re.search(r'[£$€]\s*\d', notes):
                    opp["notes"] = f"Up to {notes}"
                    warnings.append(
                        f"[fundingOpportunities] {opp.get('name', '?')}: "
                        f"added 'Up to' prefix to grant amount"
                    )

    # ── v3: Incentive Reliability ──────────────────────────────────────────

    @classmethod
    def _patch_incentive_reliability(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
    ) -> None:
        """Compute incentiveReliability (0-100) and bankabilityLabel for each
        locationRanking and incentiveEstimate from dataset payment_reliability.
        """
        territory_incentives = _index_incentives_by_territory(
            list(incentives_by_program.values())
        )

        # Patch locationRankings
        rankings = report.get("locationRankings")
        if isinstance(rankings, list):
            for loc in rankings:
                if not isinstance(loc, dict):
                    continue
                territory = loc.get("name", "")
                rows = territory_incentives.get(territory, [])
                if not rows:
                    continue
                best = _best_incentive(rows)
                reliability = _to_float(best.get("payment_reliability"))
                timeline_max = _to_float(best.get("payment_timeline_days_max"))

                # Compute reliability score (0-100)
                if reliability is not None:
                    if reliability >= 0.90:
                        rel_score = 90
                    elif reliability >= 0.70:
                        rel_score = 65
                    elif reliability >= 0.50:
                        rel_score = 40
                    else:
                        rel_score = 15
                else:
                    rel_score = 30  # unknown defaults low

                loc["incentiveReliability"] = rel_score

                # Compute bankability label
                label = cls._compute_bankability_label(reliability, timeline_max)
                loc["bankabilityLabel"] = label

        # Patch incentiveEstimates
        estimates = report.get("incentiveEstimates")
        if isinstance(estimates, list):
            for est in estimates:
                if not isinstance(est, dict):
                    continue
                program_name = est.get("program", "")
                db_row = incentives_by_program.get(program_name)
                if db_row is None:
                    continue
                reliability = _to_float(db_row.get("payment_reliability"))
                timeline_max = _to_float(db_row.get("payment_timeline_days_max"))
                label = cls._compute_bankability_label(reliability, timeline_max)
                est["bankabilityLabel"] = label

    @staticmethod
    def _compute_bankability_label(
        reliability: float | None, timeline_max: float | None
    ) -> str:
        """Return BANKABLE / VERIFY FIRST / NOT BANKABLE per v3 spec.

        When an explicit reliability score exists (admin-curated override),
        it takes precedence over the raw timeline heuristic.  A long timeline
        alone only triggers NOT BANKABLE when reliability is also low/absent.
        """
        if reliability is not None and reliability < 0.50:
            return "NOT BANKABLE"
        # Long timeline with no reliability data → NOT BANKABLE
        if timeline_max is not None and timeline_max > 365 and reliability is None:
            return "NOT BANKABLE"
        if reliability is not None and reliability >= 0.80:
            if timeline_max is None or timeline_max <= 180:
                return "BANKABLE"
        return "VERIFY FIRST"

    # ── v3: Currency Advantage ──────────────────────────────────────────────

    @classmethod
    def _patch_currency_advantage(
        cls,
        report: dict,
        currency_scores: dict | None,
        warnings: list[str],
    ) -> None:
        """Override currencyAdvantage in locationRankings with pre-computed scores."""
        if not currency_scores:
            return

        rankings = report.get("locationRankings")
        if not isinstance(rankings, list):
            return

        for loc in rankings:
            if not isinstance(loc, dict):
                continue
            territory = loc.get("name", "")
            score_data = currency_scores.get(territory)
            if score_data and isinstance(score_data, dict):
                computed = score_data.get("score")
                if computed is not None:
                    old = loc.get("currencyAdvantage", 50)
                    loc["currencyAdvantage"] = computed
                    if abs(old - computed) > 10:
                        warnings.append(
                            f"[locationRankings] {territory}: currencyAdvantage "
                            f"corrected from {old} to {computed} (live FX rate)"
                        )

    # ── v3: Score Recalculation ─────────────────────────────────────────────

    _WEIGHTS = {
        "full": {
            "costEfficiency": 0.25, "crewDepth": 0.20, "infrastructure": 0.20,
            "incentiveStrength": 0.20, "currencyAdvantage": 0.10, "incentiveReliability": 0.05,
        },
        "incentive": {
            "costEfficiency": 0.15, "crewDepth": 0.15, "infrastructure": 0.15,
            "incentiveStrength": 0.40, "currencyAdvantage": 0.10, "incentiveReliability": 0.05,
        },
        "location": {
            "costEfficiency": 0.17, "crewDepth": 0.25, "infrastructure": 0.25,
            "incentiveStrength": 0.13, "currencyAdvantage": 0.10, "incentiveReliability": 0.10,
        },
    }

    @classmethod
    def _recalculate_overall_score(
        cls,
        report: dict,
        production_priority: str,
        warnings: list[str],
    ) -> None:
        """Re-weight overall score using v3 6-dimension weights."""
        weights = cls._WEIGHTS.get(production_priority, cls._WEIGHTS["full"])

        rankings = report.get("locationRankings")
        if not isinstance(rankings, list):
            return

        for loc in rankings:
            if not isinstance(loc, dict):
                continue
            weighted_sum = 0.0
            for dim, weight in weights.items():
                val = loc.get(dim)
                if isinstance(val, (int, float)):
                    weighted_sum += val * weight
                else:
                    weighted_sum += 50 * weight  # default neutral
            new_score = int(round(weighted_sum))
            new_score = max(0, min(100, new_score))
            old_score = loc.get("score", 0)
            loc["score"] = new_score
            if abs(old_score - new_score) > 5:
                warnings.append(
                    f"[locationRankings] {loc.get('name', '?')}: "
                    f"score recalculated from {old_score} to {new_score} "
                    f"(v3 6-dimension weights, priority={production_priority})"
                )

    # ── Narrative prose patching ─────────────────────────────────────────────

    @classmethod
    def _patch_narrative_spend_percentages(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
        *,
        budget_gbp_override: float | None = None,
    ) -> None:
        """Find and correct hallucinated qualifying-spend percentages in prose.

        The AI sometimes invents a qualifying spend % (e.g. "75% of budget")
        that differs from the dataset's qualifying_spend_cap_pct.  This method
        scans key narrative text fields and substitutes the correct figure along
        with recomputed monetary amounts.
        """
        import re as _re

        if budget_gbp_override is None or budget_gbp_override <= 0:
            return

        territory_incentives = _index_incentives_by_territory(
            list(incentives_by_program.values())
        )

        # Build territory → correct qualifying_spend_cap_pct map
        territory_qs: dict[str, float] = {}
        for territory, rows in territory_incentives.items():
            best = _best_incentive(rows)
            qs_pct = _to_float(best.get("qualifying_spend_cap_pct"))
            if qs_pct is not None and 0 < qs_pct <= 100:
                territory_qs[territory] = qs_pct

        if not territory_qs:
            return

        # Pattern: "XX% estimated/of ... allocation/qualifying/budget"
        # Matches things like "75% estimated UK allocation", "75% of qualifying spend"
        pct_pattern = _re.compile(
            r"(\d{1,3})%\s*(?:estimated|of\s+(?:total\s+)?(?:qualifying|budget|spend|allocation))",
            _re.IGNORECASE,
        )

        def _fix_prose(text: str, correct_pct: float, budget: float) -> str | None:
            """Replace wrong qualifying-spend % and derived amounts in *text*."""
            changed = False
            result = text

            for m in list(pct_pattern.finditer(result)):
                found_pct = float(m.group(1))
                if abs(found_pct - correct_pct) > 1:  # tolerance of 1%
                    old_frag = m.group(0)
                    new_frag = old_frag.replace(f"{int(found_pct)}%", f"{int(correct_pct)}%")
                    result = result.replace(old_frag, new_frag, 1)
                    changed = True

                    # Also fix derived monetary amounts: wrong_pct * budget → correct
                    wrong_amount = budget * (found_pct / 100)
                    correct_amount = budget * (correct_pct / 100)
                    for fmt_fn in (_format_millions,):
                        wrong_str = fmt_fn(wrong_amount)
                        correct_str = fmt_fn(correct_amount)
                        if wrong_str and correct_str and wrong_str in result:
                            result = result.replace(wrong_str, correct_str)

            return result if changed else None

        # Fields to scan: executive summary prose, location ranking reasoning,
        # territory deep-dive prose
        patched_count = 0

        # Executive summary keyInsights
        summary = report.get("executiveSummary")
        if isinstance(summary, dict):
            territory = summary.get("recommendedTerritory", "")
            correct_pct = territory_qs.get(territory)
            if correct_pct:
                for key in ("keyInsights", "headline"):
                    text = summary.get(key)
                    if isinstance(text, str):
                        fixed = _fix_prose(text, correct_pct, budget_gbp_override)
                        if fixed:
                            summary[key] = fixed
                            patched_count += 1

        # Location ranking reasoning bullets
        rankings = report.get("locationRankings")
        if isinstance(rankings, list):
            for loc in rankings:
                if not isinstance(loc, dict):
                    continue
                territory = loc.get("name", "")
                correct_pct = territory_qs.get(territory)
                if not correct_pct:
                    continue
                reasons = loc.get("reasoning")
                if isinstance(reasons, list):
                    for i, reason in enumerate(reasons):
                        if isinstance(reason, str):
                            fixed = _fix_prose(reason, correct_pct, budget_gbp_override)
                            if fixed:
                                reasons[i] = fixed
                                patched_count += 1

        # Territory deep-dive prose fields
        dives = report.get("territoryDeepDives")
        if isinstance(dives, list):
            for dive in dives:
                if not isinstance(dive, dict):
                    continue
                territory = dive.get("name", "")
                correct_pct = territory_qs.get(territory)
                if not correct_pct:
                    continue
                for key in ("incentiveSummary", "overview", "financialNotes"):
                    text = dive.get(key)
                    if isinstance(text, str):
                        fixed = _fix_prose(text, correct_pct, budget_gbp_override)
                        if fixed:
                            dive[key] = fixed
                            patched_count += 1

        if patched_count:
            warnings.append(
                f"[narrative] Corrected qualifying-spend percentages "
                f"in {patched_count} prose field(s)"
            )

    # ── v3: Section Explainers (hardcoded) ──────────────────────────────────

    @classmethod
    # ── Production format harmonisation ──────────────────────────────────

    @classmethod
    def _patch_production_format(
        cls,
        report: dict,
        production_format: str | None,
        warnings: list[str],
    ) -> None:
        """Enforce the user-submitted production format across all sections.

        The AI may infer a different format from the script title (e.g. writing
        "Feature Film" when the user submitted "TV Series").  The user-submitted
        format is authoritative.
        """
        if not production_format:
            return

        # Top-level "scale" field often contains format references
        scale = report.get("scale")
        if isinstance(scale, str) and production_format.lower() not in scale.lower():
            old_scale = scale
            # Replace common misidentified format labels
            for wrong_format in (
                "Feature Film", "Feature", "Short Film", "TV Series",
                "Limited Series", "Mini-Series", "Documentary", "Docuseries",
                "Animation", "Animated Feature", "Commercial", "Music Video",
            ):
                if wrong_format.lower() in scale.lower() and wrong_format.lower() != production_format.lower():
                    scale = scale.replace(wrong_format, production_format)
                    break
            if scale == old_scale and production_format.lower() not in scale.lower():
                # No known format found to replace — append the correct format
                scale = f"{scale} ({production_format})"
            report["scale"] = scale
            warnings.append(
                f"[scale] format harmonised: {old_scale!r} → {scale!r}"
            )

        # executiveSummary.format
        summary = report.get("executiveSummary")
        if isinstance(summary, dict):
            fmt = summary.get("format")
            if isinstance(fmt, str) and fmt.lower() != production_format.lower():
                warnings.append(
                    f"[executiveSummary] format overridden: {fmt!r} → {production_format!r}"
                )
                summary["format"] = production_format

        # productionOverview.format / productionOverview.scale
        overview = report.get("productionOverview")
        if isinstance(overview, dict):
            for field in ("format", "scale"):
                val = overview.get(field)
                if isinstance(val, str) and production_format.lower() not in val.lower():
                    for wrong_format in (
                        "Feature Film", "Feature", "Short Film", "TV Series",
                        "Limited Series", "Mini-Series", "Documentary",
                    ):
                        if wrong_format.lower() in val.lower() and wrong_format.lower() != production_format.lower():
                            new_val = val.replace(wrong_format, production_format)
                            warnings.append(
                                f"[productionOverview.{field}] format harmonised: {val!r} → {new_val!r}"
                            )
                            overview[field] = new_val
                            break

    @classmethod
    def _inject_section_explainers(cls, report: dict, datasets: dict) -> None:
        """Inject hardcoded plain-English section explainers per v3 spec Section 09."""
        # Gather template variables
        total_scenes = "N/A"
        ext_pct = "N/A"
        int_pct = "N/A"
        budget_currency = "GBP"

        budget_data = datasets.get("_budget_gbp")
        if isinstance(budget_data, dict):
            budget_currency = budget_data.get("from_currency", "GBP")

        # Keys must be snake_case and nested under scriptAnalysis to match
        # the Jinja2 template (report.scriptAnalysis.sectionExplainers.*).
        explainers = {
            "executive_summary": (
                "How we read your script: We identified scene counts, "
                "interior/exterior ratios, named locations, and languages "
                "actually spoken to build the analysis below. "
                "All figures are estimates — always verify with qualified professionals."
            ),
            "location_strategy": (
                f"How we score territories: Each territory is rated 0–100 across six "
                f"dimensions (Cost Efficiency, Crew Depth, Infrastructure, Incentive "
                f"Strength, Currency Advantage, Incentive Reliability), weighted by your "
                f"stated production priority. Your budget currency ({budget_currency}) is "
                f"compared against each territory's local currency to calculate "
                f"purchasing power advantage."
            ),
            "financial_analysis": (
                "How we calculate rebates: We apply the qualifying spend rule "
                "(typically 80% of budget), check programme caps, deduct non-qualifying "
                "above-the-line spend (estimated 25%), then apply gross and net rates. "
                "The headline number is your estimated out-of-pocket budget after "
                "incentives. All figures are estimates — verify with a production "
                "accountant and the relevant film commission before including in "
                "investor documents."
            ),
            "territory_deep_dives": (
                "How to read territory profiles: Each territory below includes a "
                "breakdown of its incentive programmes, crew cost estimates, and "
                "location-specific considerations drawn from your script analysis."
            ),
            "incentive_analysis": (
                "How incentives work: A tax incentive is money returned to your "
                "production after you spend it in that territory. The rate tells you "
                "how much you get back per pound/dollar/euro of qualifying spend. "
                "The qualifying spend rule limits which spend counts (e.g. 80% of "
                "total budget). The payment timeline tells you when you receive it. "
                "Bankability indicates whether a lender will advance funds against "
                "the incentive before it is paid out — 'BANKABLE' means the "
                "incentive has a strong enough track record of timely payment that "
                "most gap/cash-flow lenders will accept it as collateral in your "
                "financing plan; 'CONDITIONALLY BANKABLE' means some lenders will "
                "accept it with a discount or additional security; 'NOT BANKABLE' "
                "means payment is too slow or uncertain to rely on for cash-flow "
                "financing."
            ),
            "crew_costs": (
                "How we estimate crew costs: Day rates are sourced from union/guild "
                "published scales and converted to GBP at the live exchange rate used "
                "in this report. Actual rates vary with experience, negotiation, and "
                "market conditions."
            ),
            "funding_opportunities": (
                "How we select funding opportunities: Grants and funds are matched "
                "by territory relevance, eligibility criteria, and current open status. "
                "Always verify deadlines and requirements directly with the funding body."
            ),
            "weather_logistics": (
                "How we assess weather: We look up monthly rainfall, temperature, "
                "storm risk, and daylight hours for your specific shoot months in "
                "each territory, then cross-reference with your script's exterior "
                "scene percentage. High exterior + rainy shoot month = a flag. "
                "All data is from historical averages — actual conditions will vary."
            ),
            "comparable_productions": (
                "How we select comparables: Comparables are matched on genre, budget "
                "tier (within 0.5x–2x of your budget), and territory relevance. We "
                "note explicitly when a comparable has a meaningful budget gap from "
                "your production."
            ),
        }

        # Nest under scriptAnalysis so the template can access via
        # report.scriptAnalysis.sectionExplainers.<key>
        sa = report.get("scriptAnalysis")
        if isinstance(sa, dict):
            sa["sectionExplainers"] = explainers
        else:
            report["scriptAnalysis"] = {"sectionExplainers": explainers}

        # Also keep a top-level reference for any non-template consumers
        report["sectionExplainers"] = explainers


# ── Helpers ───────────────────────────────────────────────────────────────────


def _prog_name(row: dict) -> str:
    """Return the programme name from a row, checking both DB and test keys."""
    return row.get("program_name") or row.get("program") or ""


def _index_incentives(incentives: list[dict]) -> dict[str, dict]:
    """Index incentive rows by programme name (case-insensitive).

    Checks both ``program_name`` (used in tests / AI output) and ``program``
    (the actual DB column name) so the index works in all environments.
    """
    result: dict[str, dict] = {}
    for row in incentives:
        if not isinstance(row, dict):
            continue
        name = row.get("program_name") or row.get("program")
        if name:
            result[name] = row
            result[name.lower()] = row
    return result


def _index_incentives_by_territory(incentives: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for row in incentives:
        if not isinstance(row, dict):
            continue
        t = row.get("territory") or ""
        result.setdefault(t, []).append(row)
    return result


def _best_incentive(rows: list[dict]) -> dict:
    """Pick the row with the highest rate_gross (fallback to rate_net)."""

    def _key(r: dict) -> float:
        rate = r.get("rate_gross") or r.get("rate_net") or 0
        try:
            return float(rate)
        except (TypeError, ValueError):
            return 0.0

    return max(rows, key=_key)


def _format_rate(rate_gross: Any, rate_net: Any) -> str | None:
    gross = _to_float(rate_gross)
    net = _to_float(rate_net)
    if gross is not None and gross > 0:
        return f"{gross:g}%"
    if net is not None and net > 0:
        return f"{net:g}%"
    return None


def _format_cap(cap_amount: Any, cap_currency: str) -> str | None:
    """Format cap as human-readable string. Returns None if no cap."""
    if cap_amount is None:
        return None
    amount = _to_float(cap_amount)
    if amount is None:
        return None
    if amount == 0:
        return "No cap"
    symbol = _currency_symbol(cap_currency)
    if amount >= 1_000_000:
        return f"{symbol}{amount / 1_000_000:g}M"
    if amount >= 1_000:
        return f"{symbol}{amount / 1_000:g}K"
    return f"{symbol}{amount:g}"


def _format_money(amount: Any, currency: str) -> str:
    val = _to_float(amount)
    if val is None:
        return "See programme terms"
    symbol = _currency_symbol(currency)
    if val >= 1_000_000:
        return f"{symbol}{val / 1_000_000:g}M"
    if val >= 1_000:
        return f"{symbol}{val / 1_000:g}K"
    return f"{symbol}{val:g}"


def _format_millions(amount: float) -> str | None:
    """Format a GBP amount as '£XXM' or '£XX.XM' for prose matching."""
    if amount < 100_000:
        return None
    m = amount / 1_000_000
    if m == int(m):
        return f"£{int(m)}M"
    return f"£{m:.1f}M"


def _is_zero_rate(rate_gross: Any, rate_net: Any) -> bool:
    gross = _to_float(rate_gross)
    net = _to_float(rate_net)
    g_zero = gross is None or gross == 0
    n_zero = net is None or net == 0
    return g_zero and n_zero


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _currency_symbol(currency: str) -> str:
    return {
        "GBP": "£",
        "USD": "$",
        "EUR": "€",
        "ZAR": "R",
        "HUF": "Ft ",
        "NGN": "₦",
        "AUD": "A$",
        "CAD": "C$",
    }.get((currency or "").upper(), f"{currency} ")


def _parse_money_string(text: Any) -> float | None:
    """Best-effort parse of a monetary string like '£22.5M', '$6,500,000',
    '£18M net', '£7,950,000 - £10,500,000' (takes the first figure).

    Returns a float in base units (e.g. 22_500_000 for £22.5M), or None.
    """
    import re as _re

    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None

    # Strip leading approximation markers and currency symbols
    raw = _re.sub(r'^[~≈]\s*', '', raw)
    raw = _re.sub(r'^[£$€R₦]\s*', '', raw)
    # Also strip "A$", "C$", "Ft " prefixes
    raw = _re.sub(r'^(?:A\$|C\$|Ft\s*)', '', raw)

    # Match patterns like "22.5M", "6,500,000", "18M", "7.95M"
    match = _re.match(r'([\d,]+(?:\.\d+)?)\s*([MmKkBb])?', raw)
    if not match:
        return None

    number_str = match.group(1).replace(',', '')
    try:
        value = float(number_str)
    except ValueError:
        return None

    multiplier_char = (match.group(2) or '').upper()
    multipliers = {'M': 1_000_000, 'K': 1_000, 'B': 1_000_000_000}
    value *= multipliers.get(multiplier_char, 1)

    return value if value > 0 else None
