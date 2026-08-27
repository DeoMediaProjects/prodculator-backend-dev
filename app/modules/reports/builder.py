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
    STATIC_FX_TO_GBP,
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
    format_qualifying_spend_cap,
    is_vacuous_cap_label,
    format_money,
    currency_symbol,
    parse_money_string,
    clean_source,
    resolve_payment_timing,
    resolve_schedule,
    needs_format_eligibility_check,
)
from app.modules.reports.format_eligibility import (
    FORMAT_INELIGIBLE,
    FORMAT_UNVERIFIED,
    INELIGIBLE as FORMAT_INELIGIBLE_VERDICT,
    UNCONFIRMED_VERDICTS,
    UNVERIFIED,
    any_unverified_for_format,
    evaluate_format_eligibility,
    gate_state as format_gate_state,
    scores_zero as format_scores_zero,
)
from app.core.formats import canonical_format
from app.modules.reports.calculation_status import resolve_calculation_status
from app.modules.reports.coproduction_section import build_coproduction_structure
from app.modules.reports.programme_eligibility import (
    any_unavailable,
    evaluate_programme_eligibility,
    verdict_rank as programme_rank,
)
from app.modules.reports.producer_eligibility import (
    UNKNOWN as PRODUCER_UNKNOWN,
    evaluate_producer_eligibility,
    legacy_status as producer_legacy_status,
)
from app.modules.reports.readiness import (
    SECTION_EXPLAINER as _READINESS_EXPLAINER,
    compute_financial_readiness,
)
from app.modules.reports.matching import (
    estimate_completion_date,
    match_distributors,
    match_festivals,
    match_grants,
)
from app.modules.reports.scoring import (
    _TRUSTED_BANKABILITY_SOURCE_QUALITY,
    _compute_bankability_label,
    _incentive_qualification_score,
    _incentive_rate_score,
    _incentive_stability_score,
)
from app.modules.reports.shoot_window import (
    ADJACENT as SHOOT_WINDOW_ADJACENT,
    INSIDE as SHOOT_WINDOW_INSIDE,
    OUTSIDE as SHOOT_WINDOW_OUTSIDE,
    UNKNOWN as SHOOT_WINDOW_UNKNOWN,
    classify_shoot_window,
    format_month_ranges,
)
from app.modules.reports.stacking import resolve_stacking

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
        "incentiveStrength": 0.30, "incentiveReliability": 0.15,
        "costEfficiency": 0.20, "currencyAdvantage": 0.15,
        "crewDepth": 0.10, "infrastructure": 0.10,
    },
    "incentive": {
        "incentiveStrength": 0.45, "incentiveReliability": 0.15,
        "costEfficiency": 0.15, "currencyAdvantage": 0.15,
        "crewDepth": 0.05, "infrastructure": 0.05,
    },
    "location": {
        "crewDepth": 0.25, "infrastructure": 0.20,
        "costEfficiency": 0.20, "incentiveStrength": 0.15,
        "incentiveReliability": 0.10, "currencyAdvantage": 0.10,
    },
}

# Shoot duration thresholds (must match validator._LONG_SHOOT_THRESHOLDS)
_LONG_SHOOT_THRESHOLDS: dict[str, int] = {
    "TV Pilot": 12, "TV Series": 26, "Limited Series": 20,
    "Feature Film": 26, "Mini-Series": 20,
}
_LONG_SHOOT_DEFAULT = 26


