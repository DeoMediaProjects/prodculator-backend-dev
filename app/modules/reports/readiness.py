"""Deterministic financial-readiness assessment (handoff §4.1).

No AI. Every figure this module emits traces to a named input and a stated
computation, and every component carries the checks that produced its status.
The verdict is the output of a fixed rubric applied to those statuses — it is
never a judgement, a score the model wrote, or a blend of the two.

Four components are assessed against the already-built report:

``budget_vs_cost_base``
    The production budget against what it actually costs to shoot in the
    recommended territory: the modelled programme's minimum qualifying spend
    (a hard eligibility floor) and the budgets of comparable productions in
    that territory (an empirical cost anchor).

``incentive_confidence``
    How much of the modelled incentive value is confirmed-grade rather than
    estimated. An estimate is confirmed-grade only when the project is eligible for
    the programme in its own production format, the producer qualifies outright, the
    incentive is bankable, the underlying record was verified inside ``STALE_DAYS``,
    and the programme has not expired before delivery. Format eligibility is not
    optional here: it is the dimension that most often decides a short film, and
    omitting it from this list is what let a report call an incentive confirmed
    while the same report said its format eligibility was unverified.

``soft_money_coverage``
    Matched grant/fund money as a share of budget, alongside the modelled
    incentive coverage, leaving the residual that equity or debt must carry.

``timeline_feasibility``
    Completion date against the shoot schedule plus a post-production floor,
    the incentive's certification-to-cash window, and whether the first
    festival cycle is still reachable after delivery.

Usage::

    from app.modules.reports.readiness import compute_financial_readiness

    section = compute_financial_readiness(
        report=report, datasets=datasets, request_metadata=request_metadata,
    )
    if section:
        report["financialReadiness"] = section

The function is pure and report-driven, so it can be re-run after the ranking
settles (see ``ReportValidator.assert_integrity``) without recomputing
anything upstream.
"""
from __future__ import annotations

import logging
import re as _re
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from app.modules.reports.helpers import (
    STALE_DAYS,
    STATIC_FX_TO_GBP,
    parse_money_string,
    prog_name,
    to_float,
)

logger = logging.getLogger(__name__)


# ── Rubric ───────────────────────────────────────────────────────────────────
# Component weights sum to 100. Statuses map to fixed points; the weighted sum
# is the readiness score. The verdict is decided by the ordered rules in
# _decide_verdict() — the score alone never overrides a hard fail.

WEIGHTS: dict[str, int] = {
    "budget_vs_cost_base": 30,
    "incentive_confidence": 30,
    "timeline_feasibility": 25,
    "soft_money_coverage": 15,
}

STATUS_POINTS: dict[str, int] = {
    "ready": 100,
    "conditional": 55,
    "insufficient_data": 25,
    "not_ready": 0,
}

READY_MIN_SCORE = 75
CONDITIONAL_MIN_SCORE = 45
MAX_INSUFFICIENT_COMPONENTS = 2  # this many or more → INSUFFICIENT DATA

STATUS_READY = "ready"
STATUS_CONDITIONAL = "conditional"
STATUS_NOT_READY = "not_ready"
STATUS_INSUFFICIENT = "insufficient_data"

VERDICT_READY = "READY"
VERDICT_CONDITIONAL = "CONDITIONAL"
VERDICT_NOT_READY = "NOT READY"
VERDICT_INSUFFICIENT = "INSUFFICIENT DATA"


# ── Component thresholds ─────────────────────────────────────────────────────

# Comparable-anchored cost band. A budget below half the median comparable in
# the same territory is flagged as under-capitalised against the local cost
# base; above twice it is noted but never penalised (a well-funded production
# is not a readiness risk).
COST_BASE_LOW_RATIO = 0.5
COST_BASE_HIGH_RATIO = 2.0
MIN_COMPARABLES_FOR_ANCHOR = 3

# Bankability labels, as produced by scoring._compute_bankability_label plus
# the older label wording still present on legacy stored reports.
BANKABLE_LABELS = frozenset({"BANKABLE"})
CONDITIONAL_BANKABILITY_LABELS = frozenset({"VERIFY FIRST", "CONDITIONALLY BANKABLE"})
UNBANKABLE_LABELS = frozenset({"NOT BANKABLE"})
NON_ASSESSABLE_LABELS = frozenset({"NOT APPLICABLE", "INFORMATIONAL"})

# Eligibility statuses that still qualify as confirmed-grade vs. those that
# make the incentive contingent on a structure the producer has not yet built.
ELIGIBILITY_CONFIRMED = frozenset({"qualified"})
ELIGIBILITY_CONTINGENT = frozenset({"requires_co_production", "requires_spv"})
ELIGIBILITY_FAIL = frozenset({"ineligible"})

# Post-production floor by format, in weeks. A stated modelling assumption,
# not sourced data — it is printed in the component basis so the reader can
# substitute their own schedule. Used only to test whether the declared
# completion date leaves any post window at all.
MIN_POST_WEEKS: dict[str, int] = {
    "Feature Film": 20,
    "Animated Feature": 40,
    "Animation": 40,
    "TV Series": 16,
    "Limited Series": 16,
    "Mini-Series": 16,
    "Docuseries": 16,
    "Documentary": 16,
    "TV Pilot": 10,
    "Short": 6,
    "Short Film": 6,
}
MIN_POST_WEEKS_DEFAULT = 16

# Certification-to-cash window beyond which the incentive cannot be treated as
# in-period cash flow and interim financing is required.
CASH_GAP_WARN_WEEKS = 52

# Soft money below this share of budget does not materially reduce the equity
# ask, so it is reported but not counted as coverage.
SOFT_MONEY_MATERIAL_PCT = 5.0

_ISO_DATE_RE = _re.compile(r"(\d{4}-\d{2}-\d{2})")


# ── Small parsing helpers ────────────────────────────────────────────────────

