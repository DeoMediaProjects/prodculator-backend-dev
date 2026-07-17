"""Territory data integrity validator for production analysis reports.

Structural assertions for builder-generated reports and shared calculation
logic (corrected rebate computation).

Usage::

    from app.modules.reports.validator import ReportValidator

    report, warnings = ReportValidator.assert_integrity(report, datasets)
"""
from __future__ import annotations

import logging

from app.modules.reports.helpers import (  # noqa: F401 — re-exported for backward compat
    STALE_DAYS,
    DEFAULT_ATL_PCT,
    TAX_CREDIT_RATE_TYPES,
    STATIC_FX_TO_GBP,
    TERMINAL_LABELS,
    prog_name as _prog_name,
    index_incentives as _index_incentives,
    index_incentives_by_territory as _index_incentives_by_territory,
    best_incentive as _best_incentive,
    to_float as _to_float,
    is_zero_rate as _is_zero_rate,
    format_rate as _format_rate,
    format_cap as _format_cap,
    format_money as _format_money,
    format_millions as _format_millions,
    currency_symbol as _currency_symbol,
    budget_to_display as _budget_to_display,
    parse_money_string as _parse_money_string,
)

logger = logging.getLogger(__name__)

# Backward-compatible aliases for the underscore-prefixed module-level names
# that callers (service.py, tests) import directly.
_STALE_DAYS = STALE_DAYS
_DEFAULT_ATL_PCT = DEFAULT_ATL_PCT
_TAX_CREDIT_RATE_TYPES = TAX_CREDIT_RATE_TYPES


