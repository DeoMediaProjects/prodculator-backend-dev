"""Deterministic report skeleton builder.

Constructs the complete report structure from DB-authoritative data.
AI-narrative fields are set to ``None`` for later filling by the AI.

Usage::

    from app.modules.reports.builder import ReportBuilder

    skeleton = ReportBuilder(datasets, request_metadata).build()
"""
from __future__ import annotations

import calendar
import json as _json
import logging
import re as _re
from datetime import date, timedelta
from typing import Any

from app.core.territories import resolve_territory
from app.modules.reports.helpers import (
    STALE_DAYS,
    DEFAULT_ATL_PCT,
    TAX_CREDIT_RATE_TYPES,
    TERMINAL_LABELS,
    prog_name,
    index_incentives,
    index_incentives_by_territory,
    best_incentive,
    is_domestic_corp_only,
    to_float,
    is_zero_rate,
    format_rate,
    format_cap,
    format_money,
    currency_symbol,
    budget_to_display,
    parse_money_string,
)

logger = logging.getLogger(__name__)

# Genres that define a festival's primary content category.  When present on
# a festival, the production must also carry that genre — a broad secondary
# overlap (e.g. "Thriller" on a Horror+Thriller festival) is not sufficient.
# Prevents e.g. FrightFest appearing for music-drama thrillers.
_RESTRICTING_FEST_GENRES: frozenset[str] = frozenset({
    "horror", "documentary", "animation", "experimental", "lgbtq+",
})


# ── Weight tables (must match validator._WEIGHTS exactly) ─────────────────