def _join(names: list[str]) -> str:
    """"A", "A and B", "A, B and C" — for territory names inside a sentence."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"

# HETV constants (must match validator)
_HETV_TV_FORMATS = frozenset({"TV Series", "Limited Series", "Mini-Series", "Docuseries"})
_HETV_MIN_PER_HOUR_GBP = 1_000_000.0

# Deadline urgency window — kept in sync with matching.CLOSING_SOON_DAYS
# (same 90-day cutoff, applied here to the executive-summary flag over the
# same grant/festival deadline data).
_DEADLINE_URGENT_DAYS = 90

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
        # Programmes on record but not rankable today — suspended, withdrawn,
        # awaiting verification. Kept strictly apart from the index above: no rate
        # from here may be ranked, scored or costed. They are here so a territory
        # whose programme is suspended can be described accurately instead of being
        # reported as having no programme at all.
        self._territory_inactive_incentives = index_incentives_by_territory(
            datasets.get("_inactive_incentives", []) or []
        )
        self._territory_financials: dict = datasets.get("_territory_financials") or {}
        #: programme_id -> the statutory input keys its exact formula requires.
        self._programme_required_inputs: dict = (
            datasets.get("_programme_required_inputs") or {}
        )
        #: Territory label -> the producer's scenario for it: the spend allocated
        #: there and any statutory figures supplied. Absent for a territory the
        #: producer has not filled in, which is a real state and not an error.
        self._territory_scenarios: dict = datasets.get("_territory_scenarios") or {}
        self._production_format: str | None = datasets.get("_production_format")
        self._production_priority: str = datasets.get("_production_priority", "full")
        self._currency_scores: dict | None = datasets.get("_currency_advantage_scores")
        self._territory_profiles: dict = datasets.get("_territory_profiles") or {}

        # Budget info
        budget_gbp_data = datasets.get("_budget_gbp")
        self._budget_gbp: float | None = (
            budget_gbp_data.get("converted")
            if isinstance(budget_gbp_data, dict) else None
        )
        # Facts the eligibility gates test a programme against. Only what the
        # platform actually collects goes in here: a gate it cannot answer must
        # report as untested rather than be settled against a guess.
        #
        # Built after the budget is parsed, because `_budget_gbp` arrives as a dict
        # and the gates need the scalar. Passing the dict made every budget-based
        # gate silently untestable, which is exactly the failure mode this whole
        # module is meant to remove.
        # Prefer the canonical facts the service assembled, so the builder's
        # programme selection and the financials precompute cannot disagree about
        # which programme a territory is being costed under. The literal below is
        # the fallback for callers that construct a builder directly (tests, and
        # the sample report) and never went through the service.
        self._project_facts: dict = datasets.get("_project_facts") or {
            "format": datasets.get("_production_format"),
            "runtime_minutes": datasets.get("_runtime_minutes"),
            "budget_gbp": self._budget_gbp,
            # The service copies these across from request_metadata; reading the
            # metadata as a fallback means the expiry gate does not quietly become
            # untestable if that copy is ever dropped.
            "completion_date": (
                datasets.get("_completion_date")
                or request_metadata.get("completion_date")
            ),
            "filming_start_date": (
                datasets.get("_filming_start_date")
                or request_metadata.get("filming_start_date")
            ),
            # The country constraint. Resolved to ISO by the service because the
            # programmes state their requirement as ISO codes; the raw label is
            # carried too so the gate can resolve it itself when a caller builds
            # facts without going through the service.
            "producer_iso": datasets.get("_producer_iso"),
            "producer_country": (
                datasets.get("_producer_country")
                or request_metadata.get("country")
            ),
            "co_production_intent": (
                datasets.get("_co_production_intent")
                or request_metadata.get("co_production_interest")
            ),
        }
        # Populated by _select_territories. Declared here so the attribute exists
        # even if a caller reads it before build() runs.
        self._unanalysed_territories: list[dict] = []
        # Where a chosen label was analysed under a different one: "United States"
        # modelled as New Mexico, "Scotland" as the United Kingdom. A producer who
        # commits to one place and reads figures headed by another cannot tell
        # whether that is the incentive's real level or a mistake, so the report
        # has to be able to say which substitution it made and why.
        self._territory_substitutions: dict[str, str] = {}
        # Analysed for locations, crew, weather and currency, but carrying no
        # bankable rebate. They must not be scored on an incentive they do not
        # have, and they must not be presented as if they might pay one.
        self._no_incentive_territories: set[str] = set()
        self._built_incentive_estimates: list[dict] = []
        self._budget_currency: str = datasets.get("_budget_currency", "GBP")
        self._budget_original_amount: float | None = datasets.get("_budget_amount")
        self._fx_rates_from_budget: dict = datasets.get("_fx_rates_from_budget") or {}

    def build(self) -> dict:
        """Build the full report skeleton. AI-narrative fields are ``None``."""
        territories = self._select_territories()

        # Rank first, then build every other section from the ranked order.
        # Sections used to be built from _select_territories() order while only
        # locationRankings was sorted (post-AI), so the recommended-territory
        # card, budget scenarios and payment timing could each lead with a
        # different territory than the ranked table.
        location_rankings = self._build_location_rankings(territories)
        self._rank_territories_provisionally(location_rankings, self._production_priority)
        territories = self._ranked_territory_order(territories, location_rankings)
        self._territory_names = territories
        # Built from the RANKED order, after the reorder above, because every
        # territory-keyed section has to lead with the recommended territory. Held
        # on the instance so the short-film notice can be driven by what these
        # estimates actually say rather than by the production format alone.
        self._built_incentive_estimates = self._build_incentive_estimates(territories)

        # Complexity is scored from counted script inputs, not chosen by the model.
        # It used to be a free pick from four labels in a later narrative call with no
        # count fed into it, which is how one screenplay came back Medium on one run
        # and High on the next. None here when no script was parsed, in which case the
        # narrative value is still accepted.
        computed_complexity = self._computed_complexity()

        report: dict = {
            # AI fills these top-level narrative fields
            "genre": None,
            "tone": None,
            "scale": None,
            "complexity": computed_complexity,
            # Read by _merge_ai_narratives: when the complexity above was scored from
            # the parsed script, the model's label must not replace it.
            "_complexityIsComputed": computed_complexity is not None,
            # Deterministic sections
            "locationRankings": location_rankings,
            "incentiveEstimates": self._built_incentive_estimates,
            # Present only for a co-production. Built after the estimates because
            # it copies their figures rather than recalculating, so this section
            # and the incentive section cannot quote different numbers for the
            # same programme.
            "coProductionStructure": build_coproduction_structure(
                mode=self.request_metadata.get("production_structure_mode")
                or "comparison",
                scenarios=self._territory_scenarios,
                estimates=self._built_incentive_estimates,
                budget=self._budget_gbp,
                currency=self.request_metadata.get("budget_currency"),
                unallocated_spend=self.request_metadata.get("unallocated_spend"),
                route=self.request_metadata.get("co_production_route"),
                supranational_interest=self.request_metadata.get(
                    "supranational_support_interest"
                ),
            ),
            # Set only when the production format is one the programme data cannot
            # vouch for, so the PDF carries the same caveat the wizard showed
            # rather than the warning living only at intake.
            "formatEligibilityCaveat": self._format_eligibility_caveat(),
            # Selected territories that no section could analyse, each with its
            # reason. Empty in the ordinary case.
            "unanalysedTerritories": list(self._unanalysed_territories),
            # Chosen label → the label it was actually analysed under. Empty in the
            # ordinary case. Without it a producer who selected the United States
            # and read a section headed "New Mexico" had no way to know whether the
            # substitution was deliberate.
            "territorySubstitutions": dict(self._territory_substitutions),
            "programmeAvailabilityCaveat": self._programme_availability_caveat(),
            # Sits beside the incentive figures in every surface, because a warning
            # at the top or bottom of a report does not travel with a number a
            # producer copies out of the middle of it.
            "shortFormatIncentiveNotice": self._short_format_incentive_notice(),
            # Rendered before the recommendations on both surfaces. Built from the
            # ranked territories, so it is computed after the ranking settles.
            "shortFormatGateBanner": self._short_format_gate_banner(territories),
            "financialAnalysis": self._build_financial_analysis(territories),
            "executiveSummary": self._build_executive_summary(territories),
            "comparables": self._build_comparables(),
            "weatherLogistics": self._build_weather_logistics(territories),
            "fundingOpportunities": self._build_funding_opportunities(),
            # Parsed-script stats. Named scriptStats — scriptIntelligence is
            # already the AI-narrative key (creativeRecognition, complexityDrivers)
            "scriptStats": self._build_script_intelligence(),
            "territoryDeepDives": self._build_territory_deep_dives(territories),
            "attributions": self._build_attributions(territories),
            # AI fills this
            "alternativeStrategy": None,
        }

        # Festival + distributor recommendations (paid tiers only — preview
        # shows these as locked section titles)
        if not self.is_preview:
            festival_recs = self._build_festival_recommendations(territories)
            report["festivalRecommendations"] = festival_recs
            report["distributorRecommendations"] = (
                self._build_distributor_recommendations(festival_recs)
            )
            report["scriptOriginCallout"] = self._build_script_origin_callout(territories)

            # Financial readiness (handoff §4.1). Deterministic — no AI. Computed
            # last because it reads the sections above. Paid tiers only: the free
            # preview is stripped of every monetary figure, so there is nothing
            # to assess. Recomputed by ReportValidator.assert_integrity once the
            # ranking has settled, in case the AI's costEfficiency refinement
            # changed which territory the assessment should anchor on.
            readiness = compute_financial_readiness(
                report=report,
                datasets=self.datasets,
                request_metadata=self.request_metadata,
            )
            if readiness:
                report["financialReadiness"] = readiness

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
        # Selected territories that end up in no section of the report, with the
        # reason. A producer who chose three and sees two must be told which one is
        # missing and why; silence reads as a bug, and it hides the more useful fact
        # that the territory has no bankable programme on record.
        self._unanalysed_territories: list[dict] = []
        self._territory_substitutions: dict[str, str] = {}
        # Analysed for locations, crew, weather and currency, but carrying no
        # bankable rebate. They must not be scored on an incentive they do not
        # have, and they must not be presented as if they might pay one.
        self._no_incentive_territories: set[str] = set()

        if user_territories:
            # Use user-submitted territories, preserving order.
            # Only include territories that have incentive data in the DB.
            # When a parent territory (e.g. "United States") has no national
            # incentive but has children with data (Georgia, New York, etc.),
            # expand to the best child territory so the parent isn't silently
            # dropped from the report.

            # Parent countries the user also picked regions inside. The picker
            # nests regions under their country and keeps the country selected, so
            # choosing California and New Mexico submits "United States" as well.
            # Expanding it to a best child then added a third state nobody asked
            # for — usually New York — and presented it as a considered territory.
            # A country with no national programme of its own is a container here,
            # not a choice, so its regions speak for it.
            parents_with_chosen_children: set[str] = set()
            for t in user_territories:
                resolved = resolve_territory(t)
                if resolved and resolved.parent:
                    parents_with_chosen_children.add(resolved.parent.label)

            territories: list[str] = []
            for t in user_territories:
                if t in territories:
                    continue  # already covered, directly or via an expansion
                if t in self._territory_incentives or t in self._territory_financials:
                    territories.append(t)
                elif t in parents_with_chosen_children:
                    continue  # represented by the regions chosen inside it
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

                    def _is_child_of(child_label: str, target: str, rows: list) -> bool:
                        """Whether *child_label* belongs to *target*.

                        Asks the Territory enum as well as the DB column, because the
                        column is NULL on every sub-territory row in the dataset. Relying
                        on it alone meant selecting "United States" found no children and
                        dropped the country from the report entirely, even though six of
                        its states carry active programmes. A producer who picked three
                        territories got two, with nothing saying why.
                        """
                        if any(_parent_matches(r.get("parent_territory"), target) for r in rows):
                            return True
                        resolved = resolve_territory(child_label)
                        return bool(
                            resolved and resolved.parent and resolved.parent.label == target
                        )

                    children = [
                        child_t
                        for child_t, rows in self._territory_incentives.items()
                        if _is_child_of(child_t, t, rows)
                    ]
                    if children:
                        # Pick the child the production can actually use, then the
                        # best rate within that. Picking on rate alone chose the
                        # headline number regardless of whether its programme's
                        # thresholds rule this production out, so "United States"
                        # could resolve to a state whose minimum qualifying spend is
                        # many times the entire budget.
                        def _child_key(child: str) -> tuple[int, float]:
                            rows_for_child = self._territory_incentives[child]
                            usable = max(
                                programme_rank(
                                    evaluate_programme_eligibility(
                                        r, self._project_facts
                                    )["verdict"]
                                )
                                for r in rows_for_child
                            )
                            rate = max(
                                (to_float(r.get("rate_gross")) or 0)
                                for r in rows_for_child
                            )
                            return (usable, rate)

                        best_child = max(children, key=_child_key)
                        self._territory_substitutions[t] = best_child
                        if best_child not in territories:
                            territories.append(best_child)
                    else:
                        # t is a sub-territory (e.g. "Scotland") whose incentives
                        # are stored under its parent ("United Kingdom").  Look up
                        # the parent via the Territory enum and use it instead.
                        enum_t = resolve_territory(t)
                        parent_label = enum_t.parent.label if enum_t and enum_t.parent else None
                        if (
                            parent_label
                            and parent_label in self._territory_incentives
                            and parent_label not in territories
                        ):
                            self._territory_substitutions[t] = parent_label
                            territories.append(parent_label)
                        elif parent_label and parent_label in territories:
                            self._territory_substitutions[t] = parent_label
                        elif self._is_analysable_without_incentive(t):
                            # No bankable rebate, but we hold a real profile for it:
                            # crew depth, infrastructure, weather, currency. The
                            # intake tells producers a territory stays selectable
                            # "for location, crew or currency reasons", and then the
                            # report contained nothing about it at all — not a
                            # ranking row, not a weather entry, nothing. The
                            # incentive database was deciding whether a territory
                            # existed.
                            territories.append(t)
                            self._no_incentive_territories.add(t)
                        else:
                            self._unanalysed_territories.append({
                                "territory": t,
                                "reason": self._no_programme_reason(t),
                            })
        else:
            # Fallback: all territories with pre-computed financials
            territories = list(self._territory_financials.keys())
            for t in self._territory_incentives:
                if t and t not in territories:
                    territories.append(t)

        # A territory may be reached twice: chosen directly, and again as the best
        # child of a parent country that was also chosen. Selecting New York and the
        # United States is the ordinary way that happens, and it put the same
        # territory in the ranking twice. Deduplicated here, keeping first position,
        # so no ordering decision above has to remember to do it.
        deduped: list[str] = []
        for t in territories:
            if t not in deduped:
                deduped.append(t)
        territories = deduped

        # Filter out territories whose only incentive is supplementary, recording
        # the exclusion rather than performing it silently: a stacking credit is a
        # real fact about the territory, just not a standalone incentive.
        kept: list[str] = []
        for t in territories:
            if self._is_supplementary_only_territory(t):
                self._unanalysed_territories.append({
                    "territory": t,
                    "reason": (
                        "This territory's only incentive is a supplementary credit that "
                        "stacks onto another programme, so it cannot be modelled as a "
                        "standalone rebate."
                    ),
                })
            else:
                kept.append(t)
        territories = kept

        if self.is_preview:
            territories = territories[:3]

        return territories

    def _is_analysable_without_incentive(self, territory: str) -> bool:
        """Whether anything is known about *territory* beyond its rebate.

        A curated profile carries crew depth, infrastructure, cost and payment
        intelligence; weather carries the shoot-window risk. Any of that is enough
        to give a producer a real answer about a place, and all of it was being
        discarded because the incentive table had no active row.
        """
        if self._get_territory_profile(territory):
            return True
        return bool(self._territory_inactive_incentives.get(territory))

    def _no_programme_reason(self, territory: str) -> str:
        """Why *territory* carries no rebate — the true reason, not one of them.

        "No active incentive programme is on record" was said about every case,
        including a territory whose programme is on record and suspended. The
        dataset is explicit that these are different facts: Nigeria has no
        programme at all, South Africa has the DTIC rebate suspended since March
        2024 with a documented backlog. Telling a producer the second is the first
        misstates what they would be waiting for, and whether waiting is a plan.
        """
        rows = self._territory_inactive_incentives.get(territory) or []
        if rows:
            statuses = {
                (r.get("status") or "").strip().lower() for r in rows if r.get("status")
            }
            names = [r.get("program_name") or r.get("program") for r in rows]
            named = next((n for n in names if n), "Its incentive programme")
            if "suspended" in statuses:
                return (
                    f"{named} is on record for this territory but is currently "
                    f"suspended, so no rebate can be modelled against it today. This "
                    f"is a programme that is not paying out, not the absence of one: "
                    f"the territory remains relevant for locations, crew and currency, "
                    f"and the position changes if the programme is reinstated."
                )
            return (
                f"{named} is on record for this territory, but its status is "
                f"“{', '.join(sorted(statuses)) or 'unconfirmed'}” and its "
                f"availability cannot be confirmed today, so no rebate is modelled "
                f"for it. The territory may still suit the production for locations, "
                f"crew or currency reasons."
            )
        return (
            "No incentive programme is on record for this territory at all, so it "
            "carries no rebate to model. This is a structural fact rather than a "
            "delay — there is no programme to wait for. It may still suit the "
            "production for locations, crew or currency reasons."
        )

    def _is_supplementary_only_territory(self, territory: str) -> bool:
        """True if every incentive row for this territory is supplementary."""
        rows = self._territory_incentives.get(territory, [])
        if not rows:
            return False
        return all(r.get("is_supplementary") for r in rows)

    # ── Location Rankings ──────────────────────────────────────────────────

    @staticmethod
    def _ranked_territory_order(
        territories: list[str], rankings: list[dict],
    ) -> list[str]:
        """Reorder *territories* to match ranked *rankings*.

        Territories with no incentive rows are dropped from rankings, so they
        are kept here in their original relative order after the ranked ones —
        they still appear in weather, funding and deep-dive sections.

        Deliberately NOT reordered to put a declared ``must_film_in`` first. Doing
        so would move the recommended-territory card, the financial waterfall and
        the readiness verdict onto the committed territory, which is arguably where
        they belong — but it also redefines what "recommended" means across the
        product, and it would fight the post-AI re-sort in ``compute_overall_scores``.
        The commitment is carried by ``executiveSummary.mustFilmInNote`` instead,
        which states which territory to plan against and what the ranking is
        actually telling you. Changing the ordering is a product decision, not a
        defect fix.
        """
        ranked = [
            loc["name"] for loc in rankings
            if isinstance(loc, dict) and loc.get("name")
        ]
        seen = set(ranked)
        return ranked + [t for t in territories if t not in seen]

    def _build_location_rankings(self, territories: list[str]) -> list[dict]:
        """Build locationRankings with all deterministic scores computed.

        AI-dependent fields (costEfficiency, crewDepth, infrastructure,
        reasoning, keyAdvantages) are set to None for later filling.
        """
        rankings: list[dict] = []

        for territory in territories:
            rows = self._territory_incentives.get(territory, [])
            # A territory with no bankable programme still gets a row, provided
            # _select_territories decided we know enough about it to say something.
            # Skipping it here is the second of the two gates that made a committed
            # territory vanish from the report entirely: the first dropped it from
            # the selection, this one dropped it from the rankings even if it
            # survived. Everything below that reads `best` is guarded.
            no_incentive = territory in self._no_incentive_territories
            if not rows and not no_incentive:
                continue

            best = (
                best_incentive(rows, self._production_format, self._project_facts)
                if rows else {}
            )
            effective_rate = (
                format_rate(best.get("rate_gross"), best.get("rate_net"))
                if rows else None
            )

            # Cost efficiency: curated territory_profiles score (crew day-rate
            # derivation removed 2026-07, owner-approved). None = no sourced
            # data -> neutral treatment downstream; AI may refine within ±15.
            territory_profile = self._get_territory_profile(territory)

            # Compute deterministic scores.
            #
            # ONE RULE, three states, and the middle one is the point:
            #
            #   researched and excluded      0        There is no rebate here to value.
            #   researched and it qualifies  computed A rebate this project can claim.
            #   nobody has checked           None     Not scored. Neutral in the total.
            #
            # The middle state is what this report got wrong. UK AVEC (Enhanced/IFTC)
            # had unverified short-film eligibility, a confirmed incentive of £0, and a
            # potential rebate marked illustrative — and still scored Incentive Value
            # 88, carrying the territory to an overall 73 and first place. The report
            # simultaneously told the reader not to rely on that rebate and ranked the
            # territory first because of it.
            #
            # A previous pass argued the opposite: that "nobody checked" is not evidence
            # the rebate is worthless, so scoring it neutral understates the territory
            # as confidently as quoting it overstates it. That argument is sound about
            # the PROGRAMME and wrong about the RANKING. The ranking answers "where
            # should this production shoot", and a benefit it may not be able to claim
            # cannot be evidence for an answer to that question. The illustrative figure
            # still appears in the incentive section, where it is labelled; it just no
            # longer moves the order.
            #
            # This also settles a three-way disagreement rather than adding a fourth
            # position. project_incentive.resolve_project_incentive already publishes
            # `canAffectRanking = False` for an unverified programme, and its docstring
            # says an ineligible status may not "improve a ranking as realisable value";
            # the PDF template already carries a "Not scored" branch explaining that an
            # unverified dimension "neither raises nor lowers this territory's score";
            # and tests/test_project_incentive_consistency.py already asserts the
            # neutral-not-rewarded rule. All three were correct and all three were being
            # bypassed here, because the guard below required `status == "ineligible"`
            # in addition to `canAffectRanking is False`, and an unverified programme's
            # status is "unverified". None is the value _weighted_score reads as neutral.
            fmt_verdict = (
                evaluate_format_eligibility(
                    best, self._production_format, self._project_facts,
                ).get("verdict")
                if rows else None
            )
            tf_for_rank = self._territory_financials.get(territory) or {}
            can_rank = tf_for_rank.get("incentive_can_affect_ranking")
            status_for_rank = tf_for_rank.get("incentive_eligibility_status")
            if can_rank is None and rows:
                # No precomputed financials for this territory. The rule must not depend
                # on which dict happened to be populated: with _territory_financials
                # empty, can_rank was None, the guards below all fell through, and an
                # unverified programme was rewarded with full strength again — the exact
                # behaviour this rule exists to prevent, reachable through a different
                # door.
                #
                # Derived exactly as resolve_project_incentive derives it, including the
                # scoping that matters most here: format is a REQUIRED dimension only for
                # formats whose eligibility genuinely diverges from what these programmes
                # are written for. A feature is not held back by the absence of a record
                # stating that features are accepted, which is true of every programme in
                # the dataset. Without that scoping this fallback marked every territory
                # unscored for every production, because eligible_formats is null on
                # nearly every row — turning a fix for shorts into a gutted ranking for
                # everyone.
                format_required = needs_format_eligibility_check(self._production_format)
                format_ok = (
                    fmt_verdict not in UNCONFIRMED_VERDICTS if format_required else True
                )
                can_rank = format_ok and evaluate_programme_eligibility(
                    best, self._project_facts,
                )["available"]
            if no_incentive:
                # Zero, not neutral. There is no accessible rebate here and a
                # neutral 50 would quietly credit the territory with half of one.
                strength = 0
            elif format_scores_zero(fmt_verdict):
                # A researched exclusion. Distinct from every "unresolved" case
                # below, and the only format state that zeroes the dimension.
                strength = 0
            elif can_rank is False and status_for_rank == "ineligible":
                # A verified exclusion on non-format grounds — budget floor, ceiling,
                # programme status. Also a real answer, also zero.
                strength = 0
            elif can_rank is False:
                # Unresolved, on format or on any other required dimension. Not scored,
                # so the dimension is neutral in the weighted total and the territory
                # neither gains nor loses position on a rebate nobody has confirmed
                # this production can claim.
                strength = None
            else:
                strength = self._compute_incentive_strength(best)
            reliability_score, bankability_label = self._compute_reliability(best, territory_profile)
            currency_score = self._get_currency_score(territory)
            cost_anchor = self._profile_score(territory_profile, "cost_efficiency_score")
            crew_depth_score = self._profile_score(territory_profile, "crew_depth_score")
            infrastructure_score = self._profile_score(territory_profile, "infrastructure_score")

            loc: dict = {
                "name": territory,
                "rebatePercent": effective_rate or "N/A",
                "score": None,  # computed after AI fills 3 dimensions
                # DB-deterministic dimensions
                "incentiveStrength": strength,
                "incentiveReliability": reliability_score,
                "currencyAdvantage": currency_score,
                "bankabilityLabel": bankability_label,
                # DB/profile-driven dimensions.
                "costEfficiency": cost_anchor,  # DB anchor; AI refines within ±15
                "crewDepth": crew_depth_score,
                "infrastructure": infrastructure_score,
                "crewDepthTier": self._profile_tier_label(
                    territory_profile, "crew_depth_tier",
                ),
                "infrastructureTier": self._profile_tier_label(
                    territory_profile, "infrastructure_tier",
                ),
                # Internal anchor for AI clamping — stripped before response
                "_costEfficiencyAnchor": cost_anchor,
                # AI-filled narratives
                "reasoning": None,
                "keyAdvantages": None,
                "keyRisks": [],  # DB risks populated below, AI appends
                # Driven solely by the cultural_test_required DB column (no heuristic):
                # True → fixed "High (85%)" likelihood estimate, False/NULL → "N/A".
                # Always a string — the frontend renders this under a "Likelihood" label.
                "culturalTestLikelihood": (
                    "High (85%)" if best.get("cultural_test_required") is True else "N/A"
                ),
            }

            if no_incentive:
                # Say it on the row itself. A territory shown beside others that
                # carry rebate figures, with nothing stating why this one does not,
                # reads as missing data rather than as the answer.
                reason = self._no_programme_reason(territory)
                loc["hasNoBankableIncentive"] = True
                loc["incentiveAvailability"] = reason
                loc["rebatePercent"] = "N/A"
                loc["keyRisks"].append(reason)

            # Canonical payment timing. Reading payment_timeline_notes alone
            # reported "Data not available" for every programme that recorded its
            # window numerically and left the note blank.
            loc["paymentTiming"] = resolve_payment_timing(
                best, self._get_territory_profile(territory),
            )
            loc["paymentSpeed"] = loc["paymentTiming"]["label"]

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

            # PR 5 — Schedule Viability Score
            shoot_months_list = self.datasets.get('_shoot_months') or []
            shoot_month = shoot_months_list[0] if shoot_months_list else None
            shoot_weeks = float(self.datasets.get('_shoot_weeks') or 4)
            ext_pct = float(self.datasets.get('_ext_int_ratio') or 50)
            if shoot_month:
                svs_data = self._compute_schedule_viability(
                    territory, shoot_month, shoot_weeks, ext_pct
                )
                loc['scheduleViabilityScore'] = svs_data['svs']
                loc['contingencyDaysEstimate'] = svs_data['contingency_days']
                # Weather penalty: SVS < 55 reduces costEfficiency up to 10 points
                if svs_data['svs'] < 55:
                    penalty = min(10, int((55 - svs_data['svs']) / 5))
                    current_ce = loc.get('costEfficiency')
                    if isinstance(current_ce, (int, float)):
                        loc['costEfficiency'] = max(0, int(current_ce) - penalty)
                        loc['costEfficiencyWeatherPenalty'] = penalty

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

            # Long payment timeline.
            #
            # This used to say "this incentive should not be treated as
            # investor-bankable" on every programme paying out beyond 180 days —
            # including ones this same report had just classified BANKABLE. New Mexico
            # carried the badge and the denial of it on one page, because the badge
            # comes from territory_profiles (cert 26w + payment 0w = 26 → BANKABLE) and
            # this line comes from incentive_programs.payment_timeline_days_max (270),
            # and nothing compared the two.
            #
            # A long payment window is a real cash-flow fact and worth stating. What it
            # is not is a re-classification: bankability means a lender will advance
            # against the receivable, which is a different question from when the cash
            # lands. So the wording now follows the canonical label — it states the
            # timing either way, and only contradicts bankability where the canonical
            # field already does.
            pay_max = to_float(db_row.get("payment_timeline_days_max"))
            if pay_max is not None and pay_max > 180:
                months_max = int(pay_max / 30)
                pay_min = to_float(db_row.get("payment_timeline_days_min"))
                months_min = int((pay_min or pay_max) / 30)
                label = (loc.get("bankabilityLabel") or "").upper()
                if label == "BANKABLE":
                    reliability_msg = (
                        f"Payment timeline {months_min}-{months_max} months — the "
                        f"programme is classified Bankable, but the production still "
                        f"has to carry the cost until the rebate lands. Plan interim "
                        f"cash flow for the full window."
                    )
                else:
                    reliability_msg = (
                        f"Payment timeline {months_min}-{months_max} months — this "
                        f"incentive should not be treated as investor-bankable. "
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

    def _short_format_incentive_notice(self) -> str | None:
        """Notice placed beside the incentive figures, not only at the report ends.

        Raised only for a short-form project and only when at least one incentive on
        display is potential rather than confirmed. A notice that appears on every
        report stops being read, and one that appears when every figure IS confirmed
        is simply false.
        """
        if not needs_format_eligibility_check(self._production_format):
            return None
        estimates = self._built_incentive_estimates or []
        if not any(e.get("incentiveIsConfirmed") is False for e in estimates):
            return None
        return (
            "Tax incentives shown as Potential or Unverified are illustrative "
            "calculations only. Short-film eligibility varies by programme and may "
            "depend on format, running time, production spend, distribution plans "
            "and other requirements. Unverified incentive amounts are not included "
            "in confirmed project savings or in the net production cost shown "
            "anywhere in this report."
        )

    def _programme_availability_caveat(self) -> str | None:
        """Blanket caveat, raised only while some programme fails its own thresholds.

        Driven by the data like the format caveat, so it retires itself rather than
        becoming permanent furniture. Deliberately separate from the format caveat:
        "this programme does not take shorts" and "your budget is below this
        programme's floor" are different problems with different remedies, and
        merging them into one warning would make both vaguer.
        """
        rows = [r for rows in self._territory_incentives.values() for r in rows]
        if not any_unavailable(rows, self._project_facts):
            return None
        return (
            "Some programmes in this report are not available to this production at "
            "its current budget, or their availability could not be established. "
            "Where a programme is marked not available, its stated minimum "
            "qualifying spend, budget ceiling or programme status rules this "
            "production out, and no rebate figure is shown for it because there is "
            "no figure it could claim. The territory is still listed, because its "
            "locations, crew and currency may remain relevant. Confirm any "
            "threshold with the programme administrator before ruling a territory "
            "in or out on the strength of this report."
        )

    def _format_eligibility_caveat(self) -> str | None:
        """Blanket caveat, raised only while some programme in this report is unverified.

        Driven by the data rather than by the format alone, so it retires itself the
        moment every programme in the report carries verified or settled eligibility.
        A caveat that cannot switch off stops being read.
        """
        # Scoped to formats whose eligibility materially diverges from the norm
        # these programmes are written for. Production incentives in this dataset are
        # built around features and scripted TV; the real exclusion risk is
        # concentrated in short-form work, which is commonly served by separate grant
        # schemes instead. Raising the caveat on every format made it fire on every
        # report, including features, where it added no information and trained
        # producers to scroll past the one warning that matters.
        #
        # This is a deliberate assumption and worth naming: a feature is treated as
        # within a programme's intended scope unless a verified whitelist says
        # otherwise. Where a programme IS verified as excluding a format, the
        # per-programme verdict still says so on any format, because that is recorded
        # fact rather than assumption.
        if not needs_format_eligibility_check(self._production_format):
            return None
        rows = [r for rows in self._territory_incentives.values() for r in rows]
        if not any_unverified_for_format(
            rows, self._production_format, self._project_facts
        ):
            return None
        label = (self._production_format or "").strip().lower()
        return (
            f"Format eligibility is unverified for some programmes in this report. Where a "
            f"programme is marked unverified below, we have not established whether it accepts "
            f"a {label}, and its rebate is shown as an indication only rather than as an amount "
            f"available to this production. Programmes marked eligible have been checked against "
            f"the source shown. Confirm anything unverified with the programme administrator or "
            f"film commission before relying on it."
        )

    def _short_format_gate_banner(self, territories: list[str]) -> dict | None:
        """Banner shown before the recommendations on a short-format report.

        Raised when at least two RANKED territories cannot confirm the programme
        accepts a short — either researched-and-excluded, or never checked. One
        such territory is a fact about that territory; two or more is a fact about
        short films, and a producer reading a page of rebate figures deserves to
        be told which of those they are looking at before they read any of it.

        Counted over ranked territories rather than over programme rows, because
        the reader is comparing territories: three unverified programmes inside
        one territory is still one territory they cannot rely on.
        """
        if not needs_format_eligibility_check(self._production_format):
            return None

        ineligible: list[str] = []
        unverified: list[str] = []
        for territory in territories:
            rows = self._territory_incentives.get(territory, [])
            if not rows:
                continue
            best = best_incentive(rows, self._production_format, self._project_facts)
            state = format_gate_state(
                evaluate_format_eligibility(
                    best, self._production_format, self._project_facts,
                ).get("verdict")
            )
            if state == FORMAT_INELIGIBLE:
                ineligible.append(territory)
            elif state == FORMAT_UNVERIFIED:
                unverified.append(territory)

        affected = ineligible + unverified
        if len(affected) < 2:
            return None

        label = (self._production_format or "short").strip().lower()
        parts = [
            f"Many production incentive programmes are written for features and "
            f"scripted television, and exclude short films outright."
        ]
        if ineligible:
            parts.append(
                f"{_join(ineligible)} {'is' if len(ineligible) == 1 else 'are'} "
                f"confirmed as not accepting a {label}, so no rebate is modelled "
                f"for {'it' if len(ineligible) == 1 else 'them'}."
            )
        if unverified:
            parts.append(
                f"For {_join(unverified)}, we have not established whether the "
                f"programme accepts a {label}. Any figure shown is illustrative, "
                f"not an amount this production can count on."
            )
        parts.append(
            "Confirm with the programme administrator or film commission before "
            "building either into a finance plan."
        )
        return {
            "title": f"Short-format eligibility affects {len(affected)} of your territories",
            "body": " ".join(parts),
            "ineligibleTerritories": ineligible,
            "unverifiedTerritories": unverified,
        }

    def _build_incentive_estimates(self, territories: list[str]) -> list[dict]:
        """Build fully deterministic incentiveEstimates from DB data."""
        estimates: list[dict] = []
        present_by_territory: dict[str, set[str]] = {}

        for territory in territories:
            rows = self._territory_incentives.get(territory, [])
            if not rows:
                continue

            best = best_incentive(rows, self._production_format, self._project_facts)
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

        # PROD-FIX-007 — if the engine ruled this programme out at the
        # production's budget and modelled a replacement, the estimate must be
        # labelled with the programme that was actually modelled.
        #
        # Previously `program` and `rate` came from db_row while
        # `estimatedRebate` came from the switched calculation, so a single
        # recommendation could cite one programme's name and rate beside
        # another's figure — the Lion King report named the Independent Film
        # Tax Credit at 39.75% two paragraphs above a waterfall computed at the
        # VFX credit's 29.25%.
        tf_for_naming = self._territory_financials.get(territory) or {}
        modelled_programme = tf_for_naming.get("programme")
        switched = bool(modelled_programme) and modelled_programme != program_name

        est: dict = {
            "territory": territory,
            "program": modelled_programme if switched else program_name,
        }

        # Rate — must describe the same programme as `program` above.
        if switched:
            est["rate"] = tf_for_naming.get("rate") or "N/A"
            est["programmeNote"] = tf_for_naming.get("programme_note")
        else:
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
                est["estimatedRebate"] = (
                    tf.get("net_rebate")
                    or tf.get("gross_rebate")
                    or "See programme terms"
                )
            else:
                est["estimatedRebate"] = "See programme terms"

        # Cap display — three possible sources, checked in priority order:
        # 1. rebate_cap_amount — hard per-project rebate ceiling (e.g. SA R25M)
        # 2. DB cap text label (e.g. "Budget cap £23.5M") — carries semantic meaning
        # 3. cap_amount — budget threshold formatted automatically
        #
        # PROD-FIX-007 — caps are programme-specific. When the engine switched
        # programmes, the cap shown must be the modelled programme's own, not
        # the ruled-out one's: the Lion King report showed the IFTC's fixed
        # £6.36M project cap beside a figure computed under a different
        # programme, where that cap does not apply at all.
        cap_row = db_row
        if switched:
            cap_row = next(
                (
                    r for r in self._territory_incentives.get(territory, [])
                    if (r.get("program") or r.get("program_name")) == modelled_programme
                ),
                # No row for the modelled programme means we cannot state a cap
                # honestly; an empty dict yields no cap rather than a wrong one.
                {},
            )

        rebate_cap = cap_row.get("rebate_cap_amount")
        rebate_cap_cur = cap_row.get("rebate_cap_currency") or cap_row.get("cap_currency") or "GBP"
        if rebate_cap is not None and to_float(rebate_cap):
            formatted_rebate_cap = format_cap(rebate_cap, rebate_cap_cur)
            if formatted_rebate_cap:
                est["cap"] = f"{formatted_rebate_cap} per project"
        if "cap" not in est:
            db_cap_label = (cap_row.get("cap") or "").strip()
            # Skip labels that assert an absence rather than describing a ceiling.
            # `cap` is populated from the v4 source's `rebateCap`, so UK AVEC's
            # "No cap" was being printed verbatim as this programme's cap while
            # its 80% qualifying-spend restriction went unstated entirely.
            if not is_vacuous_cap_label(db_cap_label):
                est["cap"] = db_cap_label
        if "cap" not in est:
            cap_amount = cap_row.get("cap_amount")
            cap_currency = cap_row.get("cap_currency") or "GBP"
            canonical_cap = format_cap(cap_amount, cap_currency)
            if canonical_cap is not None:
                est["cap"] = canonical_cap
        if "cap" not in est:
            # A programme with no rebate ceiling and no budget threshold may still
            # restrict what counts as qualifying spend. That restriction changes the
            # money, so it is the cap the producer needs to read.
            est["cap"] = format_qualifying_spend_cap(
                cap_row.get("qualifying_spend_cap_pct"),
                cap_row.get("qualifying_spend_cap_amount"),
                cap_row.get("qualifying_spend_cap_currency")
                or cap_row.get("currency")
                or "GBP",
            ) or "No cap stated"

        # Payment timeline, from the one canonical resolver.
        est["paymentTiming"] = resolve_payment_timing(
            db_row, self._get_territory_profile(territory),
        )
        est["paymentSpeed"] = est["paymentTiming"]["label"]

        # Format eligibility, evaluated once and rendered by every surface, so the
        # web report and the PDF cannot disagree about whether this programme
        # accepts the production's format.
        eligibility = evaluate_format_eligibility(
            db_row, self._production_format, self._project_facts,
        )
        # Surfaced only where it carries information. For a feature or scripted TV
        # project — what these programmes are written for — "nobody has explicitly
        # recorded that this programme accepts features" is true of every programme
        # in the dataset, so showing it on all of them says nothing and buries the
        # cases that matter. A recorded exclusion or a stated condition is different:
        # that is fact rather than absence of data, and it shows on any format.
        format_matters = needs_format_eligibility_check(self._production_format)
        merely_unrecorded = eligibility["verdict"] == UNVERIFIED

        # ── Confirmed vs potential, mirroring _pre_compute_territory_financials ──
        # A rebate calculation is arithmetic; eligibility is a fact about the
        # programme. Only a verified-eligible programme may present its figure as an
        # amount this production can count on. The illustrative calculation is kept
        # under a separate key so it can be shown as "potential" without ever being
        # mistaken for, or summed into, a confirmed total.
        # Read from the one resolved status rather than re-derived here. Every
        # section that answered this question for itself is how the report came to
        # contradict itself, so this reads the answer and adds nothing to it.
        tf_for_money = self._territory_financials.get(territory) or {}
        confirmed = bool(tf_for_money.get("incentive_is_confirmed", True))
        est["incentiveEligibilityStatus"] = tf_for_money.get(
            "incentive_eligibility_status", eligibility["verdict"]
        )
        est["incentiveIsConfirmed"] = confirmed
        est["incentiveEligibilityLabel"] = tf_for_money.get("incentive_eligibility_label")
        est["incentiveEligibilityReasons"] = tf_for_money.get(
            "incentive_eligibility_reasons"
        ) or []
        # The programme's own rate stays visible whatever the verdict: it is a fact
        # about the programme. What it must never do is read as this project's rate.
        est["headlineRate"] = est.get("rate")
        if not confirmed:
            est["confirmedIncentive"] = None
            # Suppressed entirely on a hard failure. A potential figure printed
            # beside "not available at this budget" is a contradiction the reader
            # has to resolve on our behalf.
            est["potentialIncentive"] = (
                tf_for_money.get("potential_net_rebate")
                if tf_for_money.get("show_potential_incentive")
                else None
            )
            # Same rule as the ranking row, so the two fields of this name on one
            # territory cannot disagree — they did, and the estimate's flat 0 was
            # the number the badge picked up.
            #
            # Three states, matching _build_location_rankings exactly:
            #   0     a researched answer of "no rebate here" — format excluded,
            #         budget below the floor or above the ceiling, programme suspended.
            #   None  unresolved. Not scored, so it cannot lift the territory on a
            #         benefit nobody has confirmed this production can claim.
            #   int   confirmed eligible. The computed strength.
            if (
                format_scores_zero(eligibility.get("verdict"))
                or tf_for_money.get("incentive_eligibility_status") == "ineligible"
            ):
                est["incentiveStrength"] = 0
            elif tf_for_money.get("incentive_can_affect_ranking") is False:
                est["incentiveStrength"] = None
            else:
                est["incentiveStrength"] = self._compute_incentive_strength(db_row)
        else:
            est["confirmedIncentive"] = est.get("estimatedRebate")
            est["potentialIncentive"] = None

        if format_matters or not merely_unrecorded:
            est["formatEligibility"] = eligibility
            # An unconfirmed programme must not present its rebate as an amount the
            # production can count on. The figure stays visible, labelled.
            #
            # INELIGIBLE has to be excluded explicitly. UNCONFIRMED_VERDICTS holds only
            # the two "unresolved" verdicts, so testing membership alone returned True
            # for a verified exclusion — California, whose Program 4.0 record now
            # carries a researched `eligible_formats.short = false`, was marked
            # rebateIsConfirmed=True. A researched "no" is the strongest possible reason
            # for the answer to be False, and it was producing the same value as a
            # confirmed yes. Found by the cross-section validator on a real generated
            # report, which is the check earning its place.
            est["rebateIsConfirmed"] = (
                eligibility["verdict"] not in UNCONFIRMED_VERDICTS
                and eligibility["verdict"] != FORMAT_INELIGIBLE_VERDICT
            )
        else:
            est["formatEligibility"] = None
            est["rebateIsConfirmed"] = True

        # Whether the production clears this programme's own stated thresholds:
        # minimum qualifying spend, budget ceiling, expiry, status. A blunter
        # question than format, and until now an unasked one, which is how a
        # programme with a $1,000,000 floor came to be ranked second and quoted a
        # rebate on a $61,780 qualifying spend.
        availability = evaluate_programme_eligibility(db_row, self._project_facts)
        est["programmeEligibility"] = availability
        if not availability["available"]:
            # Ranking and the executive summary read this to keep an unusable
            # programme below every usable one.
            est["incentiveStrength"] = 0
            # The figure is withdrawn rather than footnoted. A number a producer
            # cannot claim is worse than no number: it anchors the financing plan
            # and survives being copied out of the report into a budget document,
            # where the caveat does not travel with it. The rate stays, because the
            # rate is a true fact about the programme.
            est["estimatedRebateWithheld"] = est.get("estimatedRebate")
            est["estimatedRebate"] = availability["label"]

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
            territory_profile = self._get_territory_profile(territory)
            est["bankabilityLabel"] = _compute_bankability_label(
                reliability, timeline_max, profile=territory_profile,
            )

        # HETV threshold check
        self._apply_hetv_check(est, db_row)

        # One canonical status for this figure, derived from the verdicts already
        # resolved above rather than from a second opinion. Last on purpose: it
        # reads eligibility, confirmation and the mechanism gate, so it has to run
        # after all three have been settled.
        est.update(resolve_calculation_status(
            est,
            db_row,
            scenario=self._territory_scenarios.get(territory),
            declared_inputs=self._programme_required_inputs.get(
                db_row.get("programme_id")
            ),
        ))

        # The contract's one hard rule about figures: a status that may not carry
        # an amount does not carry one. Enforced here rather than trusted to every
        # consumer, because an illustrative figure and a relied-upon figure look
        # identical once rendered, and the withheld value is kept for audit.
        if not est.get("calculationCarriesFigure"):
            for field in ("confirmedIncentive", "potentialIncentive"):
                if est.get(field) is not None:
                    est.setdefault("estimatedRebateWithheld", est[field])
                    est[field] = None

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
        """Attach the producer/nationality verdict to this estimate.

        Delegates rather than deciding. This method used to hold its own copy of
        the nationality comparison, and it was wrong in two ways at once: it read
        a ``_producer_country`` no client ever populated, so it took its
        "jurisdiction unknown" branch on every report; and it compared that value
        to the requirement as raw text, so a label like "United Kingdom" would
        never have matched the stored code "GB" even once the field was wired up.
        Both are fixed at the source now, and the verdict comes from the one gate
        every other surface reads.
        """
        result = evaluate_producer_eligibility(db_row, self._project_facts)

        # Never overwrite a status another check already settled — the format and
        # HETV checks run against the same estimate and their answers stand.
        if not est.get("eligibilityStatus"):
            est["eligibilityStatus"] = producer_legacy_status(result)
        if result["explanation"] and not est.get("eligibilityNote"):
            est["eligibilityNote"] = result["explanation"]

        est["producerEligibilityStatus"] = result["verdict"]
        if result["requiredNationalities"]:
            est["requiredNationalities"] = result["requiredNationalities"]
        if result["routes"]:
            est["producerEligibilityRoutes"] = result["routes"]

        # An untestable requirement still has to reach the producer as a question
        # to go and answer, which is what the old unconditional assumption note
        # was reaching for. It is stated only when a requirement actually exists.
        if result["verdict"] == PRODUCER_UNKNOWN:
            reqs = est.setdefault("requirements", [])
            assumption = (
                f"Confirm the production company's jurisdiction: this programme is "
                f"restricted to {', '.join(result['requiredNationalities'])} "
                f"producers and none is recorded for this project."
            )
            if not any(
                "jurisdiction" in r.lower() for r in reqs if isinstance(r, str)
            ):
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

                # One canonical answer for whether these two programmes combine.
                #
                # The detector this replaces produced the report's flat contradiction:
                # a generated note saying "UK VFX Expenditure Credit stacks ON TOP of
                # AVEC (Enhanced/IFTC)" printed on the same page as the IFTC record's
                # own text saying "Cannot be combined with the VFX uplift". It missed
                # for two independent reasons, and both are worth naming because either
                # alone would have been enough:
                #
                #   1. it read the exclusion off the SUPPLEMENTARY row, but the UK
                #      exclusion is recorded on the PRIMARY row, which it never looked
                #      at; and
                #   2. it matched only the literal "cannot be combined with", while the
                #      VFX row's own qs_basis says "Cannot combine with the IFTC
                #      enhanced rate" — a phrasing outside the pattern.
                #
                # resolve_stacking reads every constraint-bearing field on BOTH rows.
                # The correct answer for this pair is that they do NOT stack, so the DB
                # prose was right and the generated note was wrong.
                primary_row = next(
                    (
                        r for r in territory_rows.get(territory, [])
                        if (r.get("program") or "").strip() == primary
                    ),
                    None,
                )
                stacking = resolve_stacking(
                    primary_row, row,
                    primary_name=primary if primary != "the primary incentive" else None,
                    supplementary_name=prog,
                )
                stacking_note = stacking["note"]

                stub: dict = {
                    "territory": territory,
                    "program": prog,
                    "rate": format_rate(rate_gross, rate_net) or "See DB",
                    "estimatedRebate": (
                        "Qualifying VFX/specialist spend only — "
                        "see stackingNote for calculation basis"
                    ),
                    "stackingNote": stacking_note,
                    # The canonical relationship, carried beside the sentence composed
                    # from it, so the cross-section validator can check the prose in
                    # this territory against the resolved answer for THIS pair rather
                    # than pattern-matching claims across the whole territory. That
                    # distinction matters: the VFX uplift genuinely stacks with standard
                    # AVEC and genuinely does not stack with AVEC (Enhanced/IFTC), so a
                    # UK report can carry both statements truthfully and a check that
                    # merely spotted both shapes would fail correct data.
                    "stackingRelationship": stacking["relationship"],
                    "stacksWith": stacking["primary"],
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
                # A territory whose rebate cannot be computed used to vanish from
                # this section entirely, so a producer who chose three saw two and
                # was left to guess which. Canada is the ordinary case: CPTC is
                # calculated on qualified Canadian labour expenditure, which this
                # report does not model, so there is no figure to put in a chart.
                # That is a fact worth stating, not a reason to omit the territory.
                rows = self._territory_incentives.get(territory, [])
                best = best_incentive(rows, self._production_format, self._project_facts) if rows else {}
                budget_scenarios.append({
                    "territory": territory,
                    "programme": prog_name(best) if best else None,
                    "noFinancialsReason": (
                        "No rebate could be computed for this programme from the "
                        "inputs held, so no net position is modelled here. The "
                        "programme's own terms are in the Tax Incentive Analysis "
                        "section."
                    ),
                })
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
                # Raw numerics for chart geometry (display currency)
                "currencySymbol": tf.get("currency_symbol"),
                "totalBudgetValue": tf.get("total_budget_value"),
                "qualifyingSpendValue": tf.get("qualifying_spend_value"),
                "grossRebateValue": tf.get("gross_rebate_value"),
                "netRebateValue": tf.get("net_rebate_value"),
                "netBudgetValue": tf.get("net_budget_value"),
                "rateGrossValue": tf.get("rate_gross_value"),
                "rateNetValue": tf.get("rate_net_value"),
                # The chart reads these to decide whether a rebate bar may be drawn
                # at all. A gold bar stepping the budget down reads as money
                # received, whatever the caption underneath it says, so an
                # unconfirmed incentive must not appear in the confirmed waterfall.
                "incentiveIsConfirmed": tf.get("incentive_is_confirmed", True),
                "incentiveEligibilityStatus": tf.get("incentive_eligibility_status"),
                "incentiveEligibilityLabel": tf.get("incentive_eligibility_label"),
                "potentialIncentive": (
                    tf.get("potential_net_rebate")
                    if tf.get("show_potential_incentive") else None
                ),
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

        return {
            "budgetScenarios": budget_scenarios,
            "paymentTiming": self._build_payment_timing(territories),
        }

    def _build_payment_timing(self, territories: list[str]) -> list[dict]:
        """Certification / payment receipt windows per ranked territory.

        The window itself comes from ``resolve_payment_timing``, the same
        canonical value the territory card, incentive table and executive summary
        render, so this chart can no longer contradict them. The certification
        and payment weeks are still carried for the bar breakdown, which is the
        only thing they are used for. Entries with no verified window are
        omitted: an empty range is not rendered as a confident bar.
        """
        timing: list[dict] = []
        for territory in territories:
            profile = self._get_territory_profile(territory)
            rows = self._territory_incentives.get(territory, [])
            best = best_incentive(rows, self._production_format, self._project_facts) if rows else {}
            canonical = resolve_payment_timing(best, profile)
            if canonical["minMonths"] is None:
                # No verified window. An empty range is not rendered as a
                # confident bar, and a bar is the only thing this chart says.
                continue
            profile = profile or {}
            cert_min = to_float(profile.get("cert_weeks_min"))
            cert_max = to_float(profile.get("cert_weeks_max"))
            pay_min = to_float(profile.get("payment_weeks_min"))
            pay_max = to_float(profile.get("payment_weeks_max"))
            timing.append({
                "territory": territory,
                # The canonical window and its rendered label, so the chart and
                # the territory card cannot disagree.
                "paymentTiming": canonical,
                "label": canonical["label"],
                "minMonths": canonical["minMonths"],
                "maxMonths": canonical["maxMonths"],
                "certWeeksMin": cert_min,
                "certWeeksMax": cert_max,
                "paymentWeeksMin": pay_min,
                "paymentWeeksMax": pay_max,
                # total window from completion to cash, when both known
                "totalWeeksMin": (
                    (cert_min or 0) + (pay_min or 0)
                    if (cert_min is not None or pay_min is not None) else None
                ),
                "totalWeeksMax": (
                    (cert_max or 0) + (pay_max or 0)
                    if (cert_max is not None or pay_max is not None) else None
                ),
                "sourceQuality": profile.get("bankability_source_quality"),
                "suspended": bool(profile.get("bankability_suspended")),
            })
        return timing

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
                best = best_incentive(rows, self._production_format, self._project_facts)
                summary["recommendedTerritoryPaymentSpeed"] = resolve_payment_timing(
                    best, self._get_territory_profile(top),
                )["label"]

            # Pre-computed financial headline
            tf = self._territory_financials.get(top)
            if tf:
                summary["recommendedTerritoryRebate"] = (
                    tf.get("net_rebate")
                    or tf.get("gross_rebate")
                )
                summary["headlineNetBudget"] = tf.get("headline_net_budget")

        # Canonical schedule. This field was named shootDays but held weeks, and
        # the template printed it as "N wk shoot", so the two schedule figures in
        # the report had no stated relationship to each other.
        script_days = None
        stats = self._build_script_intelligence() or {}
        if isinstance(stats, dict):
            script_days = stats.get("estShootingDays")
        schedule = resolve_schedule(self.datasets.get("_shoot_weeks"), script_days)
        summary["schedule"] = schedule
        summary["shootWeeks"] = schedule["shootWeeks"]
        # Retained under its historical name for the Excel export and any stored
        # report still being read by an older client. Same value, correct type.
        summary["shootDays"] = schedule["shootWeeks"]

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
        self._inject_eligibility_first_step(summary)
        self._inject_must_film_in(summary, territories)

        return summary

    def _inject_must_film_in(self, summary: dict, territories: list[str]) -> None:
        """Say back the commitment the producer declared, and how it was treated.

        ``must_film_in`` was read into the request schema, prepended to the analysed
        territories, and then never mentioned again. The producer told us the one
        thing that is not negotiable about this production and the report answered
        as though every territory in it were an open option — which reads as though
        the constraint was ignored, whether or not it was.

        Three things have to be stated, because each is a different answer:
        the commitment was analysed and leads the ranking; it was analysed but
        something else scored higher, so the ranking is advisory here rather than a
        recommendation; or it could not be modelled at all, and why.
        """
        declared = self.datasets.get("_must_film_in")
        if not declared:
            return

        summary["mustFilmIn"] = declared

        # Where it was actually analysed. A commitment to the United States is
        # modelled as one of its states; a commitment to Scotland as the UK.
        analysed_as = self._territory_substitutions.get(declared, declared)
        if analysed_as != declared:
            summary["mustFilmInAnalysedAs"] = analysed_as

        unanalysed = {
            u.get("territory") for u in (self._unanalysed_territories or [])
        }

        if analysed_as in territories:
            leads = territories[0] == analysed_as
            where = (
                f"{declared}"
                if analysed_as == declared
                else (
                    f"{declared}, modelled under {analysed_as} because that is the "
                    f"level the incentive exists at"
                )
            )
            if leads:
                note = (
                    f"You told us this production must film in {where}. It is the "
                    f"territory these figures are built around; the others are shown "
                    f"for comparison, not as alternatives to it."
                )
            else:
                note = (
                    f"You told us this production must film in {where}. It is analysed "
                    f"here, but {territories[0]} scores higher on the priorities you "
                    f"set. Plan against {analysed_as}; the ranking above is what the "
                    f"commitment costs, not a recommendation to break it."
                )
        elif declared in unanalysed or analysed_as in unanalysed:
            reason = next(
                (
                    u.get("reason")
                    for u in (self._unanalysed_territories or [])
                    if u.get("territory") in (declared, analysed_as)
                ),
                None,
            )
            note = (
                f"You told us this production must film in {declared}, and it could "
                f"not be modelled here. {reason or ''}".strip()
            )
        else:
            note = (
                f"You told us this production must film in {declared}. No incentive "
                f"programme on record could be matched to it, so no rebate is modelled "
                f"for the commitment. The territories below are costed on their own "
                f"terms and do not assume you are free to move."
            )

        summary["mustFilmInNote"] = note
        key_flags = summary.setdefault("keyFlags", [])
        if not any("must film in" in f.lower() for f in key_flags):
            key_flags.insert(0, note)

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

    def _inject_eligibility_first_step(self, summary: dict) -> None:
        """Put verification before any action that spends money on programme access.

        The report was telling producers to establish a qualifying production entity
        and file for certification under a programme this project had not been
        confirmed eligible for. Those steps cost money — company formation, legal,
        accountant — and the eligibility question is free to ask. Ordering them the
        other way round asks a producer to spend against an assumption we have
        explicitly told them elsewhere is unverified.

        Deterministic and prepended, so it leads regardless of what the narrative
        proposed.
        """
        unresolved = [
            est for est in (self._built_incentive_estimates or [])
            if est.get("incentiveIsConfirmed") is False
        ]
        if not unresolved:
            return

        lead = unresolved[0]
        territory = lead.get("territory") or "the recommended territory"
        programme = lead.get("program") or "the incentive programme"
        timeline = summary.setdefault("actionTimeline", [])
        if not isinstance(timeline, list):
            return

        already = " ".join(
            str(item.get("action", "")) for item in timeline if isinstance(item, dict)
        ).lower()
        if "confirm" in already and "eligib" in already:
            return

        fmt = (self._production_format or "this project").lower()
        timeline.insert(0, {
            "action": (
                f"Confirm that this {fmt} is eligible for {programme} with the "
                f"{territory} film commission or programme administrator"
            ),
            "note": (
                "Do this before relying on the incentive or incurring costs "
                "associated with programme qualification, such as forming a "
                "qualifying entity or filing for certification. Those steps only "
                "pay off if the format is accepted."
            ),
        })

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

        # FIX-03 Stage 1. Format is a criterion, not a tiebreak.
        #
        # A micro-budget supernatural short was offered eight comparables, every
        # one of them a feature, because selection scored territory and genre and
        # never asked what the production was. Two rules:
        #
        #   recorded and different  -> discarded outright, before scoring, so no
        #                              amount of territory or genre affinity can
        #                              bring an out-of-format title back
        #   not recorded            -> kept, marked unverified, and scored below
        #                              any row whose format is confirmed
        #
        # The second rule is the stopgap part. Discarding nulls would empty the
        # section on today's data, which tells a producer less than a labelled
        # list does. It closes when the curated dataset lands in Stage 2.
        wanted_format = canonical_format(self._production_format)

        scored: list[tuple[float, dict]] = []
        for row in comparables:
            if not isinstance(row, dict):
                continue
            title = (row.get("title") or "").strip()
            if not title:
                continue

            comp_format = canonical_format(row.get("format"))
            format_known = bool(comp_format)
            if wanted_format and format_known and comp_format != wanted_format:
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

            # Format (+4 confirmed match). Weighted above territory, because a
            # feature shot in the same territory is a weaker comparable for a
            # short than a short shot elsewhere — the section exists to show what
            # productions like this one did.
            if wanted_format and format_known:
                score += 4.0

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

            # Source — omit if empty/N/A, otherwise scrub legally-suppressed
            # provider attributions (e.g. TMDB) before it reaches the report.
            comp_source = (row.get("source") or "").strip()
            if comp_source.lower() in ("", "n/a", "none"):
                comp_source = ""
            else:
                comp_source = clean_source(comp_source)

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
                "format": comp_format,
                # Rendered beside the row. A comparable whose format nobody has
                # recorded is not the same claim as one confirmed to match, and
                # the reader is the one deciding how much weight to give it.
                "formatVerified": format_known,
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
        selected = [comp for _, comp in scored[:10]]

        # Second pass. The gate above runs inside the scoring loop, where a future
        # edit could reorder or short-circuit past it; this one runs on the final
        # list, immediately before it leaves the method, and answers one question
        # about each row that survived. Cheap, and it is the assertion the
        # acceptance criterion is actually written against.
        if wanted_format:
            kept: list[dict] = []
            for comp in selected:
                comp_fmt = canonical_format(comp.get("format"))
                if comp_fmt and comp_fmt != wanted_format:
                    logger.warning(
                        "Dropped out-of-format comparable %r (%s) from a %s report",
                        comp.get("title"), comp_fmt, wanted_format,
                    )
                    continue
                kept.append(comp)
            selected = kept

        return selected

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
            best_month_numbers: list[int] = []
            for m in range(1, 13):
                w = weather_index.get((territory_lower, m))
                if not w:
                    continue
                storm = str(w.get("storm_risk") or "").lower()
                rainfall = float(w.get("avg_rainfall_mm") or 0)
                if storm in ("none", "low", "") and rainfall < 80:
                    best_month_numbers.append(m)
                    best_months.append(calendar.month_name[m])

            # No truncation. This used to end `best_months[:4]`, which is not "the four
            # best months" — the loop above runs January→December, so it is "the
            # earliest four acceptable months". For the UK, months 3-9 qualify and the
            # slice printed March-June, cutting August (50mm rain, low storm risk,
            # exterior score 85). South Africa was worse: qualifying months are 4-9 and
            # August scores 93, the second-best month of its year, yet the slice printed
            # April-July.
            #
            # That truncation is what produced the contradiction downstream. The
            # narrative model was handed the shortened list and did what any reader
            # would do with four consecutive month names — read them as a contiguous
            # window and describe the August shoot against it. The model was faithfully
            # describing a list that had been silently cut; fixing the prose alone
            # would have left both territories' shoot windows understated in the data.
            entry["bestMonths"] = best_months if best_months else ["N/A"]
            entry["bestMonthNumbers"] = best_month_numbers

            # Whether the shoot is inside that window is now decided here, not inferred
            # from the rendered list by the narrative model. Every surface reads this
            # one verdict, and the cross-section validator fails the report if the
            # prose contradicts it.
            window = classify_shoot_window(shoot_months, best_month_numbers)
            entry["shootWindowVerdict"] = window["verdict"]
            entry["shootWindowLabel"] = window["label"]
            entry["optimalWindowDisplay"] = window["optimalWindowDisplay"]
            entry["shootWindowMonthsInside"] = window["monthsInside"]
            entry["shootWindowMonthsOutside"] = window["monthsOutside"]
            entry["shootWindowPartialOverlap"] = window["partialOverlap"]
            entry["shootWindowOverlap"] = window["verdict"] in (
                SHOOT_WINDOW_OUTSIDE, SHOOT_WINDOW_ADJACENT,
            )
            # Stated deterministically, above the narrative sentence, so the reader
            # gets the computed answer whether or not the prose agrees with it. This
            # schema field existed and was never populated by any producer code, which
            # is how the window claim came to live only in ungoverned prose.
            entry["shootWindowRisk"] = self._shoot_window_sentence(territory, window)

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

    @staticmethod
    def _shoot_window_sentence(territory: str, window: dict) -> str | None:
        """The computed shoot-window verdict, in words.

        Composed here from the classifier's own output so the sentence and the verdict
        cannot drift apart. Returns None when there is nothing to state, rather than a
        hedge — a sentence saying the window is unknown adds nothing the absent row
        does not already say.
        """
        verdict = window.get("verdict")
        optimal = window.get("optimalWindowDisplay")
        shoot = window.get("shootMonthDisplay")
        if verdict == SHOOT_WINDOW_UNKNOWN or not optimal or not shoot:
            return None

        if verdict == SHOOT_WINDOW_INSIDE:
            return (
                f"Your {shoot} shoot falls inside {territory}'s optimal window "
                f"({optimal})."
            )
        if window.get("partialOverlap"):
            inside = format_month_ranges(window.get("monthsInside"))
            outside = format_month_ranges(window.get("monthsOutside"))
            return (
                f"Your {shoot} shoot straddles {territory}'s optimal window "
                f"({optimal}): {inside} falls inside it, {outside} does not."
            )
        if verdict == SHOOT_WINDOW_ADJACENT:
            return (
                f"Your {shoot} shoot falls outside {territory}'s optimal window "
                f"({optimal}), immediately either side of it."
            )
        return (
            f"Your {shoot} shoot falls outside {territory}'s optimal window "
            f"({optimal})."
        )

    # ── Funding Opportunities ──────────────────────────────────────────────

    def _build_funding_opportunities(self) -> list[dict]:
        """Build fundingOpportunities from the deterministic grants matcher
        plus territory-matched festivals.

        Grants go through reports/matching.match_grants (handoff
        grants_matcher.py, PRO spec Section 07): format / deadline /
        staleness / nationality-with-no-route / budget-bounds hard gates,
        additive location/genre/format/budget signals, plain-English
        why-matched strings and prominence badges.
        """
        grants = self.datasets.get("grants", [])
        festivals = self.datasets.get("festivals", [])
        selected = {t.lower() for t in self._territory_names}

        # Script-origin territory (parser's dominant location country)
        script_origin = None
        primary = getattr(self.script_analysis, "primary_location", None)
        if primary:
            t = resolve_territory(str(primary))
            script_origin = t.label if t else str(primary)

        # Budget in USD for the fund bounds gate (approximate FX is fine here —
        # bounds are coarse eligibility windows)
        budget_usd = None
        if self._budget_gbp:
            budget_usd = self._budget_gbp * STATIC_FX_TO_GBP.get("USD", 1.27)

        production = {
            "format": self._production_format or "",
            "genres": sorted(self._production_genres()),
            "budget_usd": budget_usd,
            "home_country": self.request_metadata.get("country") or "",
            "ranked_territories": list(self._territory_names),
            "script_origin": script_origin,
        }
        grant_matches, grant_flags = match_grants(grants, production)
        for flag in grant_flags:
            logger.info(
                "grants matcher admin flag: %s — %s [%s]",
                flag.get("fund_name"), flag.get("detail"), flag.get("flag"),
            )

        opportunities: list[dict] = []
        for m in grant_matches[:10]:
            g = m["grant"]
            notes = (
                g.get("max_amount")
                or g.get("amount_description")
                or ""
            )
            if notes and not notes.lower().startswith("up to") and _re.search(r'[£$€]\s*\d', notes):
                notes = f"Up to {notes}"
            opportunities.append({
                "name": g.get("title") or g.get("fund_name") or "",
                "type": "Fund",
                "territory": g.get("territory") or "",
                "deadline": (
                    g.get("application_deadline") or g.get("deadline") or ""
                ),
                "notes": notes,
                "badges": m["badges"],
                "whyMatched": "; ".join(m["signals"]),
                "matchScore": m["score"],
            })

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

        return opportunities

    # ── Script Intelligence ────────────────────────────────────────────────

    def _computed_complexity(self) -> str | None:
        """Complexity scored from the parsed script, or None if it was not parsed.

        Lives here rather than being read inline so there is one answer to "where does
        complexity come from" — the parser when it read the file, the narrative model
        only when it did not.
        """
        challenges = getattr(self.script_analysis, "challenges", None)
        value = getattr(challenges, "deterministic_complexity", None)
        if value in ("Low", "Medium", "High", "Very High"):
            return value
        return None

    def _build_script_intelligence(self) -> dict | None:
        """Deterministic parsed-script stats for the Script Intelligence page.

        Everything here is counted from the script by the parser — the
        narrative reading lives in the AI-filled genre/tone/scale/complexity
        and executive summary fields. Absent parser fields stay None and the
        template hides the corresponding row.
        """
        sa = self.script_analysis
        if sa is None:
            return None

        def _get(obj: Any, attr: str) -> Any:
            return getattr(obj, attr, None) if obj is not None else None

        challenges_obj = _get(sa, "challenges")

        def _stat(attr: str) -> Any:
            """A counted script field, from wherever it actually lives.

            Every counted field on this page was read as ``sa.<attr>`` and every one of
            them is declared on ``sa.challenges``, so all of them resolved to None:
            scene count, the interior/exterior split, day/night, languages and named
            locations were absent from the rendered page while the two fields that do
            sit on ``sa`` directly (both from productionScale) rendered fine. That is
            consistent with the EJE report's Script Intelligence page, which showed
            estimated shooting days and principal cast and nothing else.
            ``challenges`` is checked first and the top level second, so a flattened
            object from another caller still resolves.
            """
            value = _get(challenges_obj, attr)
            return value if value is not None else _get(sa, attr)

        total = _stat("total_scenes")
        day = _stat("day_scenes")
        night = _stat("night_scenes") or _stat("nightSceneCount")
        other = None
        if total is not None and day is not None and night is not None:
            other = max(0, total - day - night)

        named_locations: list[dict] = []
        raw_locations = _stat("named_locations") or {}
        if isinstance(raw_locations, dict) and raw_locations:
            ranked = sorted(raw_locations.items(), key=lambda kv: -(kv[1] or 0))
            for name, scenes in ranked[:5]:
                pct = round(100 * scenes / total) if total else None
                named_locations.append({"name": name, "scenes": scenes, "pct": pct})

        production_scale = _get(sa, "productionScale")

        # Challenge lines, each traceable to the parsed input that justifies it.
        #
        # FIX-06. Every line here is read as a finding about this screenplay, so a
        # line may only say what its input actually establishes. One of these did
        # not: "Special permits required — long lead times in every candidate
        # territory" was emitted from a single script-level boolean, and asserted two
        # things that boolean cannot support. "Long lead times" has no backing field
        # anywhere in the schema. "In every candidate territory" is a claim about the
        # ranked territories, and this method reads no territory data at all — it
        # would have printed identically for a one-territory report and for a
        # ten-territory one, and identically whether or not any of those territories
        # had a recorded permitting regime.
        #
        # The counted lines below were already sound: a scene count is evidence for a
        # statement about scene counts. The boolean-driven ones are now scoped to what
        # a boolean can carry — that the script contains such material, and that it
        # needs planning — with no territory-wide or lead-time assertion attached.
        # Each is paired with its evidence so an unsupported line cannot be added
        # without also naming what supports it.
        challenge_specs: list[tuple[Any, str]] = []

        if night and night > 0:
            challenge_specs.append((
                night,
                f"{night} night scenes — turnaround management and lighting budget",
            ))
        music_scenes = _stat("music_performance_scenes")
        if music_scenes and music_scenes > 0:
            challenge_specs.append((
                music_scenes,
                f"{music_scenes} live music performance scenes — clearance and "
                f"licensing counsel needed before pre-production",
            ))
        crowd = _stat("crowd_scenes")
        if crowd and crowd > 0:
            challenge_specs.append((
                crowd,
                f"{crowd} large-crowd scenes — extras logistics and security planning",
            ))
        stunts = _stat("stunt_sequences")
        if stunts and stunts > 0:
            challenge_specs.append((
                stunts,
                f"{stunts} stunt sequences — stunt coordination and insurance",
            ))
        if _get(challenges_obj, "waterWork"):
            challenge_specs.append((
                True, "Water work — safety supervision and specialist equipment",
            ))
        if _get(challenges_obj, "specialPermits"):
            # Scoped to the script evidence. Which permits, and what they cost in
            # lead time, is a territory question this section cannot answer.
            challenge_specs.append((
                True,
                "Scenes set in locations that typically require permits — confirm "
                "the permitting route with each territory's film office",
            ))
        if _get(challenges_obj, "animalWrangling"):
            challenge_specs.append((
                True, "Animal work — welfare compliance and specialist handlers",
            ))
        languages = _stat("languages")
        if isinstance(languages, list) and len(languages) > 1:
            challenge_specs.append((
                languages,
                f"Dialogue in {len(languages)} languages "
                f"({', '.join(str(x) for x in languages[:4])}) — specialist casting "
                f"and translation review before principal photography",
            ))
        vfx_scenes = _stat("vfxHeavySceneCount")
        if vfx_scenes and vfx_scenes > 0:
            challenge_specs.append((
                vfx_scenes,
                f"{vfx_scenes} VFX-heavy scenes — on-set supervision matched to "
                f"post-production compositing",
            ))

        challenges = [text for evidence, text in challenge_specs if evidence]

        interior_pct = _stat("interior_pct")
        exterior_pct = _stat("exterior_pct")

        return {
            "sceneCount": total,
            "interiorPct": round(interior_pct) if interior_pct is not None else None,
            "exteriorPct": round(exterior_pct) if exterior_pct is not None else None,
            "dayScenes": day,
            "nightScenes": night,
            "otherScenes": other,
            "estShootingDays": _get(production_scale, "estimatedShootingDays"),
            "principalCast": _get(production_scale, "principalCast"),
            "supportingCast": _get(production_scale, "supportingCast"),
            "crowdScenes": crowd,
            "musicPerformanceScenes": music_scenes,
            "languages": languages,
            "namedLocations": named_locations,
            "productionChallenges": challenges,
            # Provenance. "parsed" means the counts above were read from the screenplay
            # text; "model_estimate" means no scene heading could be parsed and they are
            # the aggregated LLM values. Two reports from one screenplay previously
            # disagreed with nothing on the page indicating which was which, because
            # neither was a parse.
            "metricsSource": _stat("metrics_source"),
            "parserVersion": _stat("parser_version"),
            "mixedScenes": _stat("mixed_scenes"),
            "continuationHeadings": _stat("continuation_headings"),
            "distinctLocations": _stat("distinct_locations"),
            "complexityDrivers": _stat("complexity_drivers"),
        }

    # ── Festival & Distributor Recommendations ─────────────────────────────

    # Production format → festival/distributor eligible_formats vocabulary
    # Superseded by canonical_format(). Kept only as documentation of what this
    # hand-maintained map used to cover, because what it MISSED is the point: the
    # wizard offers "Short" and "Animated Feature", and neither was a key. A miss
    # returned no festivals at all, so a short film was told "no festival matches
    # for this production's format and timing" while 107 festivals in the dataset
    # accept shorts. A second vocabulary that has to be kept in step with the first
    # will always drift; app/core/formats.py is the one both sides read.
    _LEGACY_FORMAT_TO_ELIGIBLE = {
        "Feature Film": "feature",
        "Documentary": "documentary",
        "Docuseries": "documentary",
        "Short Film": "short",
        "TV Pilot": "tv_series",
        "TV Series": "tv_series",
        "Limited Series": "tv_series",
        "Mini-Series": "tv_series",
        "Animation Series": "animation",
    }

    # Format words that can appear in a festival's free-text deadline note. The
    # notes are curator prose, not structured data, so a festival that accepts
    # shorts may still describe only its FEATURE deadline.
    _DEADLINE_FORMAT_WORDS = {
        "feature": "feature",
        "features": "feature",
        "documentary": "documentary",
        "documentaries": "documentary",
        "short": "short",
        "shorts": "short",
        "animation": "animation",
        "animated": "animation",
    }

    def _deadline_for_format(self, text: str | None) -> str | None:
        """Return the deadline note only when it plausibly applies to this format.

        AFRIFF's note reads "Feature submissions typically close ~2 months before
        the November festival". Printed in a short film's report under a heading
        promising matches on format, that reads as this production's deadline. It is
        someone else's deadline.

        A note that names formats and does not name ours is withheld and replaced
        with a statement of what we actually know. A note that names no format at all
        is left alone: a general note is general.
        """
        if not text:
            return None
        lowered = str(text).lower()
        mentioned = {
            token for word, token in self._DEADLINE_FORMAT_WORDS.items()
            if word in lowered
        }
        if not mentioned:
            return text  # says nothing about format, so it constrains nothing

        wanted = canonical_format(self._production_format)
        if wanted and wanted in mentioned:
            return text

        named = ", ".join(sorted(mentioned))
        return (
            f"Submission deadline not independently verified for this format. The "
            f"programme's recorded note describes {named} submissions only."
        )

    def _production_genres(self) -> set[str]:
        raw = self.request_metadata.get("genre") or []
        if isinstance(raw, str):
            raw = [raw]
        return {g.strip().lower() for g in raw if g}

    def _script_countries(self) -> set[str]:
        """Countries the script itself is set in (from parsed locations)."""
        countries: set[str] = set()
        locations = getattr(self.script_analysis, "locations", None) or []
        for loc in locations:
            for attr in ("territory", "country"):
                val = getattr(loc, attr, None)
                if val:
                    t = resolve_territory(val)
                    countries.add(t.label if t else val)
        return countries

    def _declared_audience_fields(self) -> dict:
        """Intake audience/representation fields — declared-only, never inferred."""
        raw_ta = self.request_metadata.get("target_audience") or []
        if isinstance(raw_ta, str):
            raw_ta = [raw_ta]
        return {
            "target_audience": [str(a) for a in raw_ta if a],
            "audience_segments": [
                str(a) for a in (self.request_metadata.get("audience_segments") or []) if a
            ],
            "audience_skew": self.request_metadata.get("audience_skew"),
            "representation_gender": self.request_metadata.get("representation_gender"),
            "representation_minority": [
                str(m) for m in (self.request_metadata.get("representation_minority") or []) if m
            ],
        }

    def _completion_date(self):
        """Estimated completion for the festival timing gate, when derivable."""
        from datetime import date as _date

        start_raw = self.request_metadata.get("filming_start_date")
        if not start_raw:
            return None
        try:
            start = _date.fromisoformat(str(start_raw)[:10])
        except (ValueError, TypeError):
            return None
        duration = self.request_metadata.get("filming_duration")
        shoot_days = None
        scale = getattr(self.script_analysis, "productionScale", None)
        if scale is not None:
            shoot_days = getattr(scale, "estimatedShootingDays", None)
        try:
            completion, _, _ = estimate_completion_date(
                start,
                float(duration) if duration else None,
                estimated_shoot_days=shoot_days,
            )
            return completion
        except (ValueError, TypeError):
            return None

    def _build_festival_recommendations(self, territories: list[str]) -> list[dict]:
        """Festival matching via the platform's deterministic engine
        (reports/matching.py — ported from the handoff's
        festival_distributor_matcher.py). Format and timing are hard gates;
        representation is strict opt-in; audience is declared-only.
        """
        eligible_format = canonical_format(self._production_format)
        if not eligible_format:
            return []

        declared = self._declared_audience_fields()
        matches = match_festivals(
            self.datasets.get("festivals", []),
            genres=sorted(self._production_genres()),
            representation_gender=declared["representation_gender"],
            representation_minority=declared["representation_minority"],
            production_format=eligible_format,
            completion_date=self._completion_date(),
            # Comparable-production festival history is not yet carried on
            # comparable_productions rows — declared data gap, never guessed.
            comparable_production_festivals=None,
            target_audience=declared["target_audience"],
            audience_segments=declared["audience_segments"],
        )

        entries: list[dict] = []
        for m in matches[:5]:
            fest = m.festival
            reasons = [r for r in m.reasons if not r.startswith("Tier:")]
            entries.append({
                "name": fest.get("name"),
                "location": fest.get("location") or fest.get("territory"),
                "tier": fest.get("tier"),
                "oscarQualifying": bool(fest.get("oscar_qualifying")),
                "deadlinePattern": self._deadline_for_format(
                    fest.get("deadline_pattern") or fest.get("submission_deadline")
                ),
                "eligibleFormats": fest.get("eligible_formats") or [],
                "matchScore": m.score,
                "matchedOn": reasons,
                "whyMatched": ". ".join(reasons) + "." if reasons else "",
                # Rendered as a link by both surfaces. A festival recommendation
                # the reader cannot open is a name they have to go and search for.
                "sourceUrl": fest.get("website_url"),
            })
        return entries

    def _build_distributor_recommendations(
        self, festival_recs: list[dict]
    ) -> list[dict]:
        """Distributor matching via the deterministic engine. MUST run after
        festivals: the scouts-matched-festival +4 linkage is the strongest
        signal in the system and only works with the matched names. Only
        confirmed-active distributors reach this method (gated at load).
        """
        declared = self._declared_audience_fields()
        production_territories = list(self._territory_names) + sorted(
            self._script_countries()
        )
        matches = match_distributors(
            self.datasets.get("distributors", []),
            genres=sorted(self._production_genres()),
            representation_gender=declared["representation_gender"],
            representation_minority=declared["representation_minority"],
            matched_festival_names=[f["name"] for f in festival_recs],
            budget_tier=None,
            target_audience=declared["target_audience"],
            audience_segments=declared["audience_segments"],
            audience_skew=declared["audience_skew"],
            production_territories=production_territories,
            production_format=self._production_format,
        )

        recommended_names = {f["name"] for f in festival_recs}
        entries: list[dict] = []
        for m in matches[:4]:
            dist = m.distributor
            scouted = sorted(
                set(dist.get("scouts_festivals") or []) & recommended_names
            )
            reasons = [r for r in m.reasons if not r.startswith("Budget fit:")]
            entries.append({
                "name": dist.get("name"),
                "primaryMarket": dist.get("primary_market"),
                "territoryReach": dist.get("territory_reach") or [],
                "rightsType": dist.get("rights_type"),
                "budgetTierFit": dist.get("budget_tier_fit"),
                "submissionProcess": dist.get("submission_process"),
                "scoutsRecommendedFestivals": scouted,
                "matchScore": m.score,
                "matchedOn": reasons,
                "whyMatched": ". ".join(reasons) + "." if reasons else "",
                "verified": bool(dist.get("verified_at")),
                "sourceUrl": dist.get("source_url"),
            })
        return entries

    def _build_script_origin_callout(self, territories: list[str]) -> dict | None:
        """Callout for the script's primary setting when it isn't ranked.

        A script set in Lagos while Nigeria has no rankable incentive must be
        addressed honestly — outside the ranking, with its non-financial case
        (currency advantage, crew tier, authenticity) stated from data.
        """
        sa = self.script_analysis
        if sa is None:
            return None
        primary = getattr(sa, "primary_location", None)
        locations = getattr(sa, "locations", None) or []

        # Resolve the primary setting to a canonical territory
        origin_label: str | None = None
        for candidate in [primary] + [
            getattr(loc, "territory", None) or getattr(loc, "country", None)
            for loc in locations
            if getattr(loc, "isMainLocation", False)
        ]:
            if not candidate:
                continue
            t = resolve_territory(str(candidate))
            if t:
                origin_label = t.label
                break
        if not origin_label:
            return None

        ranked = {t.lower() for t in territories}
        if origin_label.lower() in ranked:
            return None  # origin is ranked normally — no callout needed

        # Scene share of the origin territory, when parsed counts exist
        total = getattr(sa, "total_scenes", None)
        origin_scenes = 0
        for loc in locations:
            loc_terr = getattr(loc, "territory", None) or getattr(loc, "country", None)
            t = resolve_territory(str(loc_terr)) if loc_terr else None
            if t and t.label == origin_label:
                origin_scenes += getattr(loc, "frequency", 0) or 0
        scenes_pct = round(100 * origin_scenes / total) if total and origin_scenes else None

        # Incentive reality for the origin
        rows = self._territory_incentives.get(origin_label, [])
        best = best_incentive(rows, self._production_format, self._project_facts) if rows else {}
        has_programme = bool(rows) and (best.get("status") or "").lower() == "active" \
            and not is_zero_rate(best.get("rate_gross"), best.get("rate_net"))

        profile = self._get_territory_profile(origin_label)

        return {
            "territory": origin_label,
            "scenesPct": scenes_pct,
            "hasIncentiveProgramme": has_programme,
            "programmeNote": (
                (best.get("program_name") or best.get("program"))
                if has_programme else "No formal production incentive programme"
            ),
            "currencyAdvantage": self._get_currency_score(origin_label),
            "crewDepthTier": self._profile_tier_label(profile, "crew_depth_tier"),
        }

    # ── Territory Deep Dives ───────────────────────────────────────────────

    def _build_territory_deep_dives(self, territories: list[str]) -> list[dict]:
        """Build territoryDeepDives shells. Narrative content filled by AI."""
        dives: list[dict] = []

        for territory in territories:
            rows = self._territory_incentives.get(territory, [])
            if not rows:
                continue

            best = best_incentive(rows, self._production_format, self._project_facts)

            # Rebate rate string
            rebate_str = format_rate(
                best.get("rate_gross"), best.get("rate_net"),
            ) or "N/A"

            # Estimated rebate from pre-computed financials
            tf = self._territory_financials.get(territory)
            if tf:
                estimated_rebate = (
                    tf.get("net_rebate")
                    or tf.get("gross_rebate")
                    or "See programme terms"
                )
            else:
                estimated_rebate = "See programme terms"

            dive: dict = {
                "name": territory,
                "country": territory,
                "score": None,  # set after score computation
                # "rebate" is the PROGRAMME's headline rate, a fact about the
                # programme. It is kept under the old key so nothing downstream
                # breaks, and re-labelled in the templates, because reading "Rebate
                # 30%" next to "Est. rebate £0" invites the reader to conclude the
                # £0 is a mistake rather than a statement about eligibility.
                "rebate": rebate_str,
                "headlineRate": rebate_str,
                "estimatedRebate": estimated_rebate,
                # The same project-specific result the tax-incentive section reads,
                # so the two sections cannot show different eligibility for the same
                # programme. Read, never re-derived here.
                "incentiveEligibilityStatus": (tf or {}).get("incentive_eligibility_status"),
                "incentiveEligibilityLabel": (tf or {}).get("incentive_eligibility_label"),
                "incentiveIsConfirmed": (tf or {}).get("incentive_is_confirmed", True),
                "confirmedIncentive": (
                    estimated_rebate if (tf or {}).get("incentive_is_confirmed", True) else None
                ),
                "potentialIncentive": (
                    (tf or {}).get("potential_net_rebate")
                    if (tf or {}).get("show_potential_incentive") else None
                ),
                # AI-filled narratives
                "infrastructure": None,
                "keyAdvantages": None,
                "keyRisks": None,
                # Driven solely by the cultural_test_required DB column (no heuristic):
                # True → fixed "High (85%)" likelihood estimate, False/NULL → "N/A".
                # Always a string — the frontend renders this under a "Likelihood" label.
                "culturalTestLikelihood": (
                    "High (85%)" if best.get("cultural_test_required") is True else "N/A"
                ),
                "adminComplexity": best.get("admin_complexity") or "Medium",
            }

            # Payment timing, same canonical value as the card and the table.
            dive["paymentTiming"] = resolve_payment_timing(
                best, self._get_territory_profile(territory),
            )
            dive["paymentSpeed"] = dive["paymentTiming"]["label"]

            dives.append(dive)

        return dives

    # ── Attributions ───────────────────────────────────────────────────────

    def _build_attributions(self, territories: list[str]) -> list[dict]:
        """Build the report's data-source provenance line.

        The former per-territory crew/cast wage citations (Bureau of Labor
        Statistics, ONS, etc.) were removed with crew-cost day-rates in 2026-07
        — they no longer back any figure in the report, so citing them would be
        a stale, misleading attribution. Instead we state the provenance of the
        data the report actually uses: incentive, grant, festival and
        distributor records, each carrying its own source and verification.
        """
        return [
            {
                "territory": "All territories",
                "text": (
                    "Incentive, grant, festival and distributor figures are sourced "
                    "from official government film offices and programme portals; "
                    "each record carries its own verification status and source. "
                    "Rebate figures are estimates that depend on the production's "
                    "actual qualifying spend and final approval by the relevant "
                    "authority."
                ),
            }
        ]

    # ── Section Explainers ─────────────────────────────────────────────────

    def _inject_section_explainers(self, report: dict) -> None:
        """Inject hardcoded section explainers per v3 spec."""
        budget_currency = self._budget_currency

        explainers = {
            "executive_summary": (
                "How we read your script: We identified scene counts, "
                "interior/exterior ratios, named locations, and languages "
                "actually spoken to build the analysis below. "
                "All figures are estimates — verify with qualified professionals."
            ),
            "location_strategy": (
                f"How we score territories: Each territory is rated 0-100 across six "
                f"dimensions (Cost Efficiency, Crew Depth, Infrastructure, Incentive "
                f"Strength, Currency Advantage, Incentive Reliability), weighted by your "
                f"stated production priority. Your budget currency ({budget_currency}) is "
                f"compared against each territory's local currency to calculate "
                f"purchasing power advantage. "
                # The rule, stated in the report rather than only in the code. A reader
                # who is told not to rely on a rebate and then sees the territory ranked
                # first because of it has no way to know which the report meant.
                f"Incentive Strength has three states. Where this project is confirmed "
                f"eligible, the dimension carries the programme's computed strength. "
                f"Where it is confirmed ineligible — a format exclusion, or a budget "
                f"below the programme's floor — the dimension scores zero, because "
                f"there is no rebate to value. Where eligibility is unresolved, the "
                f"dimension is not scored and is treated as neutral in the weighted "
                f"total: an incentive this production has not been confirmed able to "
                f"claim neither raises nor lowers where the territory ranks. Any such "
                f"rebate still appears in the incentive section, labelled as "
                f"illustrative."
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
            "financial_readiness": _READINESS_EXPLAINER,
        }

        sa = report.get("scriptAnalysis")
        if isinstance(sa, dict):
            sa["sectionExplainers"] = explainers
        else:
            report["scriptAnalysis"] = {"sectionExplainers": explainers}
        report["sectionExplainers"] = explainers


    def _get_weather_month(self, territory: str, shoot_month: int) -> dict | None:
        """Look up the weather row for a territory/month from the weather dataset.

        Returns the weather dict (avg_rainfall_mm, avg_temp_high_c, etc.) or None
        when no matching row exists.
        """
        weather_data = self.datasets.get("weather", [])
        if not weather_data:
            return None

        territory_lower = territory.lower()
        month = int(shoot_month)
        for w in weather_data:
            if (
                str(w.get("territory", "")).lower() == territory_lower
                and int(w.get("month") or 0) == month
            ):
                return w
        return None

    def _compute_schedule_viability(
        self,
        territory: str,
        shoot_month: int,
        shoot_weeks: float,
        exterior_pct: float,
    ) -> dict:
        """Compute Schedule Viability Score (SVS) for a territory/month combination.

        SVS is 0–100 where higher = more viable for exterior shooting.
        Returns dict with 'svs' and 'contingency_days'.
        """
        month_data = self._get_weather_month(territory, shoot_month)
        if not month_data:
            return {'svs': 50, 'contingency_days': max(1, int(shoot_weeks / 2))}

        daylight = float(month_data.get('avg_daylight_hours') or 0) or 12.0
        rainfall = float(month_data.get('avg_rainfall_mm') or 0) or 50.0
        temp_high = float(month_data.get('avg_temp_high_c') or 0)
        temp_low = float(month_data.get('avg_temp_low_c') or 0)
        temp = (temp_high + temp_low) / 2 if temp_high or temp_low else 15.0

        daylight_factor = max(0.0, min(1.0, (daylight - 8) / 6))
        ext_weight = exterior_pct / 100
        rain_penalty = min(0.4, rainfall / 200)
        temp_penalty = 0.1 if temp < 5 or temp > 38 else 0.0

        svs = int(round(
            (daylight_factor * ext_weight * 100)
            - (rain_penalty * 100)
            - (temp_penalty * 100)
            + ((1 - ext_weight) * 60)
        ))
        svs = max(0, min(100, svs))

        # Contingency multiplier: poor weather = more buffer days needed
        mult = 1.0 if svs >= 75 else 1.5 if svs >= 55 else 2.0 if svs >= 35 else 3.0
        contingency_days = round(shoot_weeks / 2 * mult)

        return {'svs': svs, 'contingency_days': contingency_days}

    # ── Scoring helpers ────────────────────────────────────────────────────

    @staticmethod
    def _compute_reliability(db_row: dict, profile: dict | None = None) -> tuple[int, str]:
        """Compute incentiveReliability score and bankabilityLabel.

        `profile` is the territory_profiles row (curated, human-verified
        payment-timing research) for this territory, if one exists. When it
        carries a trusted source quality, both the label AND this score are
        derived from it instead of the older incentive-row proxy, so the two
        stay consistent with each other.
        """
        reliability = to_float(db_row.get("payment_reliability"))
        timeline_max = to_float(db_row.get("payment_timeline_days_max"))

        label = _compute_bankability_label(reliability, timeline_max, profile=profile)

        if (
            profile
            and not profile.get("bankability_suspended")
            and profile.get("bankability_real_world_confirms") is not False
            and (profile.get("bankability_source_quality") or "").strip()
            in _TRUSTED_BANKABILITY_SOURCE_QUALITY
        ):
            rel_score = {"BANKABLE": 90, "NOT BANKABLE": 15}.get(label, 55)
            return rel_score, label

        # Reliability score (0-100) — legacy proxy, used when no trusted
        # curated profile exists for this territory.
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

    def _get_territory_profile(self, territory: str) -> dict | None:
        """Return the maintained profile row for a territory, if available."""
        if not self._territory_profiles:
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
            profile = self._territory_profiles.get(candidate)
            if isinstance(profile, dict):
                return profile

        territory_lower = territory.lower()
        for profile in self._territory_profiles.values():
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
    def _profile_tier_label(profile: dict | None, key: str) -> str | None:
        if not profile:
            return None
        raw = str(profile.get(key) or "").strip()
        if not raw:
            return None
        return raw.replace("_", " ").title()

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
    def _weighted_score(loc: dict, weights: dict, weather_penalty: int) -> int:
        """Weighted score across the six dimensions, plus the weather penalty.

        Missing dimensions score a neutral 50 rather than zero, so a territory
        is not punished for a dimension the AI declined to refine.
        """
        weighted_sum = 0.0
        for dim, weight in weights.items():
            val = loc.get(dim)
            if isinstance(val, (int, float)):
                weighted_sum += val * weight
            else:
                weighted_sum += 50 * weight
        return max(0, min(100, int(round(weighted_sum)) + weather_penalty))

    @classmethod
    def _rank_territories_provisionally(cls, rankings: list[dict], production_priority: str) -> None:
        """Score and sort *rankings* in place before the AI narrative call.

        All six dimensions are already populated deterministically by
        ``_build_location_rankings``; the AI only refines costEfficiency within
        ±15. Ranking here means the narrative call sees territories in true
        rank order, so the prose cannot disagree with the computed table.

        Non-destructive: ``weatherRiskImpact`` is read but not consumed, so the
        authoritative post-AI ``compute_overall_scores`` still applies it.
        """
        weights = SCORE_WEIGHTS.get(production_priority, SCORE_WEIGHTS["full"])
        for loc in rankings:
            if isinstance(loc, dict):
                loc["score"] = cls._weighted_score(
                    loc, weights, loc.get("weatherRiskImpact", 0) or 0
                )
        rankings.sort(
            key=lambda loc: loc.get("score", 0) if isinstance(loc, dict) else 0,
            reverse=True,
        )

    # Territory-keyed sections that must follow locationRankings order, as
    # (path, key-holding-the-territory-name) pairs.
    _RANK_ORDERED_SECTIONS: tuple[tuple[tuple[str, ...], str], ...] = (
        (("incentiveEstimates",), "territory"),
        (("financialAnalysis", "budgetScenarios"), "territory"),
        (("financialAnalysis", "paymentTiming"), "territory"),
        (("weatherLogistics",), "territory"),
        (("territoryDeepDives",), "name"),
    )

    @classmethod
    def align_sections_to_rankings(cls, report: dict) -> None:
        """Re-sort every territory-keyed section to match locationRankings.

        The AI may refine costEfficiency enough to reorder the ranking after
        the skeleton was built, which would otherwise leave the recommended
        card, budget scenarios and charts leading with a stale territory.
        Sections may legitimately hold territories absent from the ranking
        (e.g. no incentive rows); those keep their relative order at the end.
        """
        rankings = report.get("locationRankings")
        if not isinstance(rankings, list):
            return
        order = {
            loc["name"]: i for i, loc in enumerate(rankings)
            if isinstance(loc, dict) and loc.get("name")
        }
        if not order:
            return
        fallback = len(order)

        for path, name_key in cls._RANK_ORDERED_SECTIONS:
            node: object = report
            for part in path:
                node = node.get(part) if isinstance(node, dict) else None
            if not isinstance(node, list):
                continue
            node.sort(
                key=lambda entry, k=name_key: order.get(
                    entry.get(k) if isinstance(entry, dict) else None, fallback
                )
            )

    @staticmethod
    def _unusable_territories(report: dict) -> set[str]:
        """Territories whose modelled programme fails its own stated thresholds.

        Read from ``incentiveEstimates``, which already carries the verdict, so the
        ranking cannot disagree with the tax-incentive section about the same
        programme. An estimate with no verdict is treated as usable: absent data must
        not silently demote a territory.
        """
        unusable: set[str] = set()
        estimates = report.get("incentiveEstimates")
        if not isinstance(estimates, list):
            return unusable
        for est in estimates:
            if not isinstance(est, dict):
                continue
            if (est.get("programmeEligibility") or {}).get("available") is False:
                if est.get("territory"):
                    unusable.add(est["territory"])
        return unusable

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

            loc["score"] = ReportBuilder._weighted_score(loc, weights, weather_penalty)

        # An unusable programme's territory sorts below every usable one, whatever
        # its score. Score alone put California second on a production whose entire
        # budget was a seventeenth of California's stated minimum qualifying spend:
        # the six scored dimensions measure how good a territory is, not whether this
        # production can use its incentive at all, and no weighting of the former
        # answers the latter. The territory keeps its score and its card, and states
        # why it cannot be recommended.
        unusable = ReportBuilder._unusable_territories(report)
        rankings.sort(
            key=lambda loc: (
                0 if isinstance(loc, dict) and loc.get("name") in unusable else 1,
                loc.get("score", 0) if isinstance(loc, dict) else 0,
            ),
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

        # Keep every territory-keyed section in the same order as the ranking,
        # in case the AI's costEfficiency refinement changed it.
        ReportBuilder.align_sections_to_rankings(report)

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