def _parse_date(value: Any) -> date | None:
    """Parse the first ISO date found in *value*. Returns None on anything else.

    Always returns a plain ``date``, never a ``datetime``. ``datetime`` is a
    subclass of ``date``, so the isinstance check below used to pass a datetime
    straight through; every caller then subtracted it from ``ctx.today``, a plain
    date, and raised ``TypeError: unsupported operand type(s) for -: 'datetime.date'
    and 'datetime.datetime'``. That killed report generation outright for any
    territory whose ``last_reviewed_at`` came back from the driver as a timestamp
    rather than a string. Narrowing here fixes every comparison site at once.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    match = _ISO_DATE_RE.search(str(value))
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _to_gbp(amount: float | None, currency: str | None) -> tuple[float | None, str | None]:
    """Convert *amount* in *currency* to GBP using the static fallback table.

    Returns ``(gbp_amount, basis)``. When the currency has no rate the amount
    is not guessed at — ``(None, None)`` is returned and the caller records a
    flag instead of a figure.
    """
    value = to_float(amount)
    if value is None:
        return None, None
    cur = (currency or "GBP").upper()
    if cur == "GBP":
        return value, "already GBP"
    rate = STATIC_FX_TO_GBP.get(cur)
    if not rate:
        return None, None
    return value / rate, f"converted from {cur} at {rate:g} {cur}/GBP (static fallback rate)"


def _pct(part: float, whole: float) -> float:
    return (part / whole * 100.0) if whole else 0.0


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def _fmt_money(symbol: str, value: float) -> str:
    return f"{symbol}{value:,.0f}"


def _dict_rows(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _check(name: str, result: str, detail: str) -> dict:
    """One traceable check. *result* is pass / fail / warn / skipped."""
    return {"name": name, "result": result, "detail": detail}


def _figure(label: str, value: str, basis: str) -> dict:
    return {"label": label, "value": value, "basis": basis}


def _flag(severity: str, field: str, detail: str, action: str) -> dict:
    """An unverified or stale input. *severity* is critical / warning / info."""
    return {"severity": severity, "input": field, "detail": detail, "action": action}


def _worst_status(statuses: list[str]) -> str:
    """Return the lowest-scoring status present."""
    if not statuses:
        return STATUS_INSUFFICIENT
    return min(statuses, key=lambda s: STATUS_POINTS.get(s, 0))


# ── Context assembly ─────────────────────────────────────────────────────────

class _Context:
    """Everything the components read, resolved once from report + datasets."""

    def __init__(
        self,
        report: dict,
        datasets: dict,
        request_metadata: dict,
        today: date,
    ) -> None:
        self.report = report
        self.datasets = datasets
        self.request_metadata = request_metadata
        self.today = today

        summary = report.get("executiveSummary")
        self.summary: dict = summary if isinstance(summary, dict) else {}

        rankings = _dict_rows(report.get("locationRankings"))
        self.rankings = rankings

        # Anchor territory: the recommended one, falling back to the top of the
        # ranking, then to the first budget scenario.
        self.scenarios = _dict_rows(
            (report.get("financialAnalysis") or {}).get("budgetScenarios")
            if isinstance(report.get("financialAnalysis"), dict) else None
        )
        self.timing = _dict_rows(
            (report.get("financialAnalysis") or {}).get("paymentTiming")
            if isinstance(report.get("financialAnalysis"), dict) else None
        )
        self.territory: str | None = (
            self.summary.get("recommendedTerritory")
            or (rankings[0].get("name") if rankings else None)
            or (self.scenarios[0].get("territory") if self.scenarios else None)
        )

        self.scenario: dict = next(
            (s for s in self.scenarios if s.get("territory") == self.territory), {}
        )
        self.estimates = _dict_rows(report.get("incentiveEstimates"))
        self.estimate: dict = next(
            (e for e in self.estimates if e.get("territory") == self.territory), {}
        )
        self.timing_entry: dict = next(
            (t for t in self.timing if t.get("territory") == self.territory), {}
        )
        self.ranking: dict = next(
            (r for r in rankings if r.get("name") == self.territory), {}
        )

        self.currency_symbol: str = self.scenario.get("currencySymbol") or "£"
        self.programme: str | None = self.scenario.get("programme") or self.estimate.get("program")

        profiles = datasets.get("_territory_profiles") or {}
        self.profile: dict = (
            profiles.get(self.territory) or {} if isinstance(profiles, dict) else {}
        )

        budget_gbp_data = datasets.get("_budget_gbp")
        self.budget_gbp: float | None = (
            to_float(budget_gbp_data.get("converted"))
            if isinstance(budget_gbp_data, dict) else None
        )
        self.budget_currency: str = datasets.get("_budget_currency") or "GBP"
        self.budget_amount: float | None = to_float(datasets.get("_budget_amount"))

        # Display-currency budget for the anchor territory, so every percentage
        # in the section divides two figures in the same currency.
        self.total_budget_value: float | None = to_float(
            self.scenario.get("totalBudgetValue")
        )
        self.net_rebate_value: float | None = to_float(self.scenario.get("netRebateValue"))

        # The incentive DB row for the modelled programme — the only source for
        # minimum qualifying spend and expiry.
        self.incentive_row: dict = self._resolve_incentive_row()

        self.completion_date = _parse_date(
            request_metadata.get("completion_date")
            or datasets.get("_completion_date")
        )
        self.filming_start = _parse_date(
            request_metadata.get("filming_start_date")
            or datasets.get("_filming_start_date")
        )
        self.shoot_weeks: int | None = None
        weeks = to_float(
            request_metadata.get("filming_duration") or datasets.get("_shoot_weeks")
        )
        if weeks and weeks > 0:
            self.shoot_weeks = int(weeks)
        self.production_format: str = (
            request_metadata.get("format") or datasets.get("_production_format") or ""
        )

    def _resolve_incentive_row(self) -> dict:
        """Find the incentives row matching the modelled programme + territory."""
        rows = [
            r for r in _dict_rows(self.datasets.get("incentives"))
            if r.get("territory") == self.territory
        ]
        if not rows:
            return {}
        if self.programme:
            for row in rows:
                if prog_name(row) == self.programme:
                    return row
        return {}


# ── Component 1 — budget against the territory cost base ─────────────────────

def _assess_budget_vs_cost_base(ctx: _Context, flags: list[dict]) -> dict:
    checks: list[dict] = []
    figures: list[dict] = []
    statuses: list[str] = []
    headline_parts: list[str] = []

    if ctx.budget_gbp is None:
        flags.append(_flag(
            "critical", "budget_amount",
            "Budget could not be normalised to GBP, so no cost-base test could run.",
            "Re-submit the production with a budget amount and a supported currency.",
        ))
        return {
            "key": "budget_vs_cost_base",
            "label": "Budget against territory cost base",
            "status": STATUS_INSUFFICIENT,
            "weight": WEIGHTS["budget_vs_cost_base"],
            "headline": "No normalised budget figure — cost base not assessed.",
            "figures": figures,
            "checks": [_check("budget normalisation", "skipped", "No GBP-normalised budget available.")],
        }

    if ctx.budget_amount:
        figures.append(_figure(
            "Declared budget",
            f"{ctx.budget_currency} {ctx.budget_amount:,.0f}",
            "intake: budget_amount + budget_currency",
        ))
    figures.append(_figure(
        "Budget (GBP-normalised)",
        _fmt_money("£", ctx.budget_gbp),
        f"FX conversion of the declared {ctx.budget_currency} budget "
        f"(datasets._budget_gbp)",
    ))

    # ── Check 1: minimum qualifying spend floor (hard eligibility gate) ──
    row = ctx.incentive_row
    qs_min_raw = row.get("qualifying_spend_min") if row else None
    qs_min = to_float(qs_min_raw)
    if qs_min and qs_min > 0:
        qs_currency = row.get("qualifying_spend_currency") or row.get("currency") or "GBP"
        qs_min_gbp, fx_basis = _to_gbp(qs_min, qs_currency)
        if qs_min_gbp is None:
            checks.append(_check(
                "minimum qualifying spend", "skipped",
                f"{ctx.programme or 'Programme'} states a {qs_currency} "
                f"{qs_min:,.0f} minimum spend, but no GBP rate is held for "
                f"{qs_currency} so it could not be compared to the budget.",
            ))
            flags.append(_flag(
                "warning", f"incentive_programs.qualifying_spend_currency ({qs_currency})",
                "No GBP fallback rate is held for this currency, so the "
                "programme's minimum spend was not tested against the budget.",
                f"Add a sourced GBP fallback rate for {qs_currency}, or verify "
                f"the threshold with the film commission directly.",
            ))
            statuses.append(STATUS_INSUFFICIENT)
        elif ctx.budget_gbp < qs_min_gbp:
            shortfall = qs_min_gbp - ctx.budget_gbp
            checks.append(_check(
                "minimum qualifying spend", "fail",
                f"Budget of {_fmt_money('£', ctx.budget_gbp)} is "
                f"{_fmt_money('£', shortfall)} below the "
                f"{_fmt_money('£', qs_min_gbp)} minimum qualifying spend for "
                f"{ctx.programme or 'the modelled programme'} "
                f"({qs_currency} {qs_min:,.0f}; {fx_basis}).",
            ))
            figures.append(_figure(
                "Programme minimum spend",
                _fmt_money("£", qs_min_gbp),
                f"incentive_programs.qualifying_spend_min for "
                f"{ctx.programme or 'the modelled programme'} — {fx_basis}",
            ))
            headline_parts.append(
                f"Budget is below {ctx.programme or 'the modelled programme'}'s "
                f"minimum qualifying spend"
            )
            statuses.append(STATUS_NOT_READY)
        else:
            checks.append(_check(
                "minimum qualifying spend", "pass",
                f"Budget of {_fmt_money('£', ctx.budget_gbp)} clears the "
                f"{_fmt_money('£', qs_min_gbp)} minimum qualifying spend for "
                f"{ctx.programme or 'the modelled programme'} "
                f"({qs_currency} {qs_min:,.0f}; {fx_basis}).",
            ))
            statuses.append(STATUS_READY)
    else:
        checks.append(_check(
            "minimum qualifying spend", "pass",
            f"{ctx.programme or 'The modelled programme'} states no minimum "
            f"qualifying spend, so there is no budget floor to clear.",
        ))
        statuses.append(STATUS_READY)

    # ── Check 2: comparable-anchored cost band ──
    comparables = _dict_rows(ctx.report.get("comparables"))
    territory_comps = [
        c for c in comparables
        if to_float(c.get("budgetUSD"))
        and ctx.territory
        and ctx.territory.lower() in str(c.get("location") or "").lower()
    ]
    anchor_comps = territory_comps or [
        c for c in comparables if to_float(c.get("budgetUSD"))
    ]
    anchor_scope = (
        f"comparables located in {ctx.territory}" if territory_comps
        else "all matched comparables (none located in the recommended territory)"
    )

    usd_rate = STATIC_FX_TO_GBP.get("USD", 1.27)
    budget_usd = ctx.budget_gbp * usd_rate

    if len(anchor_comps) < MIN_COMPARABLES_FOR_ANCHOR:
        checks.append(_check(
            "comparable cost anchor", "skipped",
            f"Only {len(anchor_comps)} comparable production(s) with a known "
            f"budget were matched; at least {MIN_COMPARABLES_FOR_ANCHOR} are "
            f"required before a median is treated as a cost anchor.",
        ))
        flags.append(_flag(
            "info", "comparable_productions.budget_usd",
            f"{len(anchor_comps)} comparable(s) with a known budget — too few "
            f"to anchor the budget against a local cost base.",
            "Add comparable productions for this territory and budget tier in "
            "the admin comparables dataset.",
        ))
        statuses.append(STATUS_INSUFFICIENT)
    else:
        comp_budgets = sorted(
            float(to_float(c.get("budgetUSD")) or 0) for c in anchor_comps
        )
        comp_median = median(comp_budgets)
        ratio = budget_usd / comp_median if comp_median else 0.0
        figures.append(_figure(
            "Median comparable budget",
            f"${comp_median:,.0f}",
            f"median of {len(anchor_comps)} {anchor_scope} "
            f"(comparable_productions.budget_usd)",
        ))
        figures.append(_figure(
            "Budget vs. that median",
            f"{ratio:.2f}×",
            f"{_fmt_money('£', ctx.budget_gbp)} converted at {usd_rate:g} "
            f"USD/GBP (static fallback rate) ÷ median comparable budget",
        ))
        if ratio < COST_BASE_LOW_RATIO:
            checks.append(_check(
                "comparable cost anchor", "fail",
                f"Budget is {ratio:.2f}× the median comparable, below the "
                f"{COST_BASE_LOW_RATIO:g}× floor — the production is priced "
                f"materially under the observed cost base for this territory "
                f"and scale.",
            ))
            headline_parts.append(
                f"budget is {ratio:.2f}× the median comparable in {ctx.territory}"
            )
            statuses.append(STATUS_CONDITIONAL)
        elif ratio > COST_BASE_HIGH_RATIO:
            checks.append(_check(
                "comparable cost anchor", "pass",
                f"Budget is {ratio:.2f}× the median comparable, above the "
                f"{COST_BASE_HIGH_RATIO:g}× band. Noted for context only — a "
                f"budget above the local cost base is not a readiness risk.",
            ))
            statuses.append(STATUS_READY)
        else:
            checks.append(_check(
                "comparable cost anchor", "pass",
                f"Budget is {ratio:.2f}× the median comparable, inside the "
                f"{COST_BASE_LOW_RATIO:g}×–{COST_BASE_HIGH_RATIO:g}× band for "
                f"this territory and scale.",
            ))
            statuses.append(STATUS_READY)

    # ── Check 3: cost-efficiency provenance (context, never a pass/fail) ──
    cost_score = ctx.profile.get("cost_efficiency_score")
    cost_source = ctx.profile.get("cost_efficiency_source")
    if cost_score is None:
        checks.append(_check(
            "territory cost-efficiency provenance", "warn",
            f"No curated cost-efficiency score is held for {ctx.territory}; "
            f"the ranking used a neutral 50 rather than sourced data.",
        ))
        flags.append(_flag(
            "warning", f"territory_profiles.cost_efficiency_score ({ctx.territory})",
            "No sourced cost-efficiency score — the location ranking used a "
            "neutral 50 placeholder for this dimension.",
            "Curate a sourced cost-efficiency score for this territory before "
            "relying on its cost ranking in a financing document.",
        ))
    else:
        checks.append(_check(
            "territory cost-efficiency provenance", "pass",
            f"{ctx.territory} cost efficiency scored {cost_score}/100, sourced "
            f"from {cost_source or 'an unnamed source'}.",
        ))
        if not cost_source:
            flags.append(_flag(
                "info", f"territory_profiles.cost_efficiency_source ({ctx.territory})",
                "A cost-efficiency score is held but carries no source "
                "attribution.",
                "Record the source for this score so the figure is defensible.",
            ))

    status = _worst_status(statuses)
    if headline_parts:
        headline = (
            headline_parts[0][0].upper() + headline_parts[0][1:]
            + (f"; {'; '.join(headline_parts[1:])}" if len(headline_parts) > 1 else "")
            + "."
        )
    elif status == STATUS_READY:
        headline = (
            f"Budget clears the programme's spend floor and sits inside the "
            f"observed cost base for {ctx.territory}."
        )
    else:
        headline = (
            f"Budget could not be fully tested against the cost base for "
            f"{ctx.territory} — see the checks below."
        )

    return {
        "key": "budget_vs_cost_base",
        "label": "Budget against territory cost base",
        "status": status,
        "weight": WEIGHTS["budget_vs_cost_base"],
        "headline": headline,
        "figures": figures,
        "checks": checks,
    }


# ── Component 2 — confirmed vs. estimated incentive value ────────────────────

def _classify_estimate(
    estimate: dict, ctx: _Context,
) -> tuple[str, list[str]]:
    """Classify one incentive estimate as confirmed / contingent / failed.

    Returns ``(grade, reasons)`` where grade is ``confirmed``, ``contingent``
    or ``failed``, and reasons lists every condition that decided it.
    """
    reasons: list[str] = []
    label = (estimate.get("bankabilityLabel") or "").strip().upper()
    eligibility = (estimate.get("eligibilityStatus") or "").strip().lower()

    if label in NON_ASSESSABLE_LABELS:
        return "failed", [
            f"Programme is marked {label.title()} — it carries no assessable "
            f"incentive value for this production."
        ]

    failed = False
    contingent = False

    if eligibility in ELIGIBILITY_FAIL:
        failed = True
        reasons.append(
            f"Eligibility is 'ineligible': {estimate.get('eligibilityNote') or 'no route stated'}."
        )
    elif eligibility in ELIGIBILITY_CONTINGENT:
        contingent = True
        reasons.append(
            f"Eligibility is '{eligibility}' — the value depends on a structure "
            f"that is not yet in place."
        )
    elif eligibility in ELIGIBILITY_CONFIRMED:
        # Producer structure is one required dimension, not the whole answer. Saying
        # "qualifies outright" while the project's format eligibility for the same
        # programme is unresolved is the contradiction this check used to produce:
        # a status is only as strong as its weakest required dimension.
        project_status = estimate.get("incentiveEligibilityStatus")
        if estimate.get("incentiveIsConfirmed") is False:
            contingent = True
            detail = (estimate.get("incentiveEligibilityReasons") or [None])[0]
            reasons.append(
                "Producer structural requirements appear satisfied, but overall "
                "programme eligibility remains "
                f"{project_status or 'unresolved'}"
                + (f": {detail}" if detail else ".")
            )
        else:
            reasons.append("Producer qualifies outright (eligibility: qualified).")
    else:
        contingent = True
        reasons.append(
            "Eligibility has not been determined, so the value cannot be "
            "treated as confirmed."
        )

    if label in UNBANKABLE_LABELS:
        failed = True
        reasons.append("Bankability is 'Not bankable' — no lender will advance against it.")
    elif label in CONDITIONAL_BANKABILITY_LABELS:
        contingent = True
        reasons.append(f"Bankability is '{label.title()}' — verification required before it can be banked.")
    elif label in BANKABLE_LABELS:
        reasons.append("Bankability is 'Bankable'.")
    else:
        contingent = True
        reasons.append("No bankability assessment is held for this programme.")

    if estimate.get("stalenessWarning"):
        contingent = True
        reasons.append(
            f"Underlying incentive record is older than {STALE_DAYS} days."
        )

    if not estimate.get("lastUpdated"):
        contingent = True
        reasons.append("Underlying incentive record carries no verification date.")

    expiry = _parse_date(estimate.get("expiryDate"))
    if expiry:
        horizon = ctx.completion_date or ctx.today
        if expiry < horizon:
            failed = True
            reasons.append(
                f"Programme expires {expiry.isoformat()}, before the "
                f"{'declared completion date' if ctx.completion_date else 'assessment date'} "
                f"of {horizon.isoformat()}."
            )

    if ctx.profile.get("bankability_suspended") and estimate.get("territory") == ctx.territory:
        failed = True
        reasons.append("Territory payment reliability is suspended in the bankability dataset.")

    if failed:
        return "failed", reasons
    if contingent:
        return "contingent", reasons
    return "confirmed", reasons


def _assess_incentive_confidence(ctx: _Context, flags: list[dict]) -> dict:
    checks: list[dict] = []
    figures: list[dict] = []

    if not ctx.estimate or ctx.net_rebate_value is None or not ctx.total_budget_value:
        flags.append(_flag(
            "critical", "incentiveEstimates",
            f"No modelled incentive estimate is held for "
            f"{ctx.territory or 'the recommended territory'}.",
            "Confirm whether the territory operates a production incentive, "
            "and add the programme to the incentives dataset if it does.",
        ))
        return {
            "key": "incentive_confidence",
            "label": "Confirmed vs. estimated incentive value",
            "status": STATUS_INSUFFICIENT,
            "weight": WEIGHTS["incentive_confidence"],
            "headline": (
                f"No incentive value is modelled for "
                f"{ctx.territory or 'the recommended territory'}."
            ),
            "figures": figures,
            "checks": [_check(
                "incentive estimate present", "skipped",
                "No incentive estimate or net rebate figure to assess.",
            )],
        }

    sym = ctx.currency_symbol
    coverage_pct = _pct(ctx.net_rebate_value, ctx.total_budget_value)
    grade, reasons = _classify_estimate(ctx.estimate, ctx)

    figures.append(_figure(
        "Modelled net incentive",
        _fmt_money(sym, ctx.net_rebate_value),
        f"financialAnalysis.budgetScenarios[{ctx.territory}].netRebateValue — "
        f"{ctx.programme or 'modelled programme'}",
    ))
    figures.append(_figure(
        "As a share of budget",
        _fmt_pct(coverage_pct),
        f"{_fmt_money(sym, ctx.net_rebate_value)} ÷ "
        f"{_fmt_money(sym, ctx.total_budget_value)} — both in {sym} display currency",
    ))
    figures.append(_figure(
        "Confirmed incentive value",
        _fmt_money(sym, ctx.net_rebate_value) if grade == "confirmed" else _fmt_money(sym, 0),
        "the modelled value counts as confirmed only when this project is eligible "
        "for the programme in its own format, the producer "
        "qualifies outright, the incentive is bankable, the record was "
        f"verified inside {STALE_DAYS} days, and the programme has not expired",
    ))

    for reason in reasons:
        checks.append(_check(
            "confirmation criteria",
            "pass" if grade == "confirmed" else ("fail" if grade == "failed" else "warn"),
            reason,
        ))

    # Every other ranked territory, graded the same way — so the reader can see
    # whether a confirmed alternative exists.
    alt_confirmed: list[str] = []
    for est in ctx.estimates:
        territory = est.get("territory")
        if not territory or territory == ctx.territory:
            continue
        alt_grade, _ = _classify_estimate(est, ctx)
        if alt_grade == "confirmed":
            alt_confirmed.append(territory)
    if alt_confirmed:
        checks.append(_check(
            "confirmed alternatives", "pass",
            f"Confirmed-grade incentive value also modelled for: "
            f"{', '.join(sorted(alt_confirmed))}.",
        ))

    if ctx.estimate.get("stalenessWarning"):
        flags.append(_flag(
            "warning", f"incentive_programs ({ctx.territory} · {ctx.programme})",
            f"Incentive record is older than {STALE_DAYS} days, so the rate, "
            f"cap and rules modelled here may no longer be current.",
            "Re-verify the programme against the film commission's published "
            "terms before using these figures in a financing document.",
        ))
    if not ctx.estimate.get("lastUpdated"):
        flags.append(_flag(
            "warning", f"incentive_programs.last_verified_at ({ctx.territory})",
            "No verification date is held for the modelled programme.",
            "Record a verification date so staleness can be assessed at all.",
        ))
    if (ctx.estimate.get("eligibilityStatus") or "unknown").lower() not in (
        ELIGIBILITY_CONFIRMED | ELIGIBILITY_CONTINGENT | ELIGIBILITY_FAIL
    ):
        flags.append(_flag(
            "warning", "eligibilityStatus",
            "Producer eligibility for the modelled programme has not been "
            "determined.",
            "Complete producer nationality and co-production status at intake, "
            "then confirm the route with the programme administrator.",
        ))
    source_quality = (ctx.timing_entry.get("sourceQuality") or "").strip()
    if ctx.timing_entry and not source_quality:
        flags.append(_flag(
            "info", f"territory_profiles.bankability_source_quality ({ctx.territory})",
            "Payment-timing data carries no source-quality grading.",
            "Grade the source so the payment window can be relied on.",
        ))
    if ctx.profile.get("bankability_suspended"):
        flags.append(_flag(
            "critical", f"territory_profiles.bankability_suspended ({ctx.territory})",
            "Payment reliability for this territory is suspended — the "
            "incentive cannot be treated as recoverable cash on the modelled "
            "timeline.",
            "Do not bank this incentive. Confirm current payment status with "
            "productions that have recently certified in this territory.",
        ))

    status = {
        "confirmed": STATUS_READY,
        "contingent": STATUS_CONDITIONAL,
        "failed": STATUS_NOT_READY,
    }[grade]

    headline = {
        "confirmed": (
            f"The {_fmt_money(sym, ctx.net_rebate_value)} modelled incentive "
            f"({_fmt_pct(coverage_pct)} of budget) meets every confirmation "
            f"criterion."
        ),
        "contingent": (
            f"The {_fmt_money(sym, ctx.net_rebate_value)} modelled incentive "
            f"({_fmt_pct(coverage_pct)} of budget) is an estimate, not "
            f"confirmed value — treat it as contingent in the finance plan."
        ),
        "failed": (
            f"The {_fmt_money(sym, ctx.net_rebate_value)} modelled incentive "
            f"cannot be relied on for {ctx.territory} on the stated basis."
        ),
    }[grade]

    return {
        "key": "incentive_confidence",
        "label": "Confirmed vs. estimated incentive value",
        "status": status,
        "weight": WEIGHTS["incentive_confidence"],
        "headline": headline,
        "figures": figures,
        "checks": checks,
        "grade": grade,
    }


# ── Component 3 — soft-money coverage ────────────────────────────────────────

def _assess_soft_money(ctx: _Context, flags: list[dict]) -> dict:
    checks: list[dict] = []
    figures: list[dict] = []

    if not ctx.total_budget_value or ctx.budget_gbp is None:
        return {
            "key": "soft_money_coverage",
            "label": "Soft-money coverage",
            "status": STATUS_INSUFFICIENT,
            "weight": WEIGHTS["soft_money_coverage"],
            "headline": "No budget figure to measure soft-money coverage against.",
            "figures": figures,
            "checks": [_check(
                "budget present", "skipped",
                "No display-currency budget available for the recommended territory.",
            )],
        }

    funds = [
        f for f in _dict_rows(ctx.report.get("fundingOpportunities"))
        if (f.get("type") or "") == "Fund"
    ]

    # Grant amounts come from the grants dataset, where the currency is held
    # explicitly. The report entry's `notes` string is only a display label.
    grant_rows = _dict_rows(ctx.datasets.get("grants"))
    grants_by_title: dict[str, dict] = {}
    for row in grant_rows:
        title = (row.get("title") or row.get("fund_name") or "").strip()
        if title:
            grants_by_title[title.lower()] = row

    quantified: list[tuple[str, float]] = []
    unquantified: list[str] = []
    open_deadlines: list[tuple[str, date]] = []

    for fund in funds:
        name = (fund.get("name") or "").strip()
        row = grants_by_title.get(name.lower(), {})
        amount = to_float(row.get("max_amount"))
        currency = row.get("currency") or "GBP"
        if amount is None:
            # Fall back to the display string, which carries a symbol rather
            # than an ISO code — parsed, but only counted when the grant's own
            # currency is known from the dataset.
            amount = parse_money_string(fund.get("notes"))
            currency = row.get("currency") or currency
        gbp, _basis = _to_gbp(amount, currency) if amount else (None, None)
        if gbp:
            quantified.append((name, gbp))
        else:
            unquantified.append(name)
        deadline = _parse_date(fund.get("deadline"))
        if deadline and deadline >= ctx.today:
            open_deadlines.append((name, deadline))

    soft_gbp = sum(amount for _, amount in quantified)
    soft_pct = _pct(soft_gbp, ctx.budget_gbp)
    incentive_pct = (
        _pct(ctx.net_rebate_value, ctx.total_budget_value)
        if ctx.net_rebate_value is not None else 0.0
    )
    residual_pct = max(0.0, 100.0 - soft_pct - incentive_pct)

    figures.append(_figure(
        "Matched soft money (maximum)",
        _fmt_money("£", soft_gbp),
        f"sum of the stated maximum award across {len(quantified)} matched "
        f"fund(s) (grants.max_amount, GBP-normalised)",
    ))
    figures.append(_figure(
        "Soft money as a share of budget",
        _fmt_pct(soft_pct),
        f"{_fmt_money('£', soft_gbp)} ÷ {_fmt_money('£', ctx.budget_gbp)} "
        f"(both GBP)",
    ))
    figures.append(_figure(
        "Modelled incentive share",
        _fmt_pct(incentive_pct),
        f"net rebate ÷ total budget for {ctx.territory}, both in "
        f"{ctx.currency_symbol} display currency",
    ))
    figures.append(_figure(
        "Residual for equity or debt",
        _fmt_pct(residual_pct),
        "100% less the soft-money and incentive shares above; not reduced by "
        "any equity already committed, which this report does not hold",
    ))

    if quantified:
        checks.append(_check(
            "quantified soft money", "pass",
            f"{len(quantified)} fund(s) carry a stated maximum award: "
            + "; ".join(
                f"{name} ({_fmt_money('£', amount)})"
                for name, amount in sorted(quantified, key=lambda p: -p[1])[:5]
            )
            + ".",
        ))
    if unquantified:
        checks.append(_check(
            "unquantified soft money", "warn",
            f"{len(unquantified)} matched fund(s) state no award amount in a "
            f"currency this engine holds, so they contribute nothing to the "
            f"coverage figure: {', '.join(unquantified[:5])}"
            + ("…" if len(unquantified) > 5 else "") + ".",
        ))
        flags.append(_flag(
            "info", "grants.max_amount",
            f"{len(unquantified)} matched fund(s) have no usable award amount, "
            f"so soft-money coverage is understated.",
            "Record max_amount and currency for these funds in the admin "
            "grants dataset.",
        ))

    if open_deadlines:
        soonest = min(open_deadlines, key=lambda p: p[1])
        checks.append(_check(
            "application windows open", "pass",
            f"{len(open_deadlines)} matched fund(s) have a deadline still ahead; "
            f"the nearest is {soonest[0]} on {soonest[1].isoformat()} "
            f"({(soonest[1] - ctx.today).days} days away).",
        ))
    elif funds:
        checks.append(_check(
            "application windows open", "warn",
            "No matched fund carries a dated deadline still ahead, so none can "
            "be confirmed as open on this assessment date.",
        ))

    if not funds:
        checks.append(_check(
            "matched funds", "skipped",
            "No grants or funds matched this production's format, territory and "
            "budget, so there is no soft money to measure.",
        ))
        status = STATUS_INSUFFICIENT
        headline = (
            "No soft money matched this production — the whole budget less the "
            "modelled incentive must come from equity or debt."
        )
    elif soft_pct >= SOFT_MONEY_MATERIAL_PCT and open_deadlines:
        status = STATUS_READY
        headline = (
            f"{_fmt_pct(soft_pct)} of budget in matched soft money with at "
            f"least one application window still open."
        )
    elif soft_pct >= SOFT_MONEY_MATERIAL_PCT:
        status = STATUS_CONDITIONAL
        headline = (
            f"{_fmt_pct(soft_pct)} of budget in matched soft money, but no "
            f"application window can be confirmed as open."
        )
    else:
        status = STATUS_CONDITIONAL
        headline = (
            f"Matched soft money covers {_fmt_pct(soft_pct)} of budget, below "
            f"the {SOFT_MONEY_MATERIAL_PCT:g}% at which it materially reduces "
            f"the equity ask."
        )

    return {
        "key": "soft_money_coverage",
        "label": "Soft-money coverage",
        "status": status,
        "weight": WEIGHTS["soft_money_coverage"],
        "headline": headline,
        "figures": figures,
        "checks": checks,
        # Soft money is potential, not committed, and a fully equity-financed
        # production is not unready — this component therefore never returns
        # not_ready.
        "note": (
            "Soft-money figures are maximum available awards on matched "
            "programmes, not committed funds. A production with no soft money "
            "is not unready; the figure exists to show how much of the budget "
            "still has to be raised."
        ),
    }


# ── Component 4 — timeline feasibility ───────────────────────────────────────

def _assess_timeline(ctx: _Context, flags: list[dict]) -> dict:
    checks: list[dict] = []
    figures: list[dict] = []
    statuses: list[str] = []
    headline_parts: list[str] = []
    # Set by the schedule test below; gates the whole component (see the status
    # resolution at the end of this function).
    schedule_status = STATUS_INSUFFICIENT

    post_weeks = MIN_POST_WEEKS.get(ctx.production_format, MIN_POST_WEEKS_DEFAULT)

    if not ctx.completion_date:
        flags.append(_flag(
            "warning", "completion_date",
            "No expected completion date was declared, so schedule and "
            "festival-window feasibility could not be tested.",
            "Capture the expected completion date at intake and re-run the "
            "report.",
        ))
        checks.append(_check(
            "completion date declared", "skipped",
            "No completion date on the production, so no downstream timing "
            "test could run.",
        ))
    else:
        figures.append(_figure(
            "Declared completion",
            ctx.completion_date.isoformat(),
            "intake: completion_date",
        ))

    # ── Check: shoot end + post floor vs. completion date ──
    if ctx.completion_date and ctx.filming_start and ctx.shoot_weeks:
        shoot_end = ctx.filming_start + timedelta(weeks=ctx.shoot_weeks)
        required = shoot_end + timedelta(weeks=post_weeks)
        figures.append(_figure(
            "Photography ends",
            shoot_end.isoformat(),
            f"filming_start_date {ctx.filming_start.isoformat()} + "
            f"{ctx.shoot_weeks} week shoot (intake: filming_duration)",
        ))
        figures.append(_figure(
            "Earliest feasible completion",
            required.isoformat(),
            f"photography end + a {post_weeks}-week post-production floor for "
            f"{ctx.production_format or 'this format'} (stated modelling "
            f"assumption, not sourced data)",
        ))
        if ctx.completion_date < shoot_end:
            checks.append(_check(
                "completion after photography", "fail",
                f"Declared completion of {ctx.completion_date.isoformat()} "
                f"falls before photography ends on {shoot_end.isoformat()} — "
                f"the schedule is internally inconsistent.",
            ))
            headline_parts.append("completion date precedes the end of photography")
            schedule_status = STATUS_NOT_READY
        elif ctx.completion_date < required:
            short_weeks = (required - ctx.completion_date).days / 7.0
            checks.append(_check(
                "post-production window", "fail",
                f"Declared completion leaves "
                f"{(ctx.completion_date - shoot_end).days / 7.0:.0f} weeks of "
                f"post, {short_weeks:.0f} weeks short of the {post_weeks}-week "
                f"floor assumed for {ctx.production_format or 'this format'}.",
            ))
            headline_parts.append(
                f"post window is {short_weeks:.0f} weeks short of the "
                f"{post_weeks}-week floor"
            )
            schedule_status = STATUS_CONDITIONAL
        else:
            checks.append(_check(
                "post-production window", "pass",
                f"Declared completion leaves "
                f"{(ctx.completion_date - shoot_end).days / 7.0:.0f} weeks of "
                f"post, clearing the {post_weeks}-week floor assumed for "
                f"{ctx.production_format or 'this format'}.",
            ))
            schedule_status = STATUS_READY
    else:
        missing = [
            name for name, value in (
                ("filming_start_date", ctx.filming_start),
                ("filming_duration", ctx.shoot_weeks),
                ("completion_date", ctx.completion_date),
            ) if not value
        ]
        checks.append(_check(
            "post-production window", "skipped",
            f"Cannot test the post window: {', '.join(missing)} not declared.",
        ))
        for name in missing:
            if name == "completion_date":
                continue  # already flagged above
            flags.append(_flag(
                "warning", name,
                f"{name} was not declared, so the shoot-to-delivery schedule "
                f"could not be tested.",
                f"Capture {name} at intake and re-run the report.",
            ))

    # ── Check: incentive certification-to-cash window ──
    total_weeks_max = to_float(ctx.timing_entry.get("totalWeeksMax"))
    if total_weeks_max:
        figures.append(_figure(
            "Certification to cash",
            f"up to {total_weeks_max:.0f} weeks",
            f"territory_profiles cert_weeks_max + payment_weeks_max for "
            f"{ctx.territory}",
        ))
        if ctx.completion_date:
            cash_date = ctx.completion_date + timedelta(weeks=total_weeks_max)
            figures.append(_figure(
                "Latest expected incentive receipt",
                cash_date.isoformat(),
                f"declared completion + the {total_weeks_max:.0f}-week "
                f"certification-to-cash window above",
            ))
        if total_weeks_max > CASH_GAP_WARN_WEEKS:
            checks.append(_check(
                "incentive cash-flow gap", "fail",
                f"The incentive can take up to {total_weeks_max:.0f} weeks "
                f"after completion to arrive, beyond the "
                f"{CASH_GAP_WARN_WEEKS}-week point at which it cannot be "
                f"treated as in-period cash flow — interim or gap financing is "
                f"required to bridge it.",
            ))
            headline_parts.append(
                f"incentive cash is up to {total_weeks_max:.0f} weeks behind "
                f"completion"
            )
            statuses.append(STATUS_CONDITIONAL)
        else:
            checks.append(_check(
                "incentive cash-flow gap", "pass",
                f"The incentive is expected within {total_weeks_max:.0f} weeks "
                f"of completion, inside the {CASH_GAP_WARN_WEEKS}-week "
                f"in-period threshold.",
            ))
            statuses.append(STATUS_READY)
    else:
        checks.append(_check(
            "incentive cash-flow gap", "skipped",
            f"No certification or payment window is held for "
            f"{ctx.territory or 'the recommended territory'}.",
        ))
        flags.append(_flag(
            "warning",
            f"territory_profiles.cert_weeks_max / payment_weeks_max ({ctx.territory})",
            "No payment-timing data, so the gap between completion and "
            "incentive receipt is unknown.",
            "Research and record the certification and payment windows for "
            "this territory.",
        ))
        statuses.append(STATUS_INSUFFICIENT)

    # ── Check: festival / market windows after delivery ──
    festival_deadlines: list[tuple[str, date]] = []
    for opp in _dict_rows(ctx.report.get("fundingOpportunities")):
        if (opp.get("type") or "") != "Festival":
            continue
        deadline = _parse_date(opp.get("deadline"))
        if deadline:
            festival_deadlines.append((str(opp.get("name") or ""), deadline))

    matched_festivals = _dict_rows(ctx.report.get("festivalRecommendations"))

    if ctx.completion_date and festival_deadlines:
        reachable = [
            (name, dl) for name, dl in festival_deadlines
            if dl >= ctx.completion_date
        ]
        figures.append(_figure(
            "Festival deadlines reachable after delivery",
            f"{len(reachable)} of {len(festival_deadlines)}",
            "dated festival submission deadlines from the matched festival set, "
            "compared to the declared completion date",
        ))
        if reachable:
            soonest = min(reachable, key=lambda p: p[1])
            checks.append(_check(
                "festival window", "pass",
                f"{len(reachable)} matched festival deadline(s) fall after "
                f"delivery; the nearest reachable one is {soonest[0]} on "
                f"{soonest[1].isoformat()}.",
            ))
            statuses.append(STATUS_READY)
        else:
            latest = max(festival_deadlines, key=lambda p: p[1])
            checks.append(_check(
                "festival window", "fail",
                f"Every dated festival deadline in the matched set falls before "
                f"the declared completion of {ctx.completion_date.isoformat()} "
                f"(latest: {latest[0]}, {latest[1].isoformat()}) — the first "
                f"festival cycle is missed on this schedule.",
            ))
            headline_parts.append("the first festival cycle is missed on this schedule")
            statuses.append(STATUS_CONDITIONAL)
    else:
        detail = (
            "No completion date, so festival reachability could not be tested."
            if not ctx.completion_date else
            f"{len(matched_festivals)} festival(s) matched, but none carries a "
            f"dated submission deadline, so reachability could not be tested."
        )
        checks.append(_check("festival window", "skipped", detail))
        if ctx.completion_date and matched_festivals:
            flags.append(_flag(
                "info", "festivals.submission_deadline",
                "Matched festivals carry no dated submission deadline, so the "
                "delivery schedule could not be tested against the festival "
                "calendar.",
                "Record dated submission deadlines for these festivals in the "
                "admin festivals dataset.",
            ))
        statuses.append(STATUS_INSUFFICIENT)

    # The schedule test gates this component: without a completion date and a
    # shoot window there is no timeline to assess at all. The payment-window and
    # festival tests are independent feasibility checks, so a missing festival
    # calendar must not make a sound schedule look unassessable — it is recorded
    # as a flag instead.
    if schedule_status == STATUS_INSUFFICIENT:
        status = STATUS_INSUFFICIENT
    else:
        assessed = [s for s in statuses if s != STATUS_INSUFFICIENT]
        status = _worst_status([schedule_status, *assessed])

    if headline_parts:
        headline = (
            headline_parts[0][0].upper() + headline_parts[0][1:]
            + (f"; {'; '.join(headline_parts[1:])}" if len(headline_parts) > 1 else "")
            + "."
        )
    elif status == STATUS_READY:
        headline = (
            "Schedule, incentive payment window and festival calendar are all "
            "consistent with the declared completion date."
        )
    else:
        headline = (
            "Timeline could not be fully tested — see the checks below for "
            "which inputs are missing."
        )

    return {
        "key": "timeline_feasibility",
        "label": "Timeline feasibility",
        "status": status,
        "weight": WEIGHTS["timeline_feasibility"],
        "headline": headline,
        "figures": figures,
        "checks": checks,
    }


# ── Cross-cutting input flags ────────────────────────────────────────────────

def _collect_input_flags(ctx: _Context, flags: list[dict]) -> None:
    """Flags about input provenance that no single component owns."""
    if (ctx.budget_currency or "").upper() == "OTHER":
        flags.append(_flag(
            "critical", "budget_currency",
            "Budget currency was submitted as 'Other', so no FX rate could be "
            "applied and every converted figure in this report is unreliable.",
            "Re-submit the production with a supported budget currency.",
        ))
    elif (
        (ctx.budget_currency or "GBP").upper() != "GBP"
        and (ctx.budget_currency or "").upper() not in STATIC_FX_TO_GBP
    ):
        flags.append(_flag(
            "warning", f"budget_currency ({ctx.budget_currency})",
            "This currency has no offline fallback rate, so if the live FX API "
            "was unavailable the budget converted at 1:1.",
            "Verify the GBP-normalised budget figure before relying on any "
            "percentage in this section.",
        ))

    budget_gbp_data = ctx.datasets.get("_budget_gbp")
    if isinstance(budget_gbp_data, dict):
        rate_date = _parse_date(budget_gbp_data.get("rate_date"))
        if rate_date and (ctx.today - rate_date).days > 30:
            flags.append(_flag(
                "warning", "fx rate date",
                f"The budget was converted at a rate dated "
                f"{rate_date.isoformat()}, {(ctx.today - rate_date).days} days "
                f"before this assessment.",
                "Re-run the report to convert at a current rate.",
            ))

    last_reviewed = _parse_date(ctx.profile.get("last_reviewed_at"))
    if ctx.profile and last_reviewed is None:
        flags.append(_flag(
            "info", f"territory_profiles.last_reviewed_at ({ctx.territory})",
            "The territory profile behind the crew-depth, infrastructure and "
            "cost scores carries no review date.",
            "Record a review date so profile staleness can be assessed.",
        ))
    elif last_reviewed and (ctx.today - last_reviewed).days > STALE_DAYS:
        flags.append(_flag(
            "warning", f"territory_profiles.last_reviewed_at ({ctx.territory})",
            f"The territory profile was last reviewed "
            f"{(ctx.today - last_reviewed).days} days ago, beyond the "
            f"{STALE_DAYS}-day freshness threshold.",
            "Re-review the territory profile before relying on its scores.",
        ))

    # Any ranked territory whose incentive record is stale, not just the
    # recommended one — a producer comparing options needs to know.
    stale_others = sorted({
        str(est.get("territory"))
        for est in ctx.estimates
        if est.get("stalenessWarning") and est.get("territory") != ctx.territory
    })
    if stale_others:
        flags.append(_flag(
            "info", "incentive_programs (other ranked territories)",
            f"Incentive records for {', '.join(stale_others)} are older than "
            f"{STALE_DAYS} days.",
            "Re-verify these programmes before comparing them as alternatives.",
        ))


# ── Verdict ──────────────────────────────────────────────────────────────────

def _decide_verdict(
    components: list[dict], flags: list[dict], score: int,
) -> tuple[str, str, str]:
    """Apply the rubric. Returns ``(verdict, rule, reason)``.

    Rules are evaluated in order; the first that matches decides. The score
    never overrides a hard fail, and a critical input flag can never produce a
    READY verdict.
    """
    by_status: dict[str, list[dict]] = {}
    for component in components:
        by_status.setdefault(component["status"], []).append(component)

    failed = by_status.get(STATUS_NOT_READY, [])
    if failed:
        return (
            VERDICT_NOT_READY,
            "R1: any component not ready",
            "Not ready because "
            + "; ".join(f"{c['label'].lower()} failed" for c in failed)
            + ". A failed component is decisive regardless of the weighted score.",
        )

    insufficient = by_status.get(STATUS_INSUFFICIENT, [])
    if len(insufficient) >= MAX_INSUFFICIENT_COMPONENTS:
        return (
            VERDICT_INSUFFICIENT,
            f"R2: {MAX_INSUFFICIENT_COMPONENTS} or more components lack data",
            f"{len(insufficient)} of {len(components)} components could not be "
            f"assessed ("
            + ", ".join(c["label"].lower() for c in insufficient)
            + "). The missing inputs are listed as flags below.",
        )

    # Conditions that block READY however high the weighted score climbs. A
    # score alone must never let the section claim a plan is financeable when a
    # figure it depends on is unverified, unassessed, or merely estimated.
    blockers: list[str] = []
    criticals = [f for f in flags if f.get("severity") == "critical"]
    if criticals:
        blockers.append(
            f"{len(criticals)} critical input flag(s) mean the figures cannot "
            f"be relied on until they are resolved"
        )
    if insufficient:
        blockers.append(
            f"{insufficient[0]['label'].lower()} could not be assessed from the "
            f"inputs held"
        )
    incentive = next(
        (c for c in components if c["key"] == "incentive_confidence"), None
    )
    if incentive is not None and incentive["status"] != STATUS_READY:
        blockers.append(
            "the modelled incentive is estimated rather than confirmed value"
        )

    if score >= READY_MIN_SCORE and blockers:
        return (
            VERDICT_CONDITIONAL,
            "R3: a ready score is blocked by an unresolved condition",
            f"The weighted score reaches the ready threshold ({score}/100), but "
            + "; ".join(blockers)
            + ". Resolve these before treating the plan as financeable.",
        )

    if score >= READY_MIN_SCORE:
        return (
            VERDICT_READY,
            f"R4: weighted score ≥ {READY_MIN_SCORE} with no blocking condition",
            f"Weighted readiness score is {score}/100, with every component "
            f"assessed, the modelled incentive confirmed, and no critical input "
            f"flag.",
        )

    if score >= CONDITIONAL_MIN_SCORE:
        return (
            VERDICT_CONDITIONAL,
            f"R5: weighted score {CONDITIONAL_MIN_SCORE}–{READY_MIN_SCORE - 1}",
            f"Weighted readiness score is {score}/100 — the plan holds "
            f"together but carries conditions that must be resolved before "
            f"financial close"
            + (f": {'; '.join(blockers)}" if blockers else "")
            + ".",
        )

    return (
        VERDICT_NOT_READY,
        f"R6: weighted score < {CONDITIONAL_MIN_SCORE}",
        f"Weighted readiness score is {score}/100, below the "
        f"{CONDITIONAL_MIN_SCORE}-point floor for a conditional verdict.",
    )


# Section explainer, injected by both ReportBuilder._inject_section_explainers
# and ReportValidator._inject_section_explainers under the key
# ``financial_readiness``.
SECTION_EXPLAINER = (
    "How we assess readiness: this section is computed, not written. Four "
    "components — your budget against the recommended territory's cost base, "
    "how much of the modelled incentive is confirmed rather than estimated, "
    "matched soft money as a share of budget, and whether your completion date "
    "survives the shoot schedule, the incentive payment window and the "
    "festival calendar — are each tested against the figures elsewhere in this "
    "report. Every figure names the input it came from, every check states "
    "what it compared, and the verdict follows a fixed rule order rather than "
    "a judgement. Where an input is missing, stale or unverified it is listed "
    "as a flag instead of being estimated around."
)

METHODOLOGY = (
    "How this verdict is computed: four components are each assessed against "
    "the report's own figures and given one of four statuses — ready, "
    "conditional, insufficient data, or not ready. Statuses convert to points "
    f"(ready {STATUS_POINTS['ready']}, conditional {STATUS_POINTS['conditional']}, "
    f"insufficient data {STATUS_POINTS['insufficient_data']}, not ready "
    f"{STATUS_POINTS['not_ready']}) and are weighted "
    + ", ".join(f"{key.replace('_', ' ')} {weight}%" for key, weight in WEIGHTS.items())
    + ". The verdict then follows a fixed rule order: any failed component "
    "means NOT READY; two or more unassessable components means INSUFFICIENT "
    "DATA; a READY verdict additionally requires that every component was "
    "assessable, that the modelled incentive is confirmed rather than "
    "estimated, and that no critical input flag is outstanding; otherwise "
    f"the weighted score decides (≥ {READY_MIN_SCORE} READY, ≥ "
    f"{CONDITIONAL_MIN_SCORE} CONDITIONAL, below that NOT READY). No part of "
    "this section is generated or scored by a language model, and every figure "
    "cites the input it came from. It is an assessment of the inputs held, not "
    "financial advice — verify with a production accountant and the relevant "
    "film commission before financial close."
)


# ── Entry point ──────────────────────────────────────────────────────────────

def compute_financial_readiness(
    *,
    report: dict,
    datasets: dict,
    request_metadata: dict | None = None,
    today: date | None = None,
) -> dict | None:
    """Assess financial readiness from the built report. Returns None when the
    report has no financial basis to assess (e.g. a free preview, which is
    stripped of every monetary figure before it leaves the engine).
    """
    if not isinstance(report, dict) or not isinstance(datasets, dict):
        return None

    ctx = _Context(report, datasets, request_metadata or {}, today or date.today())

    if not ctx.territory or not ctx.scenarios:
        logger.debug(
            "financial readiness skipped: territory=%s scenarios=%d",
            ctx.territory, len(ctx.scenarios),
        )
        return None

    flags: list[dict] = []
    components = [
        _assess_budget_vs_cost_base(ctx, flags),
        _assess_incentive_confidence(ctx, flags),
        _assess_timeline(ctx, flags),
        _assess_soft_money(ctx, flags),
    ]
    _collect_input_flags(ctx, flags)

    total_weight = sum(c["weight"] for c in components) or 1
    score = round(
        sum(STATUS_POINTS.get(c["status"], 0) * c["weight"] for c in components)
        / total_weight
    )

    # Deterministic, stable order: severity then input name.
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    flags.sort(key=lambda f: (severity_rank.get(f.get("severity", "info"), 3), f.get("input", "")))

    verdict, rule, reason = _decide_verdict(components, flags, score)

    section = {
        "verdict": verdict,
        "verdictReason": reason,
        "rule": rule,
        "score": score,
        "territory": ctx.territory,
        "programme": ctx.programme,
        "currencySymbol": ctx.currency_symbol,
        "components": components,
        "flags": flags,
        "flagCounts": {
            severity: sum(1 for f in flags if f.get("severity") == severity)
            for severity in ("critical", "warning", "info")
        },
        "methodology": METHODOLOGY,
        "computedOn": ctx.today.isoformat(),
    }
    logger.info(
        "Financial readiness: territory=%s verdict=%s score=%d rule=%s flags=%s",
        ctx.territory, verdict, score, rule, section["flagCounts"],
    )
    return section
