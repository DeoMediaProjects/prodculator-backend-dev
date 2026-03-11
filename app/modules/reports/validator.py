"""Territory data integrity validator for production analysis reports.

Overrides hallucinated fields in AI-generated report data with ground-truth
values from the datasets that were injected into the prompt.  Runs post-
sanitisation so the structure is already clean.

Usage::

    from app.modules.reports.validator import ReportValidator

    sanitized, warnings = ReportValidator.validate(sanitized, datasets)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# Deviation tolerance before flagging rebate arithmetic (15 %)
_REBATE_TOLERANCE = 0.15

# Data freshness threshold — flag incentives older than this many days
_STALE_DAYS = 365

# Territories where rate_gross == 0 means the incentive is effectively inactive
_ZERO_RATE_TERRITORIES = {"Nigeria"}


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

        cls._patch_incentive_estimates(report, incentives_by_program, warnings)
        cls._patch_location_rankings(report, incentives_by_program, warnings)
        cls._patch_territory_deep_dives(report, incentives_by_program, warnings)
        cls._patch_executive_summary(report, incentives_by_program, warnings)
        cls._patch_crew_insights(report, datasets.get("crew_costs", []), warnings)

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
    ) -> None:
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

            # --- Nigeria guard: zero rate → no rebate ---
            if territory in _ZERO_RATE_TERRITORIES and _is_zero_rate(rate_gross, rate_net):
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

            # --- rebatePercent ---
            canonical_rate = _format_rate(best.get("rate_gross"), best.get("rate_net"))
            if canonical_rate and loc.get("rebatePercent") != canonical_rate:
                warnings.append(
                    f"[locationRankings] {territory}: "
                    f"rebatePercent overridden {loc.get('rebatePercent')!r} → {canonical_rate!r}"
                )
                loc["rebatePercent"] = canonical_rate

            # --- Nigeria / zero-rate guard ---
            if territory in _ZERO_RATE_TERRITORIES and _is_zero_rate(
                best.get("rate_gross"), best.get("rate_net")
            ):
                loc["incentiveStrength"] = 0
                loc["rebateAmount"] = "£0"

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

            # rebate string
            canonical_rate = _format_rate(best.get("rate_gross"), best.get("rate_net"))
            if canonical_rate:
                existing_rebate = dive.get("rebate", "")
                if existing_rebate and not existing_rebate.startswith(canonical_rate.rstrip("%")):
                    warnings.append(
                        f"[territoryDeepDives] {territory}: rebate rate component overridden"
                    )
                    # Patch only the rate prefix portion, keep any estimated amount
                    parts = existing_rebate.split("/", 1)
                    dive["rebate"] = (
                        f"{canonical_rate} / {parts[1].strip()}" if len(parts) == 2 else canonical_rate
                    )

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

        crew_by_territory: dict[str, list[dict]] = {}
        for row in crew_costs:
            t = row.get("territory") or ""
            crew_by_territory.setdefault(t, []).append(row)

        for insight in insights:
            if not isinstance(insight, dict):
                continue
            territory = insight.get("territory", "")
            rows = crew_by_territory.get(territory, [])
            if not rows:
                continue

            # Find a row with day_rate_gbp or week_rate_gbp
            for row in rows:
                day_gbp = row.get("day_rate_gbp")
                week_gbp = row.get("week_rate_gbp")
                if day_gbp or week_gbp:
                    fx_rate = row.get("fx_rate")
                    fx_date = row.get("fx_date")
                    insight["fxRate"] = fx_rate
                    insight["fxDate"] = fx_date
                    insight["currency"] = "GBP"
                    break


# ── Helpers ───────────────────────────────────────────────────────────────────


def _index_incentives(incentives: list[dict]) -> dict[str, dict]:
    """Index incentive rows by program_name (case-insensitive)."""
    result: dict[str, dict] = {}
    for row in incentives:
        if not isinstance(row, dict):
            continue
        name = row.get("program_name")
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
