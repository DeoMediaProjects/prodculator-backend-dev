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

# Data freshness threshold — flag incentives older than this many days
_STALE_DAYS = 365

# Default ATL (above-the-line) deduction percentage.  Tax credit programmes
# exclude above-the-line costs from qualifying spend — 15 % of total budget
# is a standard conservative assumption used in UK/EU tax credit modelling.
_DEFAULT_ATL_PCT = 0.15

# rate_type values that trigger automatic ATL deduction
_TAX_CREDIT_RATE_TYPES = {"tax_credit", "enhanced_tax_credit"}


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

        # Producer's original budget currency and amount
        budget_currency = datasets.get("_budget_currency", "GBP")
        budget_original_amount = datasets.get("_budget_amount")
        # FX rates from budget_currency → each territory's incentive currency
        fx_rates_from_budget = datasets.get("_fx_rates_from_budget") or {}

        cls._patch_incentive_estimates(
            report, incentives_by_program, warnings,
            budget_gbp=budget_gbp_override,
        )
        cls._patch_location_rankings(
            report, incentives_by_program, warnings,
            budget_gbp=budget_gbp_override,
        )
        # NOTE: _patch_territory_deep_dives is called later (after score
        # recalculation) so deep dives get authoritative scores.
        cls._patch_executive_summary(
            report, incentives_by_program, warnings,
        )
        cls._patch_crew_cost_territories(report, warnings)
        cls._patch_crew_insights(
            report, datasets.get("crew_costs", []), warnings,
            budget_currency=budget_currency,
        )
        cls._patch_cast_insights(report, datasets.get("cast_costs", []), warnings)
        cls._patch_attributions(report, datasets, warnings)

        # Gap-fix patches
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

        # Sort locationRankings by descending score so the best territory is #1
        rankings = report.get("locationRankings")
        if isinstance(rankings, list) and len(rankings) > 1:
            rankings.sort(
                key=lambda loc: loc.get("score", 0) if isinstance(loc, dict) else 0,
                reverse=True,
            )
            # Update executiveSummary to match top-ranked territory
            top = rankings[0]
            if isinstance(top, dict) and top.get("name"):
                summary = report.get("executiveSummary")
                if isinstance(summary, dict):
                    # Propagate recalculated score
                    new_score = top.get("score")
                    if isinstance(new_score, int):
                        old_score = summary.get("recommendedTerritoryScore")
                        if old_score != new_score:
                            summary["recommendedTerritoryScore"] = new_score
                            warnings.append(
                                f"[executiveSummary] recommendedTerritoryScore "
                                f"updated from {old_score} to {new_score}"
                            )

                    old_rec = summary.get("recommendedTerritory", "")
                    new_rec = top["name"]
                    if old_rec and old_rec != new_rec:
                        summary["recommendedTerritory"] = new_rec
                        warnings.append(
                            f"[executiveSummary] recommendedTerritory changed "
                            f"from {old_rec} to {new_rec} (highest recalculated score)"
                        )
                        # Re-run executive summary patch with the new top territory
                        cls._patch_executive_summary(
                            report, incentives_by_program, warnings,
                        )

        # Propagate recalculated scores to territoryDeepDives
        # (must run AFTER _recalculate_overall_score so scores are authoritative)
        ranking_scores: dict[str, int] = {}
        rankings = report.get("locationRankings")
        if isinstance(rankings, list):
            for loc in rankings:
                if isinstance(loc, dict) and loc.get("name") and isinstance(loc.get("score"), int):
                    ranking_scores[loc["name"]] = loc["score"]
        cls._patch_territory_deep_dives(
            report, incentives_by_program, warnings,
            ranking_scores=ranking_scores or None,
        )

        cls._patch_production_format(
            report, datasets.get("_production_format"), warnings
        )
        cls._patch_shoot_duration_context(
            report, datasets.get("_production_format"), warnings
        )
        cls._patch_deadline_proximity(report, warnings)
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
        """Patch DB-authoritative fields on incentiveEstimates.

        Monetary figures (estimatedRebate) are now pre-computed and provided to
        the AI via DERIVED: TERRITORY FINANCIALS, so this method only enforces
        structural DB fields: rate, cap, payment timeline, eligibility, source.
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

            territory = est.get("territory") or db_row.get("territory", "")

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

            # --- Zero-rate guard: set incentiveStrength to 0 and neutralise narrative ---
            if _is_zero_rate(rate_gross, rate_net):
                est["incentiveStrength"] = 0
                # Overwrite any AI-generated praise so the display doesn't contradict
                # the zero score.  Only set if the AI didn't already leave it blank.
                if not est.get("eligibilityNote"):
                    est["eligibilityNote"] = (
                        "Rate not available in dataset — no financial incentive calculated. "
                        "Verify programme status with the relevant film commission."
                    )
                # Remove a fabricated estimatedRebate if the rate is zero
                if est.get("estimatedRebate") and est["estimatedRebate"] not in ("£0", "$0", "€0", "N/A", ""):
                    est["estimatedRebate"] = "N/A"

            # --- Cap ---
            cap_amount = db_row.get("cap_amount")
            cap_currency = db_row.get("cap_currency") or "GBP"
            canonical_cap = _format_cap(cap_amount, cap_currency)
            # For tiered programmes (e.g. IFTC), cap_amount is the enhanced-rate
            # threshold, not the hard programme exclusion cap.  Annotate it so
            # producers understand the cap is a rate boundary, not an eligibility cliff.
            if canonical_cap and db_row.get("rate_tier_json"):
                import re as _re_cap
                tier_raw = db_row["rate_tier_json"]
                try:
                    tiers = _json.loads(tier_raw) if isinstance(tier_raw, str) else list(tier_raw)
                    has_boundary = any(
                        _re_cap.search(r'[£$€]\d', str(t.get("label", "")))
                        for t in tiers if isinstance(t, dict)
                    )
                    if has_boundary:
                        canonical_cap = f"Enhanced rate cap {canonical_cap}"
                except (ValueError, TypeError):
                    pass
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
            if qs_min is not None and qs_min > 0:
                est["qualifyingSpend"] = _format_money(qs_min, qs_currency)
            elif not est.get("qualifyingSpend"):
                est["qualifyingSpend"] = "No minimum threshold"

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
        """Patch DB-authoritative structural fields on locationRankings.

        rebateAmount is now pre-computed and provided to the AI verbatim, so
        this method only enforces rebatePercent, paymentSpeed, staleness, and
        zero-rate guard.
        """
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

            best = _best_incentive(rows)
            effective_rate = _format_rate(best.get("rate_gross"), best.get("rate_net"))
            if effective_rate and loc.get("rebatePercent") != effective_rate:
                warnings.append(
                    f"[locationRankings] {territory}: "
                    f"rebatePercent overridden {loc.get('rebatePercent')!r} → {effective_rate!r}"
                )
                loc["rebatePercent"] = effective_rate

            # --- Zero-rate guard ---
            if _is_zero_rate(best.get("rate_gross"), best.get("rate_net")):
                loc["incentiveStrength"] = 0

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
        ranking_scores: dict[str, int] | None = None,
    ) -> None:
        """Patch score propagation and paymentSpeed on territoryDeepDives.

        Rebate amounts and strings are now pre-computed and provided to the AI
        verbatim, so this method only propagates recalculated scores and
        patches paymentSpeed from the DB.
        """
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

    # ── executiveSummary ──────────────────────────────────────────────────────

    @classmethod
    def _patch_executive_summary(
        cls,
        report: dict,
        incentives_by_program: dict[str, dict],
        warnings: list[str],
    ) -> None:
        """Patch paymentSpeed on executiveSummary from DB.

        Monetary figures (recommendedTerritoryRebate, headlineNetBudget) are
        now pre-computed and provided to the AI verbatim, so this method only
        enforces the DB-authoritative paymentSpeed field.
        """
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

        # --- paymentSpeed ---
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
        locationRankings. Crew rate strings are pre-computed by
        _pre_compute_territory_financials and already in budget currency.
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
        *,
        budget_currency: str = "GBP",
    ) -> None:
        """Inject FX metadata (fxRate, fxDate, currency) into crewInsights rows.
        Crew rate strings (costVsUSD) are pre-computed by
        _pre_compute_territory_financials and already in budget currency.
        """
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

        from app.modules.reports.service import _TERRITORY_TO_ISO, _ISO_TO_TERRITORY

        for insight in insights:
            if not isinstance(insight, dict):
                continue
            territory = insight.get("territory", "")
            rows = crew_by_territory.get(territory, [])
            if not rows:
                iso = _TERRITORY_TO_ISO.get(territory, "")
                rows = crew_by_territory.get(iso, [])
            if not rows:
                full = _ISO_TO_TERRITORY.get(territory, "")
                rows = crew_by_territory.get(full, [])
            if not rows:
                continue

            for row in rows:
                if row.get("union_rate_gbp") or row.get("non_union_rate_gbp"):
                    insight["fxRate"] = row.get("fx_rate")
                    insight["fxDate"] = row.get("fx_date")
                    insight["currency"] = budget_currency
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
        alt: dict | None = None  # set if programme switch occurs

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
                # Distinguish two tier formats:
                #
                # 1. Spend-boundary tiers (UK IFTC, Spain):
                #    [{"label":"First £15M...","rate_gross":53},
                #     {"label":"Above £15M...","rate_gross":34}]
                #    → different rates at different spend thresholds, need blending
                #
                # 2. Additive uplift components (Georgia, NM, Czech):
                #    [{"label":"Base credit","rate_gross":20},
                #     {"label":"With logo","rate_gross":10}]
                #    → components that sum to the headline rate; the DB's
                #      rate_gross/rate_net already reflects the correct total.
                #
                # We detect spend-boundary tiers by the presence of a monetary
                # amount (e.g. "€1M", "£15M") in any tier label.  If none is
                # found the tiers are informational and we keep the headline rates.
                import re as _re
                tier_boundary: float | None = None
                for tier in tiers:
                    label = str(tier.get("label", "")).lower()
                    amount_match = _re.search(r'[£$€](\d+(?:\.\d+)?)\s*[mM]', label)
                    if amount_match:
                        tier_boundary = float(amount_match.group(1)) * 1_000_000
                        break

                if tier_boundary is not None:
                    # Spend-boundary tiers — apply blended rate logic
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
                # else: additive/informational tiers — headline rates are correct

        # Step 3 — ATL (above-the-line) deduction
        #
        # Tax credit programmes exclude ATL costs (writer, director, lead cast
        # fees) from qualifying spend.  We apply a default 15 % deduction for
        # any tax-credit programme — this is universal, not gated on whether
        # the programme has a per-person cap.  The per-person cap is a separate
        # mechanism that may further reduce qualifying spend.
        #
        # Cash rebate programmes (Hungary NFI, Malta MFC) typically include all
        # local spend, so no ATL deduction is applied for those.
        active_row = alt if alt is not None else db_row
        rate_type = (active_row.get("rate_type") or db_row.get("rate_type") or "").lower()
        cap_per_person = _to_float(db_row.get("cap_per_person"))
        atl_deduction_note: str | None = None
        atl_deduction_amount: float = 0.0
        qualifying_spend_before_atl = qualifying_spend
        apply_atl = rate_type in _TAX_CREDIT_RATE_TYPES or (
            cap_per_person is not None and cap_per_person > 0
        )
        if apply_atl:
            atl_est = budget_gbp * _DEFAULT_ATL_PCT
            atl_deduction_amount = atl_est
            qualifying_spend = max(0, qualifying_spend - atl_est)
            currency_label = db_row.get("currency") or "GBP"
            symbol = _currency_symbol(currency_label)
            atl_deduction_note = (
                f"ATL deduction estimated at {_DEFAULT_ATL_PCT:.0%} of budget "
                f"({symbol}{atl_est:,.0f})"
            )
            if cap_per_person is not None and cap_per_person > 0:
                cap_currency = db_row.get("cap_per_person_currency") or currency_label
                atl_deduction_note += (
                    f". Per-person ATL fee cap of "
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

        import re as _re_comp

        for comp in comps:
            if not isinstance(comp, dict):
                continue

            comp_budget_raw = comp.get("budgetRange") or ""
            # Only apply ratio check when the comparable budget is clearly in GBP (£ symbol).
            # Foreign-currency budgets ($, €, etc.) cannot be reliably compared without
            # live FX rates, so skip the ratio check for those to avoid false flags.
            if not _re_comp.match(r'^[~≈]?\s*£', comp_budget_raw.strip()):
                continue

            comp_budget = _parse_money_string(comp_budget_raw)
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

    # ── shoot duration context ────────────────────────────────────────────────

    # Thresholds (in shooting days) above which a keyFlag is injected to
    # contextualise the schedule for financiers / completion bond underwriters.
    _LONG_SHOOT_THRESHOLDS: dict[str, int] = {
        "TV Pilot": 60,
        "TV Series": 100,
        "Limited Series": 80,
        "Feature Film": 80,
    }
    _LONG_SHOOT_DEFAULT = 100  # fallback for unknown formats

    @classmethod
    def _patch_shoot_duration_context(
        cls,
        report: dict,
        production_format: str | None,
        warnings: list[str],
    ) -> None:
        """Add a keyFlag contextualising unusually long shoot durations.

        Completion bond underwriters and financiers will question a long shoot
        timeline if the report doesn't explain it. When ``shootDays`` exceeds a
        format-appropriate threshold, inject a flag that converts to weeks and
        provides context.
        """
        summary = report.get("executiveSummary")
        if not isinstance(summary, dict):
            return

        shoot_days = summary.get("shootDays")
        if not isinstance(shoot_days, (int, float)) or shoot_days <= 0:
            return

        fmt = (production_format or "").strip()
        threshold = cls._LONG_SHOOT_THRESHOLDS.get(fmt, cls._LONG_SHOOT_DEFAULT)

        if shoot_days < threshold:
            return

        weeks = max(1, (int(shoot_days) + 4) // 5)

        flag = (
            f"Extended shoot timeline: {int(shoot_days)} shooting days (~{weeks} weeks). "
            f"This is a significant schedule for "
            f"{'a ' + fmt.lower() if fmt else 'this format'} "
            f"and may require phased production, multiple unit scheduling, "
            f"or a detailed schedule breakdown for completion bond assessment."
        )

        key_flags = summary.get("keyFlags")
        if not isinstance(key_flags, list):
            key_flags = []
            summary["keyFlags"] = key_flags

        # Don't duplicate if already present
        if any("shoot timeline" in f.lower() or "shooting days" in f.lower() for f in key_flags):
            return

        key_flags.append(flag)
        warnings.append(
            f"[executiveSummary] keyFlag injected: extended shoot timeline "
            f"({int(shoot_days)} days / ~{weeks} weeks)"
        )

    # ── deadline proximity flagging ──────────────────────────────────────────

    # Deadlines within this many days of the report date are flagged as urgent
    _DEADLINE_URGENT_DAYS = 56  # 8 weeks

    @classmethod
    def _patch_deadline_proximity(
        cls,
        report: dict,
        warnings: list[str],
    ) -> None:
        """Flag imminent funding/festival deadlines in the action timeline.

        Scans ``fundingOpportunities`` for deadlines within
        ``_DEADLINE_URGENT_DAYS`` of today and injects urgent action items into
        ``executiveSummary.actionTimeline``.
        """
        from datetime import date, timedelta
        import re as _re

        opportunities = report.get("fundingOpportunities")
        if not isinstance(opportunities, list):
            return

        summary = report.get("executiveSummary")
        if not isinstance(summary, dict):
            return

        today = date.today()
        cutoff = today + timedelta(days=cls._DEADLINE_URGENT_DAYS)

        urgent: list[tuple[str, date]] = []
        for opp in opportunities:
            if not isinstance(opp, dict):
                continue
            name = opp.get("name", "")
            deadline_raw = opp.get("deadline") or ""
            # Extract a date from strings like "Feature Submission: 2026-08-31"
            # or plain "2026-04-01" or "Rolling — 2026-12-31"
            date_match = _re.search(r'(\d{4}-\d{2}-\d{2})', deadline_raw)
            if not date_match:
                continue
            try:
                dl = date.fromisoformat(date_match.group(1))
            except ValueError:
                continue

            # Skip deadlines that have already passed
            if dl < today:
                continue

            if dl <= cutoff:
                urgent.append((name, dl))

        if not urgent:
            return

        # Sort by soonest first
        urgent.sort(key=lambda x: x[1])

        timeline = summary.get("actionTimeline")
        if not isinstance(timeline, list):
            timeline = []
            summary["actionTimeline"] = timeline

        # Collect existing deadline text to avoid duplicates
        existing_text = " ".join(
            str(item.get("action", "")) + str(item.get("note", ""))
            for item in timeline if isinstance(item, dict)
        ).lower()

        for name, dl in urgent:
            days_left = (dl - today).days
            weeks_left = max(1, (days_left + 6) // 7)
            if name.lower() in existing_text:
                continue
            timeline.insert(0, {
                "action": f"URGENT: {name} deadline {dl.isoformat()} ({weeks_left} week{'s' if weeks_left != 1 else ''} away)",
                "deadline": dl.isoformat(),
                "note": f"Submit within {days_left} days — deadline is imminent.",
            })
            warnings.append(
                f"[actionTimeline] urgent deadline flagged: {name} "
                f"due {dl.isoformat()} ({days_left} days)"
            )

    @classmethod
    def _inject_section_explainers(cls, report: dict, datasets: dict) -> None:
        """Inject hardcoded plain-English section explainers per v3 spec Section 09."""
        # Gather template variables
        total_scenes = "N/A"
        ext_pct = "N/A"
        int_pct = "N/A"
        budget_currency = datasets.get("_budget_currency") or "GBP"

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
                "above-the-line spend (estimated 15% for tax credit programmes), then apply gross and net rates. "
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
                f"published scales and converted to {budget_currency} at the live exchange rate used "
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


def _format_millions(amount: float, symbol: str = "£") -> str | None:
    """Format a monetary amount as '[symbol]XXM' or '[symbol]XX.XM' for prose matching."""
    if amount < 100_000:
        return None
    m = amount / 1_000_000
    if m == int(m):
        return f"{symbol}{int(m)}M"
    return f"{symbol}{m:.1f}M"


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


def _budget_to_display(
    gbp_amount: float,
    territory_currency: str,
    budget_currency: str,
    budget_original_amount: float | None,
    budget_gbp: float | None,
    fx_rates_from_budget: dict[str, dict] | None,
) -> tuple[float, str, str | None]:
    """Convert a GBP-computed amount to the territory's display currency.

    When the territory's incentive currency matches the budget currency,
    scales from the original budget amount directly (avoiding GBP round-trip).
    Otherwise uses the budget→territory FX rate.

    Returns (display_amount, currency_symbol, fx_note_or_None).
    """
    symbol = _currency_symbol(territory_currency)

    if territory_currency == budget_currency:
        # Same currency — scale from original amount to avoid round-trip
        if budget_original_amount and budget_gbp and budget_gbp > 0:
            ratio = gbp_amount / budget_gbp
            return round(budget_original_amount * ratio, 0), symbol, None
        # Fallback if original amount not available
        return round(gbp_amount, 0), symbol, None

    # Different currency — convert via FX rate
    fx_rates = fx_rates_from_budget or {}
    fx_info = fx_rates.get(territory_currency)
    if fx_info and fx_info.get("rate"):
        rate = fx_info["rate"]
        # Convert from budget currency to territory currency
        if budget_original_amount and budget_gbp and budget_gbp > 0:
            ratio = gbp_amount / budget_gbp
            display = round(budget_original_amount * ratio * rate, 0)
        else:
            display = round(gbp_amount * rate, 0)
        fx_date = fx_info.get("rate_date", "")
        note = (
            f"Converted from {budget_currency} at rate {rate:.4f}"
            f"{' (' + fx_date + ')' if fx_date else ''}."
        )
        return display, symbol, note

    # No FX rate available — show in budget currency as fallback
    symbol = _currency_symbol(budget_currency)
    if budget_original_amount and budget_gbp and budget_gbp > 0:
        ratio = gbp_amount / budget_gbp
        return round(budget_original_amount * ratio, 0), symbol, None
    return round(gbp_amount, 0), symbol, None


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
    # Also strip "A$", "C$", "NZ$", "Ft " and ISO currency code prefixes
    raw = _re.sub(r'^(?:A\$|C\$|NZ\$|Ft\s*|Kč\s*)', '', raw)
    raw = _re.sub(r'^(?:HUF|USD|CAD|EUR|GBP|ZAR|NGN|AUD|NZD|CZK|MAD|RON|RSD)\s*', '', raw)

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