SCORE_WEIGHTS = {
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

# Shoot duration thresholds (must match validator._LONG_SHOOT_THRESHOLDS)
_LONG_SHOOT_THRESHOLDS: dict[str, int] = {
    "TV Pilot": 12, "TV Series": 26, "Limited Series": 20,
    "Feature Film": 26, "Mini-Series": 20,
}
_LONG_SHOOT_DEFAULT = 26

# HETV constants (must match validator)
_HETV_TV_FORMATS = frozenset({"TV Series", "Limited Series", "Mini-Series", "Docuseries"})
_HETV_MIN_PER_HOUR_GBP = 1_000_000.0

# Deadline urgency window
_DEADLINE_URGENT_DAYS = 56

# Grant format filtering
_FEATURE_ONLY_PHRASES = (
    "feature film for theatrical", "feature film only",
    "theatrical feature", "theatrical release",
)
_SHORT_FILM_PHRASES = (
    "short film", "up to 15 min", "up to 20 min",
    "short-form", "shorts only",
)
_NON_FEATURE_FORMATS = {
    "TV Series", "Limited Series", "Mini-Series", "Docuseries",
    "Documentary", "Short Film", "Animation Series",
}

# Visa disclaimer
_VISA_DISCLAIMER = (
    "Visa and work permit requirements vary by nationality and production "
    "type — verify directly with the relevant embassy and film commission. "
    "Tourist entry rights differ from crew work permits."
)

# Operational requirement patterns
_OPERATIONAL_PATTERNS = [
    "production service company", "service company required",
    "local entity required", "must apply",
    "before principal photography", "minimum qualifying",
    "minimum spend",
]


class ReportBuilder:
    """Build a complete report skeleton from DB-authoritative data.

    AI-narrative fields are set to ``None``.  After the AI fills them,
    call ``merge_narratives()`` then ``compute_overall_scores()`` to
    finalise the report.
    """

    def __init__(
        self,
        datasets: dict,
        request_metadata: dict,
        script_analysis: Any = None,
        is_preview: bool = False,
    ):
        self.datasets = datasets
        self.request_metadata = request_metadata
        self.script_analysis = script_analysis
        self.is_preview = is_preview
        self.warnings: list[str] = []

        # Pre-index datasets
        self._incentives_by_program = index_incentives(
            datasets.get("incentives", [])
        )
        self._territory_incentives = index_incentives_by_territory(
            datasets.get("incentives", [])
        )
        self._territory_financials: dict = datasets.get("_territory_financials") or {}
        self._production_format: str | None = datasets.get("_production_format")
        self._production_priority: str = datasets.get("_production_priority", "full")
        self._currency_scores: dict | None = datasets.get("_currency_advantage_scores")

        # Budget info
        budget_gbp_data = datasets.get("_budget_gbp")
        self._budget_gbp: float | None = (
            budget_gbp_data.get("converted")
            if isinstance(budget_gbp_data, dict) else None
        )
        self._budget_currency: str = datasets.get("_budget_currency", "GBP")
        self._budget_original_amount: float | None = datasets.get("_budget_amount")
        self._fx_rates_from_budget: dict = datasets.get("_fx_rates_from_budget") or {}

    def build(self) -> dict:
        """Build the full report skeleton. AI-narrative fields are ``None``."""
        territories = self._select_territories()
        self._territory_names = territories

        report: dict = {
            # AI fills these top-level narrative fields
            "genre": None,
            "tone": None,
            "scale": None,
            "complexity": None,
            # Deterministic sections
            "locationRankings": self._build_location_rankings(territories),
            "incentiveEstimates": self._build_incentive_estimates(territories),
            "financialAnalysis": self._build_financial_analysis(territories),
            "executiveSummary": self._build_executive_summary(territories),
            "crewInsights": self._build_crew_insights(territories),
            "castInsights": self._build_cast_insights(territories),
            "comparables": self._build_comparables(),
            "weatherLogistics": self._build_weather_logistics(territories),
            "fundingOpportunities": self._build_funding_opportunities(),
            "territoryDeepDives": self._build_territory_deep_dives(
                territories[:3]
            ),
            "attributions": self._build_attributions(territories),
            # AI fills this
            "alternativeStrategy": None,
        }

        # Inject section explainers and scoring methodology
        self._inject_section_explainers(report)

        return report

    # ── Territory selection ─────────────────────────────────────────────────

    def _select_territories(self) -> list[str]:
        """Return territories to include in the report.

        When the user submitted specific territories, those are used exclusively.
        Otherwise falls back to all territories with financial data (capped at
        a reasonable limit).  Sub-territories that only have supplementary
        incentives (e.g. British Columbia PSTC) are excluded — their credits
        are shown as stacking options under the parent territory.
        """
        user_territories: list[str] = self.datasets.get("_user_territories") or []

        if user_territories:
            # Use user-submitted territories, preserving order.
            # Only include territories that have incentive data in the DB.
            # When a parent territory (e.g. "United States") has no national
            # incentive but has children with data (Georgia, New York, etc.),
            # expand to the best child territory so the parent isn't silently
            # dropped from the report.
            territories: list[str] = []
            for t in user_territories:
                if t in self._territory_incentives or t in self._territory_financials:
                    territories.append(t)
                else:
                    # Check for child territories with incentive data.
                    # The DB parent_territory may use an alias (e.g. "USA")
                    # rather than the canonical label ("United States"), so
                    # resolve both sides before comparing.
                    def _parent_matches(parent_raw: str | None, target: str) -> bool:
                        if not parent_raw:
                            return False
                        if parent_raw == target:
                            return True
                        resolved = resolve_territory(parent_raw)
                        return resolved is not None and resolved.label == target

                    children = [
                        child_t
                        for child_t, rows in self._territory_incentives.items()
                        if any(
                            _parent_matches(r.get("parent_territory"), t)
                            for r in rows
                        )
                    ]
                    if children:
                        # Pick the child with the best incentive rate
                        best_child = max(
                            children,
                            key=lambda c: max(
                                (to_float(r.get("rate_gross")) or 0)
                                for r in self._territory_incentives[c]
                            ),
                        )
                        if best_child not in territories:
                            territories.append(best_child)
        else:
            # Fallback: all territories with pre-computed financials
            territories = list(self._territory_financials.keys())
            for t in self._territory_incentives:
                if t and t not in territories:
                    territories.append(t)

        # Filter out territories whose only incentive is supplementary
        territories = [
            t for t in territories
            if not self._is_supplementary_only_territory(t)
        ]

        if self.is_preview:
            territories = territories[:3]

        return territories

    def _is_supplementary_only_territory(self, territory: str) -> bool:
        """True if every incentive row for this territory is supplementary."""
        rows = self._territory_incentives.get(territory, [])
        if not rows:
            return False
        return all(r.get("is_supplementary") for r in rows)

    # ── Location Rankings ──────────────────────────────────────────────────

    def _build_location_rankings(self, territories: list[str]) -> list[dict]:
        """Build locationRankings with all deterministic scores computed.

        AI-dependent fields (costEfficiency, crewDepth, infrastructure,
        reasoning, keyAdvantages) are set to None for later filling.
        """
        rankings: list[dict] = []

        for territory in territories:
            rows = self._territory_incentives.get(territory, [])
            if not rows:
                continue

            best = best_incentive(rows, self._production_format)
            effective_rate = format_rate(best.get("rate_gross"), best.get("rate_net"))

            # Compute deterministic scores
            strength = self._compute_incentive_strength(best)
            reliability_score, bankability_label = self._compute_reliability(best)
            currency_score = self._get_currency_score(territory)

            # Crew cost anchor: DB-driven seed for costEfficiency (AI may refine within ±15)
            cost_anchor = self._crew_rate_anchor(territory)

            loc: dict = {
                "name": territory,
                "rebatePercent": effective_rate or "N/A",
                "score": None,  # computed after AI fills 3 dimensions
                # DB-deterministic dimensions
                "incentiveStrength": strength,
                "incentiveReliability": reliability_score,
                "currencyAdvantage": currency_score,
                "bankabilityLabel": bankability_label,
                # Partially DB-seeded dimensions (AI fills crewDepth + infrastructure; costEfficiency anchored)
                "costEfficiency": cost_anchor,  # DB anchor; AI refines within ±15
                "crewDepth": None,
                "infrastructure": None,
                # Internal anchor for AI clamping — stripped before response
                "_costEfficiencyAnchor": cost_anchor,
                # AI-filled narratives
                "reasoning": None,
                "keyAdvantages": None,
                "keyRisks": [],  # DB risks populated below, AI appends
                # DB column — TRUE → "High (85%)", FALSE/NULL → "N/A"
                "culturalTestLikelihood": (
                    "High (85%)" if best.get("cultural_test_required") is True else "N/A"
                ),
            }

            # Payment speed from DB
            timeline_notes = best.get("payment_timeline_notes")
            if timeline_notes:
                loc["paymentSpeed"] = timeline_notes
            else:
                loc["paymentSpeed"] = "Data not available"

            # Zero-rate guard
            if is_zero_rate(best.get("rate_gross"), best.get("rate_net")):
                loc["incentiveStrength"] = 0

            # Staleness badge
            freshness = best.get("data_freshness_days")
            if isinstance(freshness, int) and freshness > STALE_DAYS:
                loc["keyRisks"].append(
                    "Incentive data may be outdated — verify before committing"
                )

            # Inject reliability warnings from warnings_json
            self._inject_reliability_warnings(loc, rows)

            # Inject operational requirements from eligibility_rules_json
            self._inject_operational_requirements(loc, rows)

            # Inject weather risk
            self._inject_weather_risk(loc, territory)

            # Inject cap-per-person note (placeholder for AI reasoning)
            self._inject_cap_per_person_risk(loc, best)

            rankings.append(loc)

        return rankings

    def _inject_reliability_warnings(self, loc: dict, rows: list[dict]) -> None:
        """Inject DB warnings_json and long-payment-timeline warnings into keyRisks."""
        key_risks = loc["keyRisks"]

        for db_row in rows:
            # Dataset warnings
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
                    w_lower = w.lower()
                    if any(w_lower[:40] in existing.lower()
                           for existing in key_risks if isinstance(existing, str)):
                        continue
                    key_risks.append(w)

            # Long payment timeline
            pay_max = to_float(db_row.get("payment_timeline_days_max"))
            if pay_max is not None and pay_max > 180:
                months_max = int(pay_max / 30)
                pay_min = to_float(db_row.get("payment_timeline_days_min"))
                months_min = int((pay_min or pay_max) / 30)
                reliability_msg = (
                    f"Payment timeline {months_min}-{months_max} months — "
                    f"this incentive should not be treated as investor-bankable. "
                    f"Budget cash flow independently."
                )
                if not any("investor-bankable" in r.lower() or "payment timeline" in r.lower()
                           for r in key_risks if isinstance(r, str)):
                    key_risks.insert(0, reliability_msg)

    def _inject_operational_requirements(self, loc: dict, rows: list[dict]) -> None:
        """Inject critical operational requirements from eligibility_rules_json."""
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

                already = any(
                    rule_lower[:30] in existing.lower()
                    for existing in key_risks if isinstance(existing, str)
                )
                if not already:
                    key_risks.append(rule_text)

    def _inject_weather_risk(self, loc: dict, territory: str) -> None:
        """Cross-reference weather data against shoot months, inject risks."""
        shoot_months = self.datasets.get("_shoot_months")
        weather_data = self.datasets.get("weather", [])
        ext_int_ratio = self.datasets.get("_ext_int_ratio")

        if not shoot_months or not weather_data:
            return

        weather_index: dict[tuple[str, int], dict] = {}
        for w in weather_data:
            key = (str(w.get("territory", "")).lower(), int(w.get("month") or 0))
            weather_index[key] = w

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
            return

        key_risks = loc["keyRisks"]
        month_names = [calendar.month_abbr[m] for m in high_risk_months]
        risk_msg = (
            f"Weather risk: shooting in {', '.join(month_names)} overlaps with "
            f"adverse conditions in {territory}"
        )
        if not any("weather risk" in r.lower() for r in key_risks if isinstance(r, str)):
            key_risks.insert(0, risk_msg)

        # Exterior exposure amplification
        if ext_int_ratio is not None and ext_int_ratio >= 0.7:
            exposure_msg = (
                f"{ext_int_ratio * 100:.0f}% exterior scenes — "
                f"weather delays will affect majority of schedule in {territory}"
            )
            if not any("exterior" in r.lower() for r in key_risks if isinstance(r, str)):
                key_risks.insert(0, exposure_msg)

        # Weather risk impact (used by score penalty)
        if ext_int_ratio is not None and ext_int_ratio >= 0.5:
            penalty = min(10, len(high_risk_months) * 3)
            loc["weatherRiskImpact"] = -penalty

    def _inject_cap_per_person_risk(self, loc: dict, best: dict) -> None:
        """Set ``perPersonCapNote`` for the location ranking.

        When a programme has a per-person ATL fee cap, ``perPersonCapNote`` is
        set to a human-readable note that the AI may reference in its reasoning.

        When there is NO cap, ``perPersonCapNote`` is explicitly set to ``None``
        (JSON null).  This structural absence is the AI's signal — the prompt
        rule is: "only reference per-person caps when perPersonCapNote is
        non-null in the skeleton."  An explicit null is stronger than a negative
        rule ("don't mention caps") because it prevents training-knowledge
        bleed-through (e.g. France CIC's €990K cap being applied to TRIP).
        """
        cap_per_person = to_float(best.get("cap_per_person"))
        if not cap_per_person or cap_per_person <= 0:
            loc["perPersonCapNote"] = None
            return
        currency = best.get("cap_per_person_currency") or best.get("currency") or "USD"
        symbol = currency_symbol(currency)
        loc["perPersonCapNote"] = (
            f"Per-person ATL fee cap: {symbol}{cap_per_person:,.0f}. "
            f"Applies to individual above-the-line fees (directors, lead cast, writers). "
            f"Model high-fee talent against this threshold before committing to territory."
        )

    # ── Incentive Estimates ────────────────────────────────────────────────

    def _build_incentive_estimates(self, territories: list[str]) -> list[dict]:
        """Build fully deterministic incentiveEstimates from DB data."""
        estimates: list[dict] = []
        present_by_territory: dict[str, set[str]] = {}

        for territory in territories:
            rows = self._territory_incentives.get(territory, [])
            if not rows:
                continue

            best = best_incentive(rows, self._production_format)
            program_name = prog_name(best)
            if not program_name:
                continue

            est = self._build_single_estimate(best, territory, program_name)
            if est:
                estimates.append(est)
                present_by_territory.setdefault(territory, set()).add(program_name)

        # Inject missing supplementary estimates
        self._inject_supplementary_estimates(
            estimates, present_by_territory
        )

        return estimates

    def _build_single_estimate(
        self, db_row: dict, territory: str, program_name: str,
    ) -> dict | None:
        """Build a single incentiveEstimate entry from a DB row."""
        rate_gross = db_row.get("rate_gross")
        rate_net = db_row.get("rate_net")

        # Supplementary programme → informational stub
        if db_row.get("is_supplementary"):
            return {
                "territory": territory,
                "program": program_name,
                "rate": format_rate(rate_gross, rate_net) or "See DB",
                "estimatedRebate": (
                    "Supplementary only — applies to qualifying specialist "
                    "expenditure (not total budget). Calculate on your estimated "
                    "spend proportion to get combined territory benefit."
                ),
                "bankabilityLabel": "INFORMATIONAL",
                "paymentSpeed": db_row.get("payment_timeline_notes") or "See primary programme",
                "dataSource": db_row.get("source_name") or "Prodculator admin database",
                "lastUpdated": str(db_row.get("last_verified_at") or db_row.get("last_updated") or ""),
            }

        # Format applicability guard
        if self._production_format:
            af = db_row.get("applicable_formats")
            if af is not None:
                if isinstance(af, str):
                    try:
                        af = _json.loads(af)
                    except (ValueError, TypeError):
                        af = None
                if isinstance(af, list) and af:
                    if not any(f.lower() == self._production_format.lower() for f in af):
                        return {
                            "territory": territory,
                            "program": program_name,
                            "bankabilityLabel": "NOT APPLICABLE",
                            "estimatedRebate": (
                                f"Not applicable — programme restricted to {', '.join(af)}"
                            ),
                            "eligibilityNote": (
                                f"This programme is only available for "
                                f"{', '.join(af)} productions. "
                                f"It does not apply to {self._production_format}."
                            ),
                        }

        est: dict = {
            "territory": territory,
            "program": program_name,
        }

        # Rate
        canonical_rate = format_rate(rate_gross, rate_net)
        est["rate"] = canonical_rate or "N/A"

        # Zero-rate guard
        if is_zero_rate(rate_gross, rate_net):
            est["incentiveStrength"] = 0
            est["eligibilityNote"] = (
                "Rate not available in dataset — no financial incentive calculated. "
                "Verify programme status with the relevant film commission."
            )
            est["estimatedRebate"] = "N/A"
        else:
            # Estimated rebate from pre-computed financials
            tf = self._territory_financials.get(territory)
            if tf:
                est["estimatedRebate"] = tf.get("gross_rebate", "See programme terms")
            else:
                est["estimatedRebate"] = "See programme terms"

        # Cap display — three possible sources, checked in priority order:
        # 1. rebate_cap_amount — hard per-project rebate ceiling (e.g. SA R25M)
        # 2. DB cap text label (e.g. "Budget cap £23.5M") — carries semantic meaning
        # 3. cap_amount — budget threshold formatted automatically
        rebate_cap = db_row.get("rebate_cap_amount")
        rebate_cap_cur = db_row.get("rebate_cap_currency") or db_row.get("cap_currency") or "GBP"
        if rebate_cap is not None and to_float(rebate_cap):
            formatted_rebate_cap = format_cap(rebate_cap, rebate_cap_cur)
            if formatted_rebate_cap:
                est["cap"] = f"{formatted_rebate_cap} per project"
        if "cap" not in est:
            db_cap_label = (db_row.get("cap") or "").strip()
            # Skip vague/outdated labels like "No formal cap"
            if db_cap_label and "no formal cap" not in db_cap_label.lower():
                est["cap"] = db_cap_label
        if "cap" not in est:
            cap_amount = db_row.get("cap_amount")
            cap_currency = db_row.get("cap_currency") or "GBP"
            canonical_cap = format_cap(cap_amount, cap_currency)
            if canonical_cap is not None:
                est["cap"] = canonical_cap

        # Payment timeline
        timeline_notes = db_row.get("payment_timeline_notes")
        est["paymentSpeed"] = timeline_notes or "Data not available"

        # Qualifying spend
        qs_min = db_row.get("qualifying_spend_min")
        qs_currency = db_row.get("qualifying_spend_currency") or "GBP"
        if qs_min is not None and qs_min > 0:
            est["qualifyingSpend"] = format_money(qs_min, qs_currency)
        else:
            est["qualifyingSpend"] = "No minimum threshold"

        # Eligibility rules
        rules_json = db_row.get("eligibility_rules_json")
        if isinstance(rules_json, str):
            try:
                rules_json = _json.loads(rules_json)
            except (ValueError, TypeError):
                rules_json = None
        if isinstance(rules_json, list) and rules_json:
            est["requirements"] = [
                r["rule"] if isinstance(r, dict) else str(r) for r in rules_json
            ]

        # Eligibility notes (free-text, trimmed to 240 chars by prompt layer)
        notes = db_row.get("eligibility_notes")
        if notes and isinstance(notes, str):
            reqs = est.setdefault("requirements", [])
            if isinstance(reqs, list) and not any(
                notes.lower()[:30] in r.lower() for r in reqs if isinstance(r, str)
            ):
                reqs.append(notes)

        # Atomic first-class skeleton keys — not subject to string trimming.
        # These carry structured facts that the AI must act on precisely.
        net_rate_pct = to_float(db_row.get("net_rate_pct"))
        if net_rate_pct is not None:
            est["netRatePct"] = net_rate_pct

        payee_note = (db_row.get("payee_note") or "").strip()
        if payee_note:
            est["payeeNote"] = payee_note

        filing_note = (db_row.get("filing_note") or "").strip()
        if filing_note:
            est["filingNote"] = filing_note

        # Source attribution
        source_name = db_row.get("source_name")
        est["dataSource"] = source_name or "Prodculator admin database"

        # Staleness
        freshness = db_row.get("data_freshness_days")
        if isinstance(freshness, int) and freshness > STALE_DAYS:
            est["stalenessWarning"] = "Incentive data may be outdated — verify before committing"

        # Last updated
        lv = db_row.get("last_verified_at") or db_row.get("last_updated")
        if lv:
            est["lastUpdated"] = str(lv)

        # Stacking
        self._apply_stacking(est, db_row)

        # Eligibility status
        self._apply_eligibility(est, db_row)

        # Bankability label (skip if already set as terminal)
        if est.get("bankabilityLabel") not in TERMINAL_LABELS:
            reliability = to_float(db_row.get("payment_reliability"))
            timeline_max = to_float(db_row.get("payment_timeline_days_max"))
            est["bankabilityLabel"] = _compute_bankability_label(reliability, timeline_max)

        # HETV threshold check
        self._apply_hetv_check(est, db_row)

        return est

    def _apply_stacking(self, est: dict, db_row: dict) -> None:
        """Apply stacking logic from DB stackable_with.

        Includes the rate of each stackable programme so the AI uses DB
        values instead of hallucinating stale rates from training data.
        """
        db_scope = db_row.get("scope")
        if db_scope:
            est["scope"] = db_scope

        db_parent = db_row.get("parent_territory")
        if db_parent:
            est["parentTerritory"] = db_parent

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
            if db_stackable:
                est["stackableWith"] = db_stackable
                # Look up rates for each stackable programme so the AI
                # references DB values, not its own (potentially stale) data.
                stacking_rates: list[dict] = []
                for prog_name in db_stackable:
                    prog_row = self._incentives_by_program.get(prog_name) or self._incentives_by_program.get(prog_name.lower())
                    if prog_row:
                        rate_g = to_float(prog_row.get("rate_gross"))
                        rate_str = prog_row.get("rate") or ""
                        entry: dict = {"program": prog_name}
                        if rate_g:
                            entry["rate_gross"] = rate_g
                        if rate_str:
                            entry["rate"] = rate_str
                        # Exclude domestic-corps-only programmes — not available to
                        # foreign productions (e.g. BC FIBC: Canadian-controlled only).
                        if is_domestic_corp_only(prog_row):
                            continue
                        stacking_rates.append(entry)
                if stacking_rates:
                    est["_stackingRates"] = stacking_rates

    def _apply_eligibility(self, est: dict, db_row: dict) -> None:
        """Compute eligibility status from nationality_requirements."""
        producer_country = self.datasets.get("_producer_country")

        nat_reqs_raw = db_row.get("nationality_requirements")
        if not nat_reqs_raw:
            if not est.get("eligibilityStatus"):
                est["eligibilityStatus"] = "qualified"
            return

        try:
            nat_reqs: list[str] = (
                _json.loads(nat_reqs_raw)
                if isinstance(nat_reqs_raw, str)
                else list(nat_reqs_raw)
            )
        except (ValueError, TypeError):
            nat_reqs = []

        if not nat_reqs:
            return

        program_name = est.get("program", "")
        territory = db_row.get("territory", "")

        if producer_country:
            qualifies = producer_country.upper() in [n.upper() for n in nat_reqs]
            if qualifies:
                if not est.get("eligibilityStatus"):
                    est["eligibilityStatus"] = "qualified"
                    est.setdefault(
                        "eligibilityNote",
                        f"{producer_country} registered entity qualifies directly.",
                    )
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
        else:
            # No producer country — add assumption note
            reqs = est.setdefault("requirements", [])
            assumption = (
                f"Eligibility assumes a qualifying {territory} entity — "
                f"verify company jurisdiction before committing."
            )
            if not any("eligibility assumes" in r.lower() for r in reqs if isinstance(r, str)):
                reqs.append(assumption)

    def _apply_hetv_check(self, est: dict, db_row: dict) -> None:
        """Verify UK AVEC HETV minimum spend of £1M per broadcast hour."""
        if self._production_format not in _HETV_TV_FORMATS:
            return

        program_name = (est.get("program") or "").lower()
        territory_name = (est.get("territory") or "").lower()

        is_uk_avec = (
            any(kw in program_name for kw in ("avec", "audio-visual expenditure"))
            and any(
                t in territory_name
                for t in ("united kingdom", "uk", "england", "scotland",
                          "wales", "northern ireland")
            )
        )
        if not is_uk_avec:
            return

        reqs = est.setdefault("requirements", [])
        if any("hetv threshold" in str(r).lower() for r in reqs):
            return

        total_episodes = self.datasets.get("_total_episodes")
        episode_runtime = self.datasets.get("_episode_runtime_minutes")

        if total_episodes and episode_runtime and self._budget_gbp:
            total_hours = (total_episodes * episode_runtime) / 60.0
            per_hour = self._budget_gbp / total_hours if total_hours > 0 else 0.0
            if per_hour >= _HETV_MIN_PER_HOUR_GBP:
                note = (
                    f"HETV threshold: PASS — "
                    f"£{self._budget_gbp / 1_000_000:.1f}M budget across "
                    f"{total_episodes} × {episode_runtime}min episodes "
                    f"= £{per_hour / 1_000_000:.2f}M/hour "
                    f"(minimum £1M/hour required)"
                )
            else:
                note = (
                    f"HETV threshold: FAIL — "
                    f"£{self._budget_gbp / 1_000_000:.1f}M budget across "
                    f"{total_episodes} × {episode_runtime}min episodes "
                    f"= £{per_hour / 1_000:,.0f}K/hour — "
                    f"BELOW the required £1M/hour minimum. "
                    f"This production does not qualify for UK AVEC HETV strand."
                )
                est["bankabilityLabel"] = "NOT APPLICABLE"
        else:
            note = (
                "HETV THRESHOLD NOT CONFIRMED: UK AVEC HETV strand requires a minimum "
                "of £1M per broadcast hour (confirmed HMRC requirement, Source: HMRC "
                "CREC023000 / BFI). Episode count and runtime were not provided, so "
                "compliance with this threshold cannot be calculated. Provide episode "
                "count and runtime to confirm eligibility before including in investor "
                "documents."
            )

        reqs.append(note)

    def _inject_supplementary_estimates(
        self,
        estimates: list[dict],
        present_by_territory: dict[str, set[str]],
    ) -> None:
        """Inject missing supplementary programme stubs."""
        territory_rows: dict[str, list[dict]] = {}
        seen_ids: set[int] = set()
        for row in self._incentives_by_program.values():
            # index_incentives stores both exact-name and lowercase keys,
            # so the same row dict appears twice.  Deduplicate by id().
            if id(row) in seen_ids:
                continue
            seen_ids.add(id(row))
            t = (row.get("territory") or "").strip()
            if t:
                territory_rows.setdefault(t, []).append(row)

        for territory, present_progs in present_by_territory.items():
            for row in territory_rows.get(territory, []):
                if not row.get("is_supplementary"):
                    continue
                prog = (row.get("program") or "").strip()
                if not prog or prog in present_progs:
                    continue
                # Skip domestic-corps-only supplementary programmes (e.g. BC FIBC:
                # Canadian-controlled only — not available to foreign productions).
                if is_domestic_corp_only(row):
                    continue

                primary = next(
                    (p for p in present_progs if territory in territory_rows
                     and not any(
                         r.get("program") == p and r.get("is_supplementary")
                         for r in territory_rows.get(territory, [])
                     )),
                    "the primary incentive",
                )
                rate_gross = row.get("rate_gross")
                rate_net = row.get("rate_net")
                elig_notes = (row.get("eligibility_notes") or "").strip()

                # Detect mutual exclusivity between this supplementary programme
                # and the primary programme being used.  The DB stores constraints
                # like "CANNOT be combined with IFTC" in eligibility_notes — extract
                # them and check whether any exclusion token matches the primary name.
                is_mutually_exclusive = False
                if elig_notes and primary and primary != "the primary incentive":
                    import re as _re_stacking
                    exclusions = _re_stacking.findall(
                        r"cannot be combined with ([^.;]+)",
                        elig_notes,
                        flags=_re_stacking.IGNORECASE,
                    )
                    primary_tokens = set(
                        _re_stacking.findall(r'\b[A-Za-z]{3,}\b', primary)
                    )
                    for excl in exclusions:
                        excl_tokens = set(_re_stacking.findall(r'\b[A-Za-z]{3,}\b', excl))
                        if primary_tokens & excl_tokens:
                            is_mutually_exclusive = True
                            break

                if is_mutually_exclusive:
                    stacking_note = (
                        f"MUTUAL EXCLUSIVITY: {prog} CANNOT be combined with {primary}. "
                        f"These are alternative programmes — a production must choose one "
                        f"or the other, not both. Model both paths to determine the better "
                        f"outcome before committing."
                    )
                else:
                    stacking_note = (
                        f"SUPPLEMENTARY: {prog} stacks ON TOP of {primary}. "
                        f"Applies only to qualifying specialist expenditure "
                        f"(not total budget). Calculate on your estimated VFX/specialist "
                        f"spend proportion to get combined territory benefit."
                    )

                stub: dict = {
                    "territory": territory,
                    "program": prog,
                    "rate": format_rate(rate_gross, rate_net) or "See DB",
                    "estimatedRebate": (
                        "Qualifying VFX/specialist spend only — "
                        "see stackingNote for calculation basis"
                    ),
                    "stackingNote": stacking_note,
                    "bankabilityLabel": "INFORMATIONAL",
                    "paymentSpeed": row.get("payment_timeline_notes") or "See primary programme",
                    "dataSource": row.get("source_name") or "Prodculator admin database",
                }
                if elig_notes:
                    stub["eligibilityNote"] = elig_notes
                lv = row.get("last_verified_at") or row.get("last_updated")
                if lv:
                    stub["lastUpdated"] = str(lv)
                estimates.append(stub)

    # ── Financial Analysis ─────────────────────────────────────────────────

    def _build_financial_analysis(self, territories: list[str]) -> dict:
        """Build financialAnalysis from pre-computed territory_financials."""
        budget_scenarios: list[dict] = []

        for territory in territories:
            tf = self._territory_financials.get(territory)
            if not tf:
                continue

            scenario: dict = {
                "territory": territory,
                "totalBudget": tf.get("total_budget"),
                "qualifyingSpendPct": tf.get("qualifying_spend_pct"),
                "qualifyingSpend": tf.get("qualifying_spend"),
                "netQualifyingSpend": tf.get("net_qualifying_spend"),
                "rateGross": tf.get("rate_gross"),
                "rateNet": tf.get("rate_net"),
                "grossRebate": tf.get("gross_rebate"),
                "netRebate": tf.get("net_rebate"),
                "netBudget": tf.get("net_budget"),
                "programme": tf.get("programme"),
            }

            # ATL deduction
            atl_str = tf.get("atl_deduction")
            if atl_str:
                neg_atl = f"-{atl_str}" if not atl_str.startswith("-") else atl_str
                scenario["atlDeduction"] = neg_atl

            atl_pct = tf.get("atl_pct")
            if atl_pct:
                scenario["atlDeductionPct"] = atl_pct

            # Notes (ATL note, rebate cap, qualifying spend type)
            notes_parts: list[str] = []
            for note_key in ("atl_deduction_note", "rebate_cap_note", "qualifying_spend_note"):
                note = tf.get(note_key)
                if note:
                    notes_parts.append(note)
            if notes_parts:
                scenario["notes"] = " ".join(notes_parts)

            budget_scenarios.append(scenario)

        # Build crew cost comparison (filtered to ranked territories)
        crew_comparison = self._build_crew_cost_comparison(territories)

        return {
            "budgetScenarios": budget_scenarios,
            "crewCostComparison": crew_comparison,
        }

    def _build_crew_cost_comparison(self, territories: list[str]) -> list[dict]:
        """Build crewCostComparison from pre-computed crew rates."""
        territory_set = set(territories)
        role_data: dict[str, dict[str, str]] = {}

        for territory in territories:
            tf = self._territory_financials.get(territory)
            if not tf:
                continue
            crew_rates = tf.get("crew_rates", {})
            for role, rate_text in crew_rates.items():
                role_data.setdefault(role, {})[territory] = rate_text

        result: list[dict] = []
        for role, territory_rates in role_data.items():
            # Filter to only ranked territories
            filtered = {t: r for t, r in territory_rates.items() if t in territory_set}
            if filtered:
                result.append({"role": role, "territories": filtered})

        return result

    # ── Executive Summary ──────────────────────────────────────────────────

    def _build_executive_summary(self, territories: list[str]) -> dict:
        """Build executiveSummary shell. keyInsights filled by AI."""
        summary: dict = {
            "keyInsights": None,  # AI fills
            "recommendedTerritory": territories[0] if territories else None,
            "recommendedTerritoryScore": None,  # set after score computation
        }

        # Payment speed for top territory
        if territories:
            top = territories[0]
            rows = self._territory_incentives.get(top, [])
            if rows:
                best = best_incentive(rows, self._production_format)
                timeline_notes = best.get("payment_timeline_notes")
                summary["recommendedTerritoryPaymentSpeed"] = (
                    timeline_notes or "Data not available"
                )

            # Pre-computed financial headline
            tf = self._territory_financials.get(top)
            if tf:
                summary["recommendedTerritoryRebate"] = tf.get("gross_rebate")
                summary["headlineNetBudget"] = tf.get("headline_net_budget")

        # Shoot days (authoritative from user input)
        shoot_weeks = self.datasets.get("_shoot_weeks")
        if shoot_weeks and shoot_weeks > 0:
            summary["shootDays"] = shoot_weeks

        # Production format
        if self._production_format:
            summary["format"] = self._production_format

        # Budget display
        if self._budget_original_amount:
            sym = currency_symbol(self._budget_currency)
            summary["budget"] = f"{sym}{self._budget_original_amount:,.0f}"

        # Shoot duration context flag
        self._inject_shoot_duration_flag(summary)

        # Deadline proximity
        self._inject_deadline_flags(summary)

        return summary

    def _inject_shoot_duration_flag(self, summary: dict) -> None:
        """Add keyFlag for unusually long shoot durations."""
        shoot_weeks = summary.get("shootDays")
        if not isinstance(shoot_weeks, (int, float)) or shoot_weeks <= 0:
            return

        fmt = (self._production_format or "").strip()
        threshold = _LONG_SHOOT_THRESHOLDS.get(fmt, _LONG_SHOOT_DEFAULT)

        if shoot_weeks < threshold:
            return

        flag = (
            f"Extended shoot timeline: {int(shoot_weeks)} weeks. "
            f"This is a significant schedule for "
            f"{'a ' + fmt.lower() if fmt else 'this format'} "
            f"and may require phased production, multiple unit scheduling, "
            f"or a detailed schedule breakdown for completion bond assessment."
        )

        key_flags = summary.setdefault("keyFlags", [])
        if not any("shoot timeline" in f.lower() or "shooting days" in f.lower() for f in key_flags):
            key_flags.append(flag)

    def _inject_deadline_flags(self, summary: dict) -> None:
        """Flag imminent funding/festival deadlines."""
        opportunities = self.datasets.get("grants", [])
        festivals = self.datasets.get("festivals", [])
        all_opps = list(opportunities) + list(festivals)

        if not all_opps:
            return

        today = date.today()
        cutoff = today + timedelta(days=_DEADLINE_URGENT_DAYS)
        urgent: list[tuple[str, date]] = []

        for opp in all_opps:
            if not isinstance(opp, dict):
                continue
            name = opp.get("title") or opp.get("name") or ""
            deadline_raw = opp.get("deadline") or opp.get("next_deadline") or ""
            date_match = _re.search(r'(\d{4}-\d{2}-\d{2})', str(deadline_raw))
            if not date_match:
                continue
            try:
                dl = date.fromisoformat(date_match.group(1))
            except ValueError:
                continue
            if dl < today:
                continue
            if dl <= cutoff:
                urgent.append((name, dl))

        if not urgent:
            return

        urgent.sort(key=lambda x: x[1])
        timeline = summary.setdefault("actionTimeline", [])

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

    # ── Crew Insights ──────────────────────────────────────────────────────

    def _build_crew_insights(self, territories: list[str]) -> list[dict]:
        """Build crewInsights with FX metadata. Narrative fields for AI."""
        crew_costs = self.datasets.get("crew_costs", [])
        if not crew_costs:
            return []

        from app.modules.reports.service import _TERRITORY_TO_ISO, _ISO_TO_TERRITORY

        crew_by_territory: dict[str, list[dict]] = {}
        for row in crew_costs:
            country = row.get("country") or ""
            territory = row.get("territory") or ""
            if country:
                crew_by_territory.setdefault(country, []).append(row)
            if territory:
                crew_by_territory.setdefault(territory, []).append(row)

        insights: list[dict] = []
        for territory in territories:
            rows = crew_by_territory.get(territory, [])
            if not rows:
                iso = _TERRITORY_TO_ISO.get(territory, "")
                rows = crew_by_territory.get(iso, [])
            if not rows:
                full = _ISO_TO_TERRITORY.get(territory, "")
                rows = crew_by_territory.get(full, [])
            if not rows:
                continue

            insight: dict = {
                "territory": territory,
                "availability": None,  # AI fills
                "specialties": None,  # AI fills
                "tradeoff": None,  # AI fills
            }

            # FX metadata and cost comparison
            rates_gbp: list[float] = []
            for row in rows:
                union = row.get("union_rate_gbp")
                non_union = row.get("non_union_rate_gbp")
                if union or non_union:
                    if not insight.get("fxRate"):
                        insight["fxRate"] = row.get("fx_rate")
                        insight["fxDate"] = row.get("fx_date")
                        insight["currency"] = self._budget_currency
                    rate = float(union or non_union or 0)
                    if rate > 0:
                        rates_gbp.append(rate)

            # Cost vs budget currency — relative indicator
            if rates_gbp:
                avg_rate = sum(rates_gbp) / len(rates_gbp)
                sym = currency_symbol(self._budget_currency)
                # Benchmark: UK average HOD ~£900/day
                if avg_rate < 500:
                    insight["costVsUSD"] = f"{sym}{avg_rate:,.0f}/day avg — below market"
                elif avg_rate < 1000:
                    insight["costVsUSD"] = f"{sym}{avg_rate:,.0f}/day avg — at market"
                else:
                    insight["costVsUSD"] = f"{sym}{avg_rate:,.0f}/day avg — above market"
            else:
                insight["costVsUSD"] = "Data not available"

            # Quality rating: derive from crew data density (more data = more established industry)
            insight["qualityRating"] = min(5, max(1, len(rows) // 3 + 1))

            insights.append(insight)

        return insights

    # ── Cast Insights ──────────────────────────────────────────────────────

    def _build_cast_insights(self, territories: list[str]) -> list[dict]:
        """Build castInsights with FX metadata."""
        cast_costs = self.datasets.get("cast_costs", [])
        if not cast_costs:
            return []

        from app.modules.reports.service import _TERRITORY_TO_ISO, _ISO_TO_TERRITORY

        cast_by_territory: dict[str, list[dict]] = {}
        for row in cast_costs:
            country = row.get("country") or ""
            territory = row.get("territory") or ""
            if country:
                cast_by_territory.setdefault(country, []).append(row)
            if territory:
                cast_by_territory.setdefault(territory, []).append(row)

        insights: list[dict] = []
        for territory in territories:
            rows = cast_by_territory.get(territory, [])
            if not rows:
                iso = _TERRITORY_TO_ISO.get(territory, "")
                rows = cast_by_territory.get(iso, [])
            if not rows:
                full = _ISO_TO_TERRITORY.get(territory, "")
                rows = cast_by_territory.get(full, [])
            if not rows:
                continue

            insight: dict = {"territory": territory}

            for row in rows:
                if row.get("union_rate_gbp") or row.get("non_union_rate_gbp"):
                    insight["fxRate"] = row.get("fx_rate")
                    insight["fxDate"] = row.get("fx_date")
                    break

            insights.append(insight)

        return insights

    # ── Comparables ────────────────────────────────────────────────────────

    def _build_comparables(self) -> list[dict]:
        """Build comparables from dataset, filtered and capped.

        Selection priority:
        1. Territory match (same territory as one of the ranked territories)
        2. Genre match (shares at least one genre with the production)
        3. Budget proximity (within 0.2x–5x of the production budget)

        Maximum 10 comparables in the final report.
        """
        comparables = self.datasets.get("comparables", [])
        if not comparables:
            return []

        # Production context for scoring
        territories_set = {t.lower() for t in self._territory_names}
        prod_genres_raw = self.request_metadata.get("genre") or []
        if isinstance(prod_genres_raw, str):
            prod_genres_raw = [prod_genres_raw]
        prod_genres = {g.lower().strip() for g in prod_genres_raw if g}

        scored: list[tuple[float, dict]] = []
        for row in comparables:
            if not isinstance(row, dict):
                continue
            title = (row.get("title") or "").strip()
            if not title:
                continue

            comp_territory = (row.get("primary_territory") or "").strip()
            comp_genre_raw = row.get("genre") or ""
            if isinstance(comp_genre_raw, list):
                comp_genres = {g.lower().strip() for g in comp_genre_raw if g}
            elif isinstance(comp_genre_raw, str):
                comp_genres = {g.lower().strip() for g in comp_genre_raw.split(",") if g.strip()}
            else:
                comp_genres = set()
            budget_range = row.get("budget_range") or ""

            # Relevance scoring
            score = 0.0

            # Territory match (+3)
            if comp_territory and comp_territory.lower() in territories_set:
                score += 3.0

            # Genre match (+2 per shared genre, max +4)
            genre_overlap = prod_genres & comp_genres
            score += min(len(genre_overlap) * 2.0, 4.0)

            # Budget proximity (+2 if within range, -1 if far)
            if self._budget_gbp and _re.match(r'^[~≈]?\s*[£$€]', budget_range.strip()):
                comp_budget = parse_money_string(budget_range)
                if comp_budget and self._budget_gbp > 0:
                    ratio = comp_budget / self._budget_gbp
                    if 0.2 <= ratio <= 5.0:
                        score += 2.0
                    else:
                        score -= 1.0

            # Year — omit if empty so template can conditionally render
            comp_year = row.get("year") or row.get("release_year") or ""
            if comp_year:
                comp_year = str(comp_year).strip()

            # Source — omit if empty/N/A
            comp_source = (row.get("source") or "").strip()
            if comp_source.lower() in ("", "n/a", "none"):
                comp_source = ""

            # Genre — ensure string for template rendering
            if isinstance(comp_genre_raw, list):
                genre_display = ", ".join(str(g) for g in comp_genre_raw if g)
            else:
                genre_display = str(comp_genre_raw) if comp_genre_raw else ""

            comp_dict: dict = {
                "title": title,
                "year": comp_year,
                "location": comp_territory,
                "budgetRange": budget_range,
                "genre": genre_display,
                "source": comp_source,
                "relevanceDescription": None,  # AI fills
            }

            # Budget gap caveat for AI prompt context
            if self._budget_gbp and _re.match(r'^[~≈]?\s*£', budget_range.strip()):
                comp_budget = parse_money_string(budget_range)
                if comp_budget and self._budget_gbp > 0:
                    ratio = comp_budget / self._budget_gbp
                    if ratio > 5.0 or ratio < 0.2:
                        comp_dict["_budgetGapFlag"] = (
                            "significantly larger" if ratio > 5 else "significantly smaller"
                        )

            scored.append((score, comp_dict))

        # Sort by relevance score descending, take top 10
        scored.sort(key=lambda x: x[0], reverse=True)
        return [comp for _, comp in scored[:10]]

    # ── Weather Logistics ──────────────────────────────────────────────────

    def _build_weather_logistics(self, territories: list[str]) -> list[dict]:
        """Build weatherLogistics from DB data. Narrative fields for AI."""
        weather_data = self.datasets.get("weather", [])
        visa_requirements = self.datasets.get("_visa_requirements")
        shoot_months = self.datasets.get("_shoot_months") or []

        # Index weather by (territory_lower, month)
        weather_index: dict[tuple[str, int], dict] = {}
        for w in weather_data:
            key = (str(w.get("territory", "")).lower(), int(w.get("month") or 0))
            weather_index[key] = w

        results: list[dict] = []
        for territory in territories:
            entry: dict = {
                "territory": territory,
                "infrastructure": None,  # AI fills
                "seasonalConsiderations": None,  # AI fills
            }

            # Compute weather risk from shoot months
            territory_lower = territory.lower()
            high_risk_count = 0
            total_checked = 0
            best_months: list[str] = []

            if shoot_months:
                for month in shoot_months:
                    w = weather_index.get((territory_lower, month))
                    if not w:
                        continue
                    total_checked += 1
                    storm = str(w.get("storm_risk") or "").lower()
                    rainfall = float(w.get("avg_rainfall_mm") or 0)
                    if storm == "high" or rainfall > 100:
                        high_risk_count += 1

            # Determine best months — months with low rainfall and no storm risk
            for m in range(1, 13):
                w = weather_index.get((territory_lower, m))
                if not w:
                    continue
                storm = str(w.get("storm_risk") or "").lower()
                rainfall = float(w.get("avg_rainfall_mm") or 0)
                if storm in ("none", "low", "") and rainfall < 80:
                    best_months.append(calendar.month_name[m])

            entry["bestMonths"] = best_months[:4] if best_months else ["N/A"]

            # Weather risk level
            if total_checked == 0:
                entry["weatherRisk"] = "Low"
            elif high_risk_count == 0:
                entry["weatherRisk"] = "Low"
            elif high_risk_count <= total_checked * 0.3:
                entry["weatherRisk"] = "Medium"
            else:
                entry["weatherRisk"] = "High"

            # Visa info from DB
            if visa_requirements and territory:
                db_entry = visa_requirements.get(territory)
                if db_entry:
                    entry["travelVisa"] = db_entry.get("notes") or _VISA_DISCLAIMER
                else:
                    entry["travelVisa"] = _VISA_DISCLAIMER
            else:
                entry["travelVisa"] = _VISA_DISCLAIMER

            results.append(entry)

        return results

    # ── Funding Opportunities ──────────────────────────────────────────────

    def _build_funding_opportunities(self) -> list[dict]:
        """Build fundingOpportunities from grants + festivals datasets.

        Only includes entries whose territory matches one of the selected
        territories (self._territory_names).

        Grants whose eligibility text indicates they require a domestic
        delegate producer (e.g. CNC Aide Sélective) are excluded when the
        production's base country differs from the grant territory — a US
        production using a French PSC for the TRIP rebate is NOT the French
        delegate producer and cannot access such programmes.
        """
        grants = self.datasets.get("grants", [])
        festivals = self.datasets.get("festivals", [])
        selected = {t.lower() for t in self._territory_names}
        base_country = (
            self.request_metadata.get("country")
            or self.request_metadata.get("base_country")
            or ""
        ).strip()

        # Keywords (lowercase) that signal a grant requires a domestic delegate
        # producer.  Matched case-insensitively against eligibility text.
        _DOMESTIC_PRODUCER_MARKERS = (
            "delegate producer",
            "délégué",
            "delegue",
            "not accessible to foreign",
            "french majority production",
            "avance sur recettes",
        )

        opportunities: list[dict] = []

        for grant in grants:
            if not isinstance(grant, dict):
                continue
            grant_territory = (grant.get("territory") or "").strip()
            if grant_territory.lower() not in selected:
                continue
            grant_name = (grant.get("title") or grant.get("name") or "").strip()
            if not grant_name:
                continue

            # Check if grant requires a domestic delegate producer and the
            # production is foreign-initiated (base country ≠ grant territory).
            if base_country and base_country.lower() != grant_territory.lower():
                eligibility_raw = grant.get("eligibility") or ""
                if isinstance(eligibility_raw, str):
                    try:
                        elig_list = _json.loads(eligibility_raw)
                        eligibility_text = " ".join(str(e) for e in elig_list)
                    except (ValueError, TypeError):
                        eligibility_text = eligibility_raw
                elif isinstance(eligibility_raw, list):
                    eligibility_text = " ".join(str(e) for e in eligibility_raw)
                else:
                    eligibility_text = str(eligibility_raw)

                eligibility_lower = eligibility_text.lower()
                if any(marker in eligibility_lower for marker in _DOMESTIC_PRODUCER_MARKERS):
                    continue  # Skip — not accessible to this production
            opp: dict = {
                "name": grant_name,
                "type": "Fund",
                "territory": grant.get("territory") or "",
                "deadline": (
                    grant.get("application_deadline")
                    or grant.get("next_deadline")
                    or ""
                ),
                "notes": (
                    grant.get("max_amount")
                    or grant.get("amount_description")
                    or ""
                ),
            }

            # Grant label — ensure "Up to" prefix
            notes = opp.get("notes") or ""
            if notes and not notes.lower().startswith("up to"):
                if _re.search(r'[£$€]\s*\d', notes):
                    opp["notes"] = f"Up to {notes}"

            opportunities.append(opp)

        # Production genres for festival relevance filtering
        prod_genres_raw = self.request_metadata.get("genre") or []
        if isinstance(prod_genres_raw, str):
            prod_genres_raw = [prod_genres_raw]
        prod_genres_lower = {g.lower().strip() for g in prod_genres_raw if g}

        for festival in festivals:
            if not isinstance(festival, dict):
                continue
            # Festivals store location as freetext "City, Country" — extract
            # the country part and match against selected territories.
            fest_territory = (
                festival.get("territory") or festival.get("country") or ""
            ).strip()
            if not fest_territory:
                location_str = (festival.get("location") or "").strip()
                if "," in location_str:
                    fest_territory = location_str.rsplit(",", 1)[-1].strip()
                else:
                    fest_territory = location_str
            if fest_territory.lower() not in selected:
                continue
            fest_name = (festival.get("title") or festival.get("name") or "").strip()
            if not fest_name:
                continue
            # Genre relevance — include if festival accepts "All Genres" or
            # shares at least one genre with the production.
            fest_genres = festival.get("genres") or []
            if isinstance(fest_genres, str):
                try:
                    fest_genres = _json.loads(fest_genres)
                except (ValueError, TypeError):
                    fest_genres = [fest_genres]
            fest_genres_lower = {g.lower().strip() for g in fest_genres if g}
            if fest_genres_lower and "all genres" not in fest_genres_lower:
                if not fest_genres_lower & prod_genres_lower:
                    continue
                # content_restricted = True (DB-authoritative): festival is
                # content-type-specific — production must share a restricting genre.
                # content_restricted = False: no restriction beyond genre overlap.
                # content_restricted = None (legacy rows): fall back to frozenset.
                cr = festival.get("content_restricted")
                if cr is True:
                    restricting = fest_genres_lower & _RESTRICTING_FEST_GENRES
                    if restricting and not (restricting & prod_genres_lower):
                        continue
                elif cr is None:
                    # Legacy fallback for rows without content_restricted set
                    restricting = fest_genres_lower & _RESTRICTING_FEST_GENRES
                    if restricting and not (restricting & prod_genres_lower):
                        continue
                # cr is False: no content restriction — genre overlap alone is sufficient
            # Festival deadline: may be in 'deadlines' array or 'submission_deadline'
            fest_deadline = festival.get("submission_deadline") or ""
            if not fest_deadline:
                deadlines = festival.get("deadlines")
                if isinstance(deadlines, str):
                    try:
                        deadlines = _json.loads(deadlines)
                    except (ValueError, TypeError):
                        deadlines = None
                if isinstance(deadlines, list) and deadlines:
                    first = deadlines[0]
                    if isinstance(first, dict):
                        fest_deadline = first.get("date") or first.get("deadline") or ""
                    else:
                        fest_deadline = str(first)
            opportunities.append({
                "name": fest_name,
                "type": "Festival",
                "territory": fest_territory,
                "deadline": fest_deadline,
                "notes": festival.get("description") or festival.get("notes") or "",
            })

        # Format filtering — build eligibility index once for all checks
        title_to_elig: dict[str, list[str]] = {}
        for row in grants:
            title = (row.get("title") or "").strip()
            elig = row.get("eligibility") or []
            if isinstance(elig, str):
                try:
                    elig = _json.loads(elig)
                except (ValueError, TypeError):
                    elig = [elig]
            if title:
                title_to_elig[title.lower()] = [str(e) for e in elig]

        to_remove: set[str] = set()

        # Remove feature-film-only grants for non-feature formats
        if self._production_format in _NON_FEATURE_FORMATS:
            for opp in opportunities:
                name = (opp.get("name") or "").strip()
                db_elig = title_to_elig.get(name.lower(), [])
                combined = " ".join(db_elig).lower()
                if any(phrase in combined for phrase in _FEATURE_ONLY_PHRASES):
                    to_remove.add(name)

        # Remove short-film-only grants for non-short formats
        if self._production_format != "Short Film":
            for opp in opportunities:
                if opp.get("type") != "Fund":
                    continue
                name = (opp.get("name") or "").strip()
                # Check grant name and eligibility for short-film indicators
                name_lower = name.lower()
                db_elig = title_to_elig.get(name_lower, [])
                combined = (name_lower + " " + " ".join(db_elig)).lower()
                if any(phrase in combined for phrase in _SHORT_FILM_PHRASES):
                    to_remove.add(name)

        if to_remove:
            opportunities = [
                o for o in opportunities
                if (o.get("name") or "") not in to_remove
            ]

        return opportunities

    # ── Territory Deep Dives ───────────────────────────────────────────────

    def _build_territory_deep_dives(self, territories: list[str]) -> list[dict]:
        """Build territoryDeepDives shells. Narrative content filled by AI."""
        dives: list[dict] = []

        for territory in territories:
            rows = self._territory_incentives.get(territory, [])
            if not rows:
                continue

            best = best_incentive(rows, self._production_format)

            # Rebate rate string
            rebate_str = format_rate(
                best.get("rate_gross"), best.get("rate_net"),
            ) or "N/A"

            # Estimated rebate from pre-computed financials
            tf = self._territory_financials.get(territory)
            if tf:
                estimated_rebate = tf.get("gross_rebate", "See programme terms")
            else:
                estimated_rebate = "See programme terms"

            dive: dict = {
                "name": territory,
                "country": territory,
                "score": None,  # set after score computation
                "rebate": rebate_str,
                "estimatedRebate": estimated_rebate,
                # AI-filled narratives
                "infrastructure": None,
                "keyAdvantages": None,
                "keyRisks": None,
                # DB columns — NULL falls back to safe defaults
                "culturalTestLikelihood": (
                    "High (85%)" if best.get("cultural_test_required") is True else "N/A"
                ),
                "adminComplexity": best.get("admin_complexity") or "Medium",
            }

            # Payment speed
            timeline_notes = best.get("payment_timeline_notes")
            dive["paymentSpeed"] = timeline_notes or "Data not available"

            dives.append(dive)

        return dives

    # ── Attributions ───────────────────────────────────────────────────────

    def _build_attributions(self, territories: list[str]) -> list[dict]:
        """Build territory-specific data attributions."""
        from app.modules.reports.attributions import (
            MANDATORY_DISCLAIMER,
            TERRITORY_ATTRIBUTIONS,
        )
        from app.modules.reports.service import _TERRITORY_TO_ISO

        attributions: list[dict] = []
        seen: set[str] = set()
        for territory in sorted(territories):
            iso = territory if len(territory) == 2 else _TERRITORY_TO_ISO.get(territory, "")
            if iso and iso not in seen:
                text = TERRITORY_ATTRIBUTIONS.get(iso)
                if text:
                    attributions.append({"territory": territory, "text": text})
                    seen.add(iso)

        return attributions

    # ── Section Explainers ─────────────────────────────────────────────────

    def _inject_section_explainers(self, report: dict) -> None:
        """Inject hardcoded section explainers per v3 spec."""
        budget_currency = self._budget_currency

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
                "(typically 80% of budget), check programme caps, then apply gross and net rates. "
                "For tax credit programmes that distinguish above-the-line (ATL) costs "
                "(e.g. Canada PSTC), an estimated 15% ATL deduction is applied to the "
                "qualifying spend base. Programmes with no ATL/BTL distinction — "
                "notably the UK Audio-Visual Expenditure Credit (AVEC), which applies "
                "a flat rate to ALL qualifying expenditure — are not subject to this "
                "deduction. The headline number is your estimated out-of-pocket budget "
                "after incentives. All figures are estimates — verify with a production "
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

        sa = report.get("scriptAnalysis")
        if isinstance(sa, dict):
            sa["sectionExplainers"] = explainers
        else:
            report["scriptAnalysis"] = {"sectionExplainers": explainers}
        report["sectionExplainers"] = explainers

    def _crew_rate_anchor(self, territory: str) -> int | None:
        """Compute a costEfficiency anchor (0-100) from crew day rates.

        Uses the crew_costs DB table.  UK at £900/day = 50 (midpoint).
        Lower costs → higher anchor (more efficient); higher costs → lower anchor.
        Returns None when no crew data exists for the territory.
        """
        from app.modules.reports.service import _TERRITORY_TO_ISO, _ISO_TO_TERRITORY

        crew_costs = self.datasets.get("crew_costs", [])
        if not crew_costs:
            return None

        # Build lookup index (same logic as _build_crew_insights)
        crew_by_territory: dict[str, list[dict]] = {}
        for row in crew_costs:
            country = row.get("country") or ""
            terr = row.get("territory") or ""
            if country:
                crew_by_territory.setdefault(country, []).append(row)
            if terr:
                crew_by_territory.setdefault(terr, []).append(row)

        rows = crew_by_territory.get(territory, [])
        if not rows:
            iso = _TERRITORY_TO_ISO.get(territory, "")
            rows = crew_by_territory.get(iso, [])
        if not rows:
            full = _ISO_TO_TERRITORY.get(territory, "")
            rows = crew_by_territory.get(full, [])
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
        # UK baseline £900/day = anchor 50.  Lower daily rate = more efficient = higher anchor.
        # Formula: anchor = clamp(int(900 * 50 / avg_rate), 20, 85)
        anchor = int(900 * 50 / avg_rate)
        return max(20, min(85, anchor))

    # ── Scoring helpers ────────────────────────────────────────────────────

    def _compute_reliability(self, db_row: dict) -> tuple[int, str]:
        """Compute incentiveReliability score and bankabilityLabel."""
        reliability = to_float(db_row.get("payment_reliability"))
        timeline_max = to_float(db_row.get("payment_timeline_days_max"))

        # Reliability score (0-100)
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
            rel_score = 30

        label = _compute_bankability_label(reliability, timeline_max)
        return rel_score, label

    def _get_currency_score(self, territory: str) -> int:
        """Get pre-computed currency advantage score for a territory."""
        if not self._currency_scores:
            return 50
        score_data = self._currency_scores.get(territory)
        if score_data and isinstance(score_data, dict):
            computed = score_data.get("score")
            if computed is not None:
                return computed
        return 50

    @staticmethod
    def _compute_incentive_strength(db_row: dict) -> int:
        """Return incentiveStrength 0-100 from a DB incentive row.

        Formula: rateScore×0.35 + reliabilityScore×0.30
                 + qualificationScore×0.20 + stabilityScore×0.15
        """
        rate_gross = to_float(db_row.get("rate_gross")) or 0.0
        rate_score = _incentive_rate_score(rate_gross)

        reliability = to_float(db_row.get("payment_reliability"))
        if reliability is None:
            rel_score = 30
        elif reliability >= 0.90:
            rel_score = 90
        elif reliability >= 0.70:
            rel_score = 65
        elif reliability >= 0.50:
            rel_score = 40
        else:
            rel_score = 15

        qual_score = _incentive_qualification_score(db_row)
        stab_score = _incentive_stability_score(db_row)

        raw = (
            rate_score * 0.35
            + rel_score * 0.30
            + qual_score * 0.20
            + stab_score * 0.15
        )
        return max(0, min(100, int(round(raw))))

    # ── Post-AI merge and score computation ────────────────────────────────

    @staticmethod
    def compute_overall_scores(
        report: dict,
        production_priority: str = "full",
    ) -> None:
        """Compute overall scores on locationRankings after AI fills 3 dimensions.

        Call this after merging AI narratives. Uses all 6 dimensions with
        the appropriate weight table.
        """
        weights = SCORE_WEIGHTS.get(production_priority, SCORE_WEIGHTS["full"])

        rankings = report.get("locationRankings")
        if not isinstance(rankings, list):
            return

        for loc in rankings:
            if not isinstance(loc, dict):
                continue

            # Apply weather penalty before computing final score
            weather_penalty = loc.pop("weatherRiskImpact", 0) or 0

            weighted_sum = 0.0
            for dim, weight in weights.items():
                val = loc.get(dim)
                if isinstance(val, (int, float)):
                    weighted_sum += val * weight
                else:
                    weighted_sum += 50 * weight  # default neutral for missing AI dims
            new_score = int(round(weighted_sum))
            new_score = max(0, min(100, new_score + weather_penalty))
            loc["score"] = new_score

        # Sort by descending score
        rankings.sort(
            key=lambda l: l.get("score", 0) if isinstance(l, dict) else 0,
            reverse=True,
        )

        # Update executiveSummary
        if rankings:
            top = rankings[0]
            if isinstance(top, dict) and top.get("name"):
                summary = report.get("executiveSummary")
                if isinstance(summary, dict):
                    summary["recommendedTerritory"] = top["name"]
                    summary["recommendedTerritoryScore"] = top.get("score")

        # Propagate scores to territoryDeepDives
        ranking_scores: dict[str, int] = {}
        for loc in rankings:
            if isinstance(loc, dict) and loc.get("name") and isinstance(loc.get("score"), int):
                ranking_scores[loc["name"]] = loc["score"]

        dives = report.get("territoryDeepDives")
        if isinstance(dives, list):
            for dive in dives:
                if isinstance(dive, dict) and dive.get("name"):
                    score = ranking_scores.get(dive["name"])
                    if score is not None:
                        dive["score"] = score


# ── Module-level helper functions ──────────────────────────────────────────
# These mirror the validator's static methods exactly.


def _compute_bankability_label(
    reliability: float | None, timeline_max: float | None
) -> str:
    """Return BANKABLE / VERIFY FIRST / NOT BANKABLE per v3 spec."""
    if reliability is not None and reliability < 0.50:
        return "NOT BANKABLE"
    if timeline_max is not None and timeline_max > 365 and reliability is None:
        return "NOT BANKABLE"
    if reliability is not None and reliability >= 0.80:
        if timeline_max is None or timeline_max <= 180:
            return "BANKABLE"
    return "VERIFY FIRST"


def _incentive_rate_score(rate_gross: float) -> float:
    """Interpolate rateScore from system-prompt breakpoints."""
    _BP = [(0, 0), (20, 40), (30, 65), (40, 82), (53, 90), (100, 100)]
    if rate_gross <= 0:
        return 0.0
    for i in range(len(_BP) - 1):
        r0, s0 = _BP[i]
        r1, s1 = _BP[i + 1]
        if r0 <= rate_gross <= r1:
            t = (rate_gross - r0) / (r1 - r0)
            return s0 + t * (s1 - s0)
    return 100.0


def _incentive_qualification_score(db_row: dict) -> int:
    """Estimate qualification ease (0-100; higher = easier)."""
    nat_req = db_row.get("nationality_requirements")
    has_nat_req = False
    if nat_req:
        try:
            parsed = _json.loads(nat_req) if isinstance(nat_req, str) else nat_req
            has_nat_req = bool(parsed)
        except (ValueError, TypeError):
            has_nat_req = True

    base = 40 if has_nat_req else 80

    rules_raw = db_row.get("eligibility_rules_json")
    n_mandatory = 0
    if rules_raw:
        try:
            rules = _json.loads(rules_raw) if isinstance(rules_raw, str) else rules_raw
            if isinstance(rules, list):
                n_mandatory = sum(
                    1 for r in rules
                    if isinstance(r, dict) and r.get("required", True)
                )
        except (ValueError, TypeError):
            pass

    if n_mandatory >= 5:
        base -= 15
    elif n_mandatory >= 3:
        base -= 8

    if db_row.get("spv_eligible") is False:
        base -= 10

    return max(20, min(85, base))


def _incentive_stability_score(db_row: dict) -> int:
    """Estimate programme stability (0-100; higher = more stable)."""
    if (db_row.get("status") or "").lower() != "active":
        return 20

    warnings_raw = db_row.get("warnings_json")
    w_text = ""
    if warnings_raw:
        try:
            w = _json.loads(warnings_raw) if isinstance(warnings_raw, str) else warnings_raw
            if isinstance(w, list):
                w_text = " ".join(str(x) for x in w).lower()
        except (ValueError, TypeError):
            pass

    _FROZEN_KW = (
        "frozen", "suspended", "no new", "halted", "operational",
        "dtic payment", "payment delays",
    )
    _CAUTION_KW = (
        "conditionally bankable", "registration queue", "backlog",
        "cap not yet set", "delayed into", "verify",
    )

    if any(kw in w_text for kw in _FROZEN_KW):
        return 20
    if any(kw in w_text for kw in _CAUTION_KW):
        return 45

    reliability = to_float(db_row.get("payment_reliability"))
    if reliability is not None:
        if reliability >= 0.85:
            return 90
        if reliability >= 0.70:
            return 70
    return 70