class ReportValidator:
    """Post-process a sanitised report dict against the source datasets."""

    @classmethod
    def assert_integrity(
        cls, report: dict, datasets: dict
    ) -> tuple[dict, list[str]]:
        """Lightweight structural assertions for builder-generated reports.

        Unlike the removed ``validate()`` (which patched AI-generated reports),
        this method only *checks* that the builder output is internally
        consistent.  It does not modify deterministic fields — the builder
        already set them correctly.

        The only mutations it makes are:
        - Sorting locationRankings by descending score
        - Injecting section explainers (static text)
        - Patching production format (user-submitted, must be authoritative)
        """
        warnings: list[str] = []

        cls._assert_required_sections(report, warnings)
        cls._assert_score_bounds(report, warnings)
        cls._assert_financial_consistency(report, datasets, warnings)
        cls._assert_territory_coverage(report, datasets, warnings)
        cls._assert_no_null_narratives(report, warnings)

        # Sort locationRankings by descending score (builder computes scores
        # but the merge step may have changed the order via AI dimensions)
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
                    summary["recommendedTerritory"] = top["name"]
                    if isinstance(top.get("score"), int):
                        summary["recommendedTerritoryScore"] = top["score"]

                    # Refresh financial headline from territory_financials
                    # so the headline always matches the top-ranked territory
                    territory_financials = datasets.get("_territory_financials") or {}
                    tf = territory_financials.get(top["name"])
                    if tf:
                        summary["recommendedTerritoryRebate"] = (
                            tf.get("net_rebate")
                            or tf.get("gross_rebate")
                        )
                        summary["headlineNetBudget"] = tf.get("headline_net_budget")

                    # Refresh payment speed from incentive data
                    by_territory = _index_incentives_by_territory(
                        datasets.get("incentives", [])
                    )
                    rows = by_territory.get(top["name"], [])
                    if rows:
                        best = _best_incentive(
                            rows, datasets.get("_production_format")
                        )
                        timeline = best.get("payment_timeline_notes")
                        if timeline:
                            summary["recommendedTerritoryPaymentSpeed"] = timeline

        # Static text — always inject
        cls._inject_section_explainers(report, datasets)

        # Production format must match user input
        cls._patch_production_format(
            report, datasets.get("_production_format"), warnings
        )

        if warnings:
            logger.info(
                "assert_integrity found %d issues: %s",
                len(warnings),
                "; ".join(warnings[:10]),
            )
        return report, warnings

    @classmethod
    def _assert_required_sections(
        cls, report: dict, warnings: list[str]
    ) -> None:
        """Check that all top-level report sections exist."""
        required = [
            "locationRankings", "incentiveEstimates", "executiveSummary",
            "genre", "tone", "scale", "complexity",
        ]
        for key in required:
            if key not in report or report[key] is None:
                warnings.append(f"[structure] missing required section: {key}")

    @classmethod
    def _assert_score_bounds(cls, report: dict, warnings: list[str]) -> None:
        """Verify all scores are within 0-100."""
        dims = (
            "score", "costEfficiency", "crewDepth", "infrastructure",
            "incentiveStrength", "incentiveReliability", "currencyAdvantage",
        )
        for loc in report.get("locationRankings", []):
            if not isinstance(loc, dict):
                continue
            name = loc.get("name", "?")
            for dim in dims:
                val = loc.get(dim)
                if isinstance(val, (int, float)) and not (0 <= val <= 100):
                    loc[dim] = max(0, min(100, int(val)))
                    warnings.append(
                        f"[scores] {name}.{dim} clamped to 0-100 (was {val})"
                    )

    @classmethod
    def _assert_financial_consistency(
        cls, report: dict, datasets: dict, warnings: list[str]
    ) -> None:
        """Check that incentive estimates reference valid DB programmes."""
        incentives_by_program = _index_incentives(
            datasets.get("incentives", [])
        )
        for est in report.get("incentiveEstimates", []):
            if not isinstance(est, dict):
                continue
            prog = est.get("programName", "")
            if prog and prog not in incentives_by_program and prog.lower() not in incentives_by_program:
                warnings.append(
                    f"[financial] programName '{prog}' not found in DB incentives"
                )

    @classmethod
    def _assert_territory_coverage(
        cls, report: dict, datasets: dict, warnings: list[str]
    ) -> None:
        """Warn if ranked territories have no matching incentive data."""
        incentives = datasets.get("incentives", [])
        db_territories = {
            (row.get("territory") or "").lower()
            for row in incentives
            if isinstance(row, dict)
        }
        for loc in report.get("locationRankings", []):
            if not isinstance(loc, dict):
                continue
            name = (loc.get("name") or "").lower()
            if name and name not in db_territories:
                warnings.append(
                    f"[coverage] ranked territory '{loc.get('name')}' has no DB incentive data"
                )

    @classmethod
    def _assert_no_null_narratives(
        cls, report: dict, warnings: list[str]
    ) -> None:
        """Check that AI-narrative fields were filled (not left as None)."""
        narrative_fields = ("genre", "tone", "scale", "complexity")
        for field in narrative_fields:
            if report.get(field) is None:
                warnings.append(f"[narrative] top-level '{field}' is None")

        summary = report.get("executiveSummary")
        if isinstance(summary, dict) and not summary.get("keyInsights"):
            warnings.append("[narrative] executiveSummary.keyInsights is empty")

        for loc in report.get("locationRankings", []):
            if not isinstance(loc, dict):
                continue
            name = loc.get("name", "?")
            if not loc.get("reasoning"):
                warnings.append(f"[narrative] {name} has no reasoning")
            for dim in ("costEfficiency", "crewDepth", "infrastructure"):
                if loc.get(dim) is None:
                    warnings.append(f"[narrative] {name}.{dim} is None")

    # ── corrected rebate calculation ─────────────────────────────────────────

    _REBATE_CAP_STATIC_FX: dict[str, float] = {
        "ZAR": 23.8,   # R1 -> GBP0.042 (conservative)
        "AUD": 1.95,
        "USD": 1.27,
        "EUR": 1.17,
        "CAD": 1.75,
        "NZD": 2.15,
        "KRW": 1680.0,  # KRW200M ≈ £119K
        "INR": 104.0,   # INR300M ≈ £2.9M
        "JPY": 188.0,   # JPY1B ≈ £5.3M
    }

    @classmethod
    def _compute_corrected_rebate(
        cls,
        db_row: dict,
        budget_gbp: float,
        territory_incentives: dict[str, list[dict]],
        production_format: str | None = None,
        fx_rate_to_gbp: float | None = None,
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

        # Step 1 — qualifying spend: type-aware calculation
        #
        # 'labour'      -> rate applies to qualifying labour only; use labour_pct
        # 'pdv'         -> rate applies to PDV/VFX work only; use pdv_pct
        # 'local_spend' -> rate applies to in-territory spend; apply cap_pct if set
        # 'total'       -> rate applies to all qualifying spend; apply cap_pct if set
        qs_type = (db_row.get("qualifying_spend_type") or "total").lower()
        qualifying_spend_note: str | None = None

        if qs_type == "labour":
            # No fabricated ratios (handoff \u00a76): a labour-only credit needs a
            # SOURCED labour share to compute a number. Without one there is no
            # honest rebate figure \u2014 the programme is presented without a
            # computed working rather than with a confident wrong number.
            labour_pct = _to_float(db_row.get("qualifying_spend_labour_pct"))
            if labour_pct is None:
                return None
            qualifying_spend = budget_gbp * (labour_pct / 100.0)
            qualifying_spend_pct = labour_pct
            qualifying_spend_note = (
                f"Labour-only credit: rate applies to qualifying labour expenditure "
                f"(sourced at {labour_pct:.0f}% of total budget). "
                f"Actual credit depends on your specific payroll split \u2014 "
                f"verify with a production accountant before including in investor documents."
            )
        elif qs_type == "pdv":
            # Same rule for PDV/VFX-only credits \u2014 no sourced share, no number.
            pdv_pct = _to_float(db_row.get("qualifying_spend_labour_pct"))
            if pdv_pct is None:
                return None
            qualifying_spend = budget_gbp * (pdv_pct / 100.0)
            qualifying_spend_pct = pdv_pct
            qualifying_spend_note = (
                f"PDV credit: rate applies to qualifying post-production, VFX, and digital "
                f"work only (sourced at {pdv_pct:.0f}% of total budget). "
                f"Does not apply to principal photography costs."
            )
        else:
            # 'total' or 'local_spend' — apply qualifying_spend_cap_pct if set
            qs_cap_pct = _to_float(db_row.get("qualifying_spend_cap_pct"))
            if qs_cap_pct is not None and 0 < qs_cap_pct <= 100:
                qualifying_spend = budget_gbp * (qs_cap_pct / 100.0)
            else:
                qualifying_spend = budget_gbp
            qualifying_spend_pct = (qualifying_spend / budget_gbp * 100) if budget_gbp > 0 else 100
            if qs_type == "local_spend":
                qualifying_spend_note = (
                    "Local-spend credit: rate applies to qualifying in-territory expenditure "
                    "only, not the total production budget. The figure shown assumes the full "
                    "qualifying spend is incurred in this territory."
                )

        # Step 2 — rate-tier logic and budget-cap programme selection
        rate_tier_raw = db_row.get("rate_tier_json")
        cap_amount = _to_float(db_row.get("cap_amount"))
        programme_note: str | None = None
        switched_programme: str | None = None
        alt: dict | None = None  # set if programme switch occurs

        # If budget exceeds the programme's hard cap, this programme may not
        # apply.  Check whether the territory has an alternative programme.
        #
        # cap_basis = 'core_costs' means the cap is measured against core
        # production costs (pre-production, principal photography, post-
        # production), NOT total budget.  We still switch to the alternative
        # programme for the financial model (conservative) but append an
        # advisory note that the original programme may still apply if the
        # producer's core costs fall below the threshold.
        if cap_amount is not None and cap_amount > 0 and budget_gbp > cap_amount:
            territory = db_row.get("territory", "")
            alt_rows = territory_incentives.get(territory, [])
            # Find a primary programme without a cap (or with a higher cap).
            # Exclude supplementary credits (e.g. VFX Expenditure Credit) — these
            # apply only to a subset of spend and must not be used as a full-budget
            # replacement when the primary programme is capped out.
            alternatives = [
                r for r in alt_rows
                if _prog_name(r) != _prog_name(db_row)
                and (_to_float(r.get("cap_amount")) is None or (_to_float(r.get("cap_amount")) or 0) >= budget_gbp)
                and not _is_zero_rate(r.get("rate_gross"), r.get("rate_net"))
                and not r.get("is_supplementary")
            ]
            if alternatives:
                alt = _best_incentive(alternatives, production_format)
                switched_programme = _prog_name(alt)
                cap_basis = (db_row.get("cap_basis") or "total_budget").lower()
                programme_note = (
                    f"Budget exceeds {_prog_name(db_row) or 'programme'} cap "
                    f"of {_format_cap(cap_amount, db_row.get('cap_currency') or 'GBP')} — "
                    f"{switched_programme or 'alternative programme'} applies instead"
                )
                if cap_basis == "core_costs":
                    programme_note += (
                        f". NOTE: the {_prog_name(db_row)} cap applies to core "
                        f"production costs (pre-production, principal photography, "
                        f"post-production) — NOT total budget. If your core costs "
                        f"are below "
                        f"{_format_cap(cap_amount, db_row.get('cap_currency') or 'GBP')}, "
                        f"{_prog_name(db_row)} may still apply at the higher rate "
                        f"— verify with your production accountant before accepting "
                        f"{switched_programme}"
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
                # tier_type on tiers[0] determines how to interpret the set:
                #
                # "spend_boundary" (UK IFTC, Spain, Canary Islands, Ireland):
                #   Different rates apply to different portions of the qualifying spend.
                #   The boundary amount is parsed from the tier label and a blended rate
                #   is calculated (e.g. 53% on first £15M, 34% on remainder → blended).
                #
                # "informational" (France TRIP, Czech, Malta, US states, etc.):
                #   Tiers describe categories or conditions — the DB headline rate_gross/
                #   rate_net is the correct rate for the primary calculation scenario.
                #   No blending — use the headline rate directly.
                #
                # All rows are stamped by migration n6o7p8q9r0s1. A missing tier_type
                # is a data error; treat as informational (safe default — avoids
                # fabricating a blended rate from an unclassified label).
                import re as _re
                first_tier_type = (tiers[0].get("tier_type") or "").lower()
                tier_boundary: float | None = None

                if first_tier_type == "spend_boundary":
                    for tier in tiers:
                        label = str(tier.get("label", "")).lower()
                        amount_match = _re.search(
                            r'[\u00a3$\u20ac](\d+(?:\.\d+)?)\s*[mM]', label
                        )
                        if amount_match:
                            tier_boundary = float(amount_match.group(1)) * 1_000_000
                            break

                if tier_boundary is not None:
                    if qualifying_spend <= tier_boundary:
                        effective_rate_gross = _to_float(tiers[0].get("rate_gross")) or effective_rate_gross
                        effective_rate_net = _to_float(tiers[0].get("rate_net")) or effective_rate_net
                    else:
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

        # Step 3 — ATL (above-the-line) deduction
        #
        # Tax credit programmes typically exclude ATL costs (writer, director,
        # lead cast fees) from qualifying spend.  However two classes of
        # programme must NOT receive this deduction:
        #
        # a) atl_exempt = True: programmes where ATL/BTL distinction does not
        #    exist by statute (e.g. UK AVEC, which applies a flat rate to ALL
        #    qualifying UK expenditure regardless of ATL/BTL split).
        #
        # b) qualifying_spend_type in ("labour", "pdv"): the labour/PDV
        #    percentage already represents a BTL-weighted estimate of qualifying
        #    spend.  Applying a second ATL deduction on top double-discounts.
        #
        # Cash rebate programmes (Hungary NFI, Malta MFC) already skip ATL via
        # the rate_type check below.
        active_row = alt if alt is not None else db_row
        rate_type = (active_row.get("rate_type") or db_row.get("rate_type") or "").lower()
        atl_exempt = bool(active_row.get("atl_exempt") or db_row.get("atl_exempt"))
        cap_per_person = _to_float(db_row.get("cap_per_person"))
        atl_deduction_note: str | None = None
        atl_deduction_amount: float = 0.0
        qualifying_spend_before_atl = qualifying_spend
        apply_atl = (
            rate_type in _TAX_CREDIT_RATE_TYPES
            and not atl_exempt
            and qs_type not in ("labour", "pdv")
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
        elif cap_per_person is not None and cap_per_person > 0:
            # Per-person wage cap set but programme is not a tax-credit type (e.g.
            # transferable_tax_credit such as Georgia EIIA) — no blanket ATL deduction
            # applies, but the per-person cap must still be surfaced as a risk note.
            currency_label = db_row.get("currency") or "GBP"
            cap_currency = db_row.get("cap_per_person_currency") or currency_label
            atl_deduction_note = (
                f"Per-person wage cap: {_currency_symbol(cap_currency)}{cap_per_person:,.0f}"
                f" per individual \u2014 wages above this threshold are not qualifying spend."
                f" On high-budget productions with expensive talent, this materially reduces"
                f" the actual credit received. Verify full payroll breakdown before modelling."
            )

        # Step 4 — compute rebate
        gross_rebate = qualifying_spend * (effective_rate_gross / 100.0)
        net_rebate = qualifying_spend * (effective_rate_net / 100.0) if effective_rate_net else gross_rebate

        # Step 5 — hard rebate cap enforcement
        #
        # rebate_cap_amount is the maximum grant issued per project (e.g. South
        # Africa R25M).  This is DISTINCT from cap_amount, which is a budget
        # threshold that triggers programme switching.
        #
        # If the computed gross_rebate exceeds the cap (converted to GBP), the
        # rebate is reduced to the cap.  fx_rate_to_gbp (GBP->cap_currency) is
        # passed in from the caller when live FX data is available; otherwise the
        # static fallback table is used (erring conservative — smaller GBP cap).
        rebate_cap_note: str | None = None
        rebate_cap_raw = _to_float(db_row.get("rebate_cap_amount"))
        if rebate_cap_raw and rebate_cap_raw > 0:
            cap_currency = (db_row.get("rebate_cap_currency") or "GBP").upper()
            if cap_currency == "GBP":
                cap_gbp: float | None = rebate_cap_raw
            elif fx_rate_to_gbp and fx_rate_to_gbp > 0:
                cap_gbp = rebate_cap_raw / fx_rate_to_gbp
            else:
                fallback = cls._REBATE_CAP_STATIC_FX.get(cap_currency)
                cap_gbp = (rebate_cap_raw / fallback) if fallback else None

            if cap_gbp is not None and gross_rebate > cap_gbp:
                cap_sym = _currency_symbol(cap_currency)
                rebate_cap_note = (
                    f"Rebate capped at {cap_sym}"
                    f"{rebate_cap_raw / 1_000_000:g}M {cap_currency} per project \u2014 "
                    f"calculated rebate exceeds this ceiling and has been reduced to "
                    f"the programme maximum"
                )
                gross_rebate = cap_gbp
                net_rebate = cap_gbp

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
        if rebate_cap_note:
            result["rebate_cap_note"] = rebate_cap_note
        if qualifying_spend_note:
            result["qualifying_spend_note"] = qualifying_spend_note
        return result

    # ── production format enforcement ────────────────────────────────────────

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

    # ── section explainers ───────────────────────────────────────────────────

    @classmethod
    def _inject_section_explainers(cls, report: dict, datasets: dict) -> None:
        """Inject hardcoded plain-English section explainers per v3 spec Section 09."""
        # Gather template variables
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
                f"How we score territories: Each territory is rated 0\u2013100 across six "
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
                "breakdown of its incentive programmes and location-specific "
                "considerations drawn from your script analysis."
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
                "tier (within 0.5x\u20132x of your budget), and territory relevance. We "
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
# All helper functions have been extracted to app/modules/reports/helpers.py
# and are imported at the top of this file with underscore-prefixed aliases
# (e.g. _prog_name, _index_incentives, etc.) for full backward compatibility.
