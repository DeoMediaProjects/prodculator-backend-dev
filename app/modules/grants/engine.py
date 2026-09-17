"""Grants & Funding Engine v2: gates, scoring, and the one service the report calls.

PIPELINE (Developer Guide §5, IMPLEMENTATION_SEQUENCE.md steps 3-8)

    records -> routing/record-state -> verification & current cycle -> hard gates
            -> score (eligible only) -> strategic portfolio -> package entitlement
            -> report payload

THE RULE v1 BROKE
-----------------
v1 initialised the score inside the nationality gate and applied the budget bounds
after five signals had already accumulated, so a record could carry points it had not
earned the right to. Logic Guide §5 is explicit: "a programme that fails a hard gate
has no meaningful score for presentation. Do not assign 0 and keep it in the same
ranking list; mark it INELIGIBLE with gate reasons." Here every gate runs, and only
then — if nothing excluded the record — does anything add points.

THREE OUTCOMES, NOT TWO
-----------------------
An unknown fact is not a failure. The master is full of records that state no format
(95), no stage (110), no territory (8) and no official source (149), and a gate that
fails closed on unknown would delete a third of the database while looking like
rigour. So a gate returns one of:

  pass + tested      the fact was checked and satisfied
  pass + not tested  the fact was unknown; the record survives and is marked
                     CONDITIONAL, naming what is missing
  fail               the fact was checked and contradicted

The only exclusions are the third kind, plus the record-state and cycle exclusions
the contract names outright: archived, reclassified, retired split parents, closed
rounds, suspended programmes and invite-only calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.formats import canonical_format
from app.core.regions import regions_for_territories, satisfies

from app.modules.grants import normalise as N
from app.modules.grants.portfolio import select_portfolio
from app.modules.grants.schemas_v2 import (
    CoProductionContext,
    DisplayFields,
    GrantsReportPayload,
    HardGateResult,
    MatchResult,
    NarrativeContext,
    ScoreComponent,
    VerificationState,
)
from app.modules.grants.v2_contracts import (
    ACTIONABLE_STATUSES,
    CAVEATS,
    CLOSING_SOON_DAYS,
    GRANTS_DATABASE_VERSION,
    NON_ACTIONABLE_STATUSES,
    ROUTING_GRANTS,
    SCORE_REASON_LABELS,
    SCORE_WEIGHTS,
    SCORING_WEIGHTS_VERSION,
    STALE_EXCLUDE_MONTHS,
    STALE_FLAG_MONTHS,
    display_limit,
)

logger = logging.getLogger(__name__)

#: Maps the coarse continent labels stored on grant rows onto the canonical region
#: vocabulary. Mirrors reports.matching._REGION_TO_CONTINENT; duplicated rather than
#: imported so this module never depends on the reports package (Developer Guide §13
#: requires the dependency to run reports -> grants, one way only).
_REGION_TO_CONTINENT: dict[str, str] = {
    "Africa": "Africa",
    "Asia": "Asia-Pacific",
    "Southeast Asia": "Asia-Pacific",
    "Central Asia": "Asia-Pacific",
    "Oceania": "Asia-Pacific",
    "Europe": "Europe",
    "Eastern Europe": "Europe",
    "Middle East": "Middle East",
    "Latin America": "Americas",
    "North America": "Americas",
}


@dataclass
class ProjectFacts:
    """Everything the engine knows about the project, normalised once.

    Developer Guide §7: reuse the existing report inputs and script-derived Project
    DNA; do not build a second grants questionnaire. Every field is optional because
    every one of them is genuinely unknown for some real report, and an unknown fact
    must reach the gate as None rather than as a default that looks like an answer.
    """

    format: str | None = None
    genres: list[str] = field(default_factory=list)
    budget_usd: float | None = None
    home_country: str | None = None
    producer_country: str | None = None
    script_origin: str | None = None
    ranked_territories: list[str] = field(default_factory=list)
    production_stage: str | None = None
    #: Whether the producer STATED their stage, or the engine inferred it from the
    #: filming dates. No intake field asks for stage, so it is almost always derived —
    #: and Developer Guide §5 forbids gating on an inferred fact ("use explicit
    #: producer/project facts only. Never infer"). A derived stage therefore qualifies
    #: a record rather than excluding it. Gating on it would silently delete around
    #: 100 funds from a real report on the strength of a date arithmetic guess.
    production_stage_declared: bool = False
    co_production_status: str | None = None
    co_production_interest: str | None = None
    treaty_context: CoProductionContext | None = None
    package: str | None = None

    def origin_regions(self) -> frozenset[str]:
        """Regions the FILMMAKERS come from, not where they plan to shoot.

        PROD-FIX-008: a regional fund restricts by who you are, so treating a chosen
        shooting territory as evidence would qualify a UK production for an African
        filmmakers' fund purely by deciding to shoot in Cape Town.
        """
        return regions_for_territories([self.script_origin, self.home_country,
                                        self.producer_country])

    def affinity_continents(self) -> set[str]:
        """Continents that touch the project, shooting territories included.

        Continent affinity is a soft signal rather than a gate, so shooting
        territories legitimately count towards it.
        """
        regions = self.origin_regions() | regions_for_territories(self.ranked_territories)
        return {_REGION_TO_CONTINENT[r] for r in regions if r in _REGION_TO_CONTINENT}


def _text(record: dict, *keys: str) -> str:
    """First non-empty value among *keys*, as stripped text."""
    for key in keys:
        value = record.get(key)
        if not N.is_unknown(value):
            return str(value).strip()
    return ""


class GrantsMatchingService:
    """Evaluates the full grants universe against one project.

    Nothing here is package-aware except ``build_payload``'s final slice. Logic Guide
    §6: "All paid packages search the full database... Match and rank everything
    first; entitlement is the final display layer."
    """

    def __init__(self, *, today: date | None = None) -> None:
        # Injected rather than read from the clock so a regression case pins to a
        # date. Every existing matcher test in this repo does this.
        self.today = today or date.today()
        self.admin_flags: list[dict[str, Any]] = []

    # ── gates ────────────────────────────────────────────────────────────────

    def _gate_routing(self, record: dict) -> HardGateResult:
        routing = _text(record, "routing").upper() or ROUTING_GRANTS
        # The old grants table may not have a routing column.  This known
        # Film London programme is a finance market, not direct grant money;
        # do not let a missing legacy routing value put it back in Grants.
        title = _text(record, "canonical_title", "title", "name").casefold()
        normalized_title = " ".join(
            "".join(char if char.isalnum() else " " for char in title).split()
        )
        if normalized_title == "film london production finance market":
            routing = "MARKETS_LABS_WIP"
        if routing != ROUTING_GRANTS:
            return HardGateResult(
                gate="routing", passed=False,
                reason_code="GATE_ROUTING_NOT_GRANTS",
                reason=f"Routed to {routing}, not Grants/Funds.",
            )
        return HardGateResult(gate="routing", passed=True,
                              reason_code="GATE_ROUTING_GRANTS_FUNDS")

    def _gate_record_state(self, record: dict) -> HardGateResult:
        lifecycle = _text(record, "lifecycle_state").upper() or "LIVE"
        codes = {
            "ARCHIVED": ("GATE_RECORD_ARCHIVED", "Archived — retained for history only."),
            "RECLASSIFIED": ("GATE_RECORD_RECLASSIFIED", "Reclassified to another engine."),
            "SPLIT_PARENT": ("GATE_SPLIT_PARENT_RETIRED",
                             "Superseded by its individual strands."),
            "SUSPENDED": ("GATE_CYCLE_SUSPENDED", "Programme suspended."),
            # Outside the frozen v2 inventory, or a duplicate of a record that is
            # inside it. Held back until an admin confirms which identity survives —
            # showing both spellings of one fund reads as two opportunities.
            "NEEDS_REVIEW": ("GATE_RECORD_NEEDS_REVIEW",
                             "Held for admin review before paid matching."),
        }
        if lifecycle in codes:
            code, reason = codes[lifecycle]
            return HardGateResult(gate="record_state", passed=False,
                                  reason_code=code, reason=reason)

        # paid_match_eligible is an operational surfacing flag. DATA_README.md is
        # explicit that it is "a migration/runtime hint, not a permanent claim that a
        # round is open" — so it excludes when false, but never admits on its own.
        paid = N.parse_bool(record.get("paid_match_eligible"))
        if paid is False:
            return HardGateResult(
                gate="record_state", passed=False,
                reason_code="GATE_NOT_PAID_MATCH_ELIGIBLE",
                reason="Not currently surfaced for paid matching.",
            )
        return HardGateResult(gate="record_state", passed=True,
                              reason_code="GATE_RECORD_LIVE")

    def _gate_current_cycle(self, record: dict) -> HardGateResult:
        status = _text(record, "canonical_status") or N.canonical_status(
            _text(record, "current_status", "status")
        )
        if status in NON_ACTIONABLE_STATUSES:
            codes = {
                "CLOSED_CURRENT_ROUND": "GATE_CYCLE_CLOSED",
                "SUSPENDED": "GATE_CYCLE_SUSPENDED",
                "INVITE_ONLY": "GATE_CYCLE_INVITE_ONLY",
                "ROUTED_OUT": "GATE_ROUTING_NOT_GRANTS",
            }
            return HardGateResult(
                gate="current_cycle", passed=False,
                reason_code=codes.get(status, "GATE_CYCLE_CLOSED"),
                reason=f"Current round is {status.replace('_', ' ').lower()}.",
            )

        verified = N.parse_bool(record.get("current_cycle_verified"))
        if status in ACTIONABLE_STATUSES and verified is True:
            code = "GATE_CYCLE_ROLLING" if status == "ROLLING" else "GATE_CYCLE_OPEN"
            return HardGateResult(gate="current_cycle", passed=True, reason_code=code)

        # Identity-verified but current-cycle-unverified. Logic Guide §3 keeps the
        # record and forbids presenting it as actionable — 124 of the 204
        # paid-match-eligible records in the master are in exactly this state, so
        # excluding them would delete most of the database, and promoting them would
        # assert 124 open rounds nobody has checked.
        return HardGateResult(
            gate="current_cycle", passed=True, tested=False,
            reason_code="GATE_CYCLE_UNVERIFIED",
            reason="Current round and next dates are not verified.",
        )

    def _gate_deadline(self, record: dict) -> HardGateResult:
        kind, deadline = N.parse_deadline(
            _text(record, "next_deadline", "application_deadline", "deadline"),
            recurrence=record.get("recurrence"),
        )
        if kind == "ROLLING":
            return HardGateResult(gate="deadline", passed=True,
                                  reason_code="GATE_DEADLINE_ROLLING")
        if kind == "ISO_DATE" and deadline is not None:
            if deadline < self.today:
                self.admin_flags.append({
                    "opportunity": _text(record, "canonical_title", "title"),
                    "flag": "dead_deadline",
                    "detail": f"deadline {deadline.isoformat()} has passed",
                })
                return HardGateResult(
                    gate="deadline", passed=False,
                    reason_code="GATE_DEADLINE_PASSED",
                    reason=f"Deadline passed on {deadline.isoformat()}.",
                )
            return HardGateResult(gate="deadline", passed=True,
                                  reason_code="GATE_DEADLINE_FUTURE")
        if kind in ("TBC", "ISO_MONTH_PARTIAL"):
            return HardGateResult(
                gate="deadline", passed=True, tested=False,
                reason_code="GATE_DEADLINE_TBC_UNVERIFIED",
                reason="Next cycle dates are not yet published.",
            )
        if kind == "FREE_TEXT":
            self.admin_flags.append({
                "opportunity": _text(record, "canonical_title", "title"),
                "flag": "deadline_unparseable",
                "detail": f"deadline '{_text(record, 'next_deadline')}' is not a date",
            })
            return HardGateResult(
                gate="deadline", passed=True, tested=False,
                reason_code="GATE_DEADLINE_UNPARSEABLE",
                reason="Deadline is stated as prose — confirm on the official source.",
            )
        return HardGateResult(gate="deadline", passed=True, tested=False,
                              reason_code="GATE_DEADLINE_TBC_UNVERIFIED",
                              reason="No deadline stated.")

    def _gate_format(self, record: dict, facts: ProjectFacts) -> HardGateResult:
        accepted = N.parse_format_list(
            record.get("eligible_formats_canonical") or record.get("eligible_formats")
        )
        wanted = canonical_format(facts.format)
        if accepted is None:
            return HardGateResult(gate="format", passed=True, tested=False,
                                  reason_code="GATE_FORMAT_UNSTATED")
        if not wanted:
            return HardGateResult(gate="format", passed=True, tested=False,
                                  reason_code="GATE_FORMAT_UNSTATED")
        if "all" in accepted or wanted in accepted:
            return HardGateResult(gate="format", passed=True,
                                  reason_code="GATE_FORMAT_MATCH")
        return HardGateResult(
            gate="format", passed=False, reason_code="GATE_FORMAT_MISMATCH",
            reason=f"Funds {', '.join(accepted)} — not {wanted.replace('_', ' ')}.",
        )

    def _gate_stage(self, record: dict, facts: ProjectFacts) -> HardGateResult:
        accepted = N.parse_stage_list(
            record.get("production_stage_canonical") or record.get("production_stage")
        )
        wanted = (facts.production_stage or "").strip().lower() or None
        if accepted is None or wanted is None:
            return HardGateResult(gate="stage", passed=True, tested=False,
                                  reason_code="GATE_STAGE_UNSTATED")
        if wanted in accepted:
            return HardGateResult(gate="stage", passed=True,
                                  reason_code="GATE_STAGE_MATCH")
        if not facts.production_stage_declared:
            # The stage was inferred from filming dates, not stated. A mismatch
            # against an inferred fact is not evidence of ineligibility, so it
            # qualifies the record instead of deleting it.
            return HardGateResult(
                gate="stage", passed=True, tested=False,
                reason_code="GATE_STAGE_UNSTATED",
                reason=f"Supports {', '.join(accepted)}; confirm your project stage.",
            )
        return HardGateResult(
            gate="stage", passed=False, reason_code="GATE_STAGE_MISMATCH",
            reason=f"Supports {', '.join(accepted)} — not {wanted}.",
        )

    def _gate_budget(self, record: dict, facts: ProjectFacts) -> HardGateResult:
        low, high = record.get("budget_min_usd"), record.get("budget_max_usd")
        budget = facts.budget_usd

        # Developer Guide §5: "Apply only verified min/max budget fields", and §1:
        # "Total programme/call pools are not per-project maximums". An unverified
        # bound is a number somebody wrote down, not a rule — gating on it would
        # exclude a project for exceeding a figure the programme never set.
        if low is None and high is None:
            return HardGateResult(gate="budget", passed=True, tested=False,
                                  reason_code="GATE_BUDGET_UNVERIFIED_SKIPPED")
        if budget is None:
            return HardGateResult(gate="budget", passed=True, tested=False,
                                  reason_code="GATE_BUDGET_UNVERIFIED_SKIPPED")
        if high is not None and budget > float(high):
            return HardGateResult(
                gate="budget", passed=False,
                reason_code="GATE_BUDGET_ABOVE_VERIFIED_MAX",
                reason="Project budget is above this fund's stated ceiling.",
            )
        if low is not None and budget < float(low):
            return HardGateResult(
                gate="budget", passed=False,
                reason_code="GATE_BUDGET_BELOW_VERIFIED_MIN",
                reason="Project budget is below this fund's stated floor.",
            )
        return HardGateResult(gate="budget", passed=True,
                              reason_code="GATE_BUDGET_WITHIN_VERIFIED_BOUNDS")

    def _gate_nationality(self, record: dict, facts: ProjectFacts) -> HardGateResult:
        if not record.get("nationality_required"):
            return HardGateResult(gate="nationality", passed=True,
                                  reason_code="GATE_NATIONALITY_NOT_REQUIRED")
        territory = _text(record, "territory").lower()
        if not territory:
            return HardGateResult(gate="nationality", passed=True, tested=False,
                                  reason_code="GATE_NATIONALITY_UNKNOWN",
                                  reason="Fund states a nationality rule but no territory.")
        home = (facts.home_country or "").lower()
        producer = (facts.producer_country or "").lower()
        origin = (facts.script_origin or "").lower()
        ranked = {t.lower() for t in facts.ranked_territories}

        if not (home or producer):
            return HardGateResult(gate="nationality", passed=True, tested=False,
                                  reason_code="GATE_NATIONALITY_UNKNOWN",
                                  reason="Your applicant nationality has not been stated.")
        if territory in (home, producer):
            return HardGateResult(gate="nationality", passed=True,
                                  reason_code="GATE_NATIONALITY_MET")
        if territory in ranked or (origin and territory == origin):
            # Reachable only through a local partner. Kept, with the caveat — v1 did
            # the same and scored it -1, which is the honest position: it is an
            # opportunity conditional on a structure the producer does not yet have.
            return HardGateResult(
                gate="nationality", passed=True,
                reason_code="GATE_NATIONALITY_MET",
                reason="Reachable only via a local co-production structure.",
            )
        return HardGateResult(
            gate="nationality", passed=False,
            reason_code="GATE_NATIONALITY_NO_ROUTE",
            reason=f"Restricted to {_text(record, 'territory')} applicants.",
        )

    def _gate_region(self, record: dict, facts: ProjectFacts) -> HardGateResult:
        from app.modules.reports.matching import _region_list  # local: avoids a cycle

        required = _region_list(record.get("eligible_regions"))
        if not required:
            return HardGateResult(gate="territory_region", passed=True,
                                  reason_code="GATE_REGION_UNRESTRICTED")
        origins = facts.origin_regions()
        label = ", ".join(required)
        if not origins:
            # Origin unknown. Surfacing it with the restriction shown beats deleting
            # an opportunity the producer may well qualify for — but it is CONDITIONAL,
            # not eligible, because we have not established that they do.
            return HardGateResult(
                gate="territory_region", passed=True, tested=False,
                reason_code="GATE_REGION_ORIGIN_UNKNOWN",
                reason=f"Open to filmmakers from {label} — confirm your eligibility.",
            )
        if not satisfies(origins, required):
            self.admin_flags.append({
                "opportunity": _text(record, "canonical_title", "title"),
                "flag": "region_mismatch",
                "detail": f"restricted to {label}",
            })
            return HardGateResult(
                gate="territory_region", passed=False,
                reason_code="GATE_REGION_MISMATCH",
                reason=f"Restricted to filmmakers from {label}.",
            )
        return HardGateResult(gate="territory_region", passed=True,
                              reason_code="GATE_REGION_MATCH")

    def _gate_co_production(self, record: dict, facts: ProjectFacts) -> HardGateResult:
        if not record.get("co_production_required"):
            return HardGateResult(gate="co_production", passed=True,
                                  reason_code="GATE_COPRODUCTION_NOT_REQUIRED")

        treaty = facts.treaty_context
        if treaty is not None and treaty.treaty_route_identified:
            return HardGateResult(
                gate="co_production", passed=True,
                reason_code="GATE_COPRODUCTION_ROUTE_PRESENT",
                reason="A treaty co-production route has been identified.",
            )
        status = (facts.co_production_status or "").strip().lower()
        interest = (facts.co_production_interest or "").strip().lower()
        if status == "co_production_treaty":
            return HardGateResult(gate="co_production", passed=True,
                                  reason_code="GATE_COPRODUCTION_ROUTE_PRESENT")
        if status == "sole_producer" or interest == "no":
            return HardGateResult(
                gate="co_production", passed=False,
                reason_code="GATE_COPRODUCTION_REQUIRED_NO_ROUTE",
                reason="Requires an official co-production; you have declared sole "
                       "production.",
            )
        # Undecided or unstated: the route is required and not yet established.
        return HardGateResult(
            gate="co_production", passed=True, tested=False,
            reason_code="GATE_COPRODUCTION_ROUTE_UNCONFIRMED",
            reason="Requires an official co-production structure you have not "
                   "confirmed.",
        )

    def _gate_applicant_structure(self, record: dict, facts: ProjectFacts) -> HardGateResult:
        """Applicant entity requirements, distinct from nationality.

        No structured column encodes company type today — it lives in prose in
        ``eligibility_summary``/``key_rule``. Developer Guide §5 forbids inferring it,
        so this gate reports untested and names the missing fact rather than reading
        the prose and guessing. It exists now so the payload shape and the reason-code
        vocabulary are already right when that column lands.
        """
        if not record.get("nationality_required"):
            return HardGateResult(gate="applicant_structure", passed=True,
                                  reason_code="GATE_APPLICANT_STRUCTURE_OK")
        if not facts.producer_country:
            return HardGateResult(
                gate="applicant_structure", passed=True, tested=False,
                reason_code="GATE_APPLICANT_STRUCTURE_UNKNOWN",
                reason="Applicant company jurisdiction has not been stated.",
            )
        return HardGateResult(gate="applicant_structure", passed=True,
                              reason_code="GATE_APPLICANT_STRUCTURE_OK")

    # ── verification ─────────────────────────────────────────────────────────

    def _verification(self, record: dict) -> tuple[VerificationState, list[str], bool]:
        """Verification state, its caveats, and whether staleness excludes."""
        verified_at = N.parse_date(record.get("last_verified_at"))
        caveats: list[str] = []
        excluded = False
        staleness_days = None

        if verified_at is not None:
            staleness_days = (self.today - verified_at).days
            age_months = N.months_between(verified_at, self.today)
            if age_months > STALE_EXCLUDE_MONTHS:
                excluded = True
            elif age_months > STALE_FLAG_MONTHS:
                caveats.append("CAVEAT_VERIFICATION_AGEING")

        record_verified = N.parse_bool(record.get("record_verified"))
        source_verified = N.parse_bool(record.get("official_source_verified"))
        cycle_verified = N.parse_bool(record.get("current_cycle_verified"))

        if cycle_verified and source_verified:
            composite = "VERIFIED_CURRENT"
        elif source_verified or cycle_verified:
            composite = "PARTIALLY_VERIFIED"
        elif record_verified:
            composite = "IDENTITY_ONLY"
        else:
            composite = "NEEDS_REVIEW"

        return (
            VerificationState(
                record_verified=record_verified,
                official_source_verified=source_verified,
                current_cycle_verified=cycle_verified,
                verified_at=verified_at.isoformat() if verified_at else None,
                composite_state=composite,
                staleness_days=staleness_days,
            ),
            caveats,
            excluded,
        )

    # ── scoring ──────────────────────────────────────────────────────────────

    def _score(self, record: dict, facts: ProjectFacts,
               gates: list[HardGateResult]) -> list[ScoreComponent]:
        """Points for a record that has already cleared every gate."""
        components: list[ScoreComponent] = []

        def add(code: str, **fmt: Any) -> None:
            label = SCORE_REASON_LABELS.get(code, "")
            components.append(ScoreComponent(
                reason_code=code,
                points=SCORE_WEIGHTS[code],
                detail=label.format(**fmt) if label else None,
                weight_version=SCORING_WEIGHTS_VERSION,
            ))

        territory_label = _text(record, "territory")
        territory = territory_label.lower()
        ranked = {t.lower() for t in facts.ranked_territories}
        origin = (facts.script_origin or "").lower()
        home = (facts.home_country or "").lower()

        hit_ranked = bool(territory) and territory in ranked
        hit_origin = bool(territory) and bool(origin) and territory == origin

        if hit_ranked:
            add("TERRITORY_CONSIDERED", territory=territory_label)
        if hit_origin:
            add("SCRIPT_ORIGIN_TERRITORY", territory=territory_label)

        # Consolation signals, mutually exclusive with a territory hit and with each
        # other — a fund that is already yours does not also get credit for being open
        # to everyone.
        if not (hit_ranked or hit_origin):
            if territory in ("global", "international") or "global" in territory:
                add("GLOBAL_OPEN")
            else:
                continent = _text(record, "continent")
                if continent and continent in facts.affinity_continents():
                    add("CONTINENT_AFFINITY", continent=continent)

        genre_tags = record.get("genre_tags") or []
        if isinstance(genre_tags, str):
            genre_tags = N.parse_text_list(genre_tags)
        tags = {str(t).strip().lower() for t in genre_tags if str(t).strip()}
        wanted_genres = {g.strip().lower() for g in facts.genres if g and g.strip()}
        if wanted_genres and tags:
            overlap = sorted(wanted_genres & tags)
            if overlap or "all" in tags:
                add("GENRE_OVERLAP", detail=", ".join(overlap) if overlap else "all genres")

        accepted = N.parse_format_list(
            record.get("eligible_formats_canonical") or record.get("eligible_formats")
        )
        wanted_format = canonical_format(facts.format)
        if accepted and wanted_format and wanted_format in accepted and len(accepted) <= 2:
            add("SPECIALISED_FORMAT", detail=wanted_format.replace("_", " "))

        low, high = record.get("budget_min_usd"), record.get("budget_max_usd")
        if (facts.budget_usd is not None and low is not None and high is not None
                and float(low) <= facts.budget_usd <= float(high)):
            add("BUDGET_FIT_VERIFIED")

        if record.get("nationality_required") and territory:
            if territory in (home, (facts.producer_country or "").lower()):
                add("NATIONALITY_HOME", territory=territory_label)
            elif territory in ranked or (origin and territory == origin):
                add("NATIONALITY_ROUTE_ONLY", territory=territory_label)

        region_gate = next((g for g in gates if g.gate == "territory_region"), None)
        if region_gate is not None and region_gate.reason_code == "GATE_REGION_MATCH":
            from app.modules.reports.matching import _region_list

            add("REQUIRED_REGION_FIT",
                detail=", ".join(_region_list(record.get("eligible_regions"))))

        return components

    # ── display ──────────────────────────────────────────────────────────────

    def _display_fields(self, record: dict, gates: list[HardGateResult]) -> DisplayFields:
        amount_text, amount_value, is_pool = N.parse_amount(
            record.get("max_amount_text") or record.get("max_amount")
        )
        kind, deadline_date = N.parse_deadline(
            _text(record, "next_deadline", "application_deadline", "deadline"),
            recurrence=record.get("recurrence"),
        )

        badges: list[str] = []
        if record.get("nationality_required"):
            badges.append("NATIONALITY RESTRICTION")
        if record.get("co_production_required"):
            badges.append("CO-PRODUCTION REQUIRED")
        if kind == "ROLLING":
            badges.append("ROLLING")

        days_until = None
        if deadline_date is not None:
            days_until = (deadline_date - self.today).days
            if 0 <= days_until <= CLOSING_SOON_DAYS:
                badges.append("CLOSING SOON")

        cycle_gate = next((g for g in gates if g.gate == "current_cycle"), None)
        if cycle_gate is not None and cycle_gate.reason_code == "GATE_CYCLE_UNVERIFIED":
            badges.append("CURRENT CYCLE UNVERIFIED")

        # A deadline is shown only when it is a real date or a genuine rolling
        # programme. Everything else stays None so no renderer can turn an empty
        # string into "Rolling".
        deadline_display = None
        if kind == "ISO_DATE" and deadline_date is not None:
            deadline_display = deadline_date.isoformat()
        elif kind == "ROLLING":
            deadline_display = "Rolling"

        return DisplayFields(
            title=_text(record, "canonical_title", "title") or "Untitled programme",
            funding_body=_text(record, "funding_body") or None,
            territory=_text(record, "territory") or None,
            amount=amount_text,
            deadline=deadline_display,
            official_source=_text(record, "official_source", "website_url") or None,
            amount_value=amount_value,
            amount_currency=_text(record, "currency") or None,
            amount_is_per_project=(None if amount_value is None else not is_pool),
            deadline_state=kind,
            deadline_date=deadline_date.isoformat() if deadline_date else None,
            days_until_deadline=days_until,
            opportunity_type=_text(record, "opportunity_type", "grant_type") or None,
            production_stage=N.parse_stage_list(record.get("production_stage")) or [],
            eligible_formats=N.parse_format_list(record.get("eligible_formats")) or [],
            key_rule=_text(record, "key_rule") or None,
            eligibility_summary=" ".join(
                N.parse_text_list(record.get("eligibility_summary") or record.get("eligibility"))
            ) or None,
            badges=badges,
        )

    # ── evaluation ───────────────────────────────────────────────────────────

    def evaluate(self, record: dict, facts: ProjectFacts) -> MatchResult:
        """One record against one project. Always returns a result, never None."""
        opportunity_id = str(record.get("id") or _text(record, "canonical_title", "title"))

        gates: list[HardGateResult] = [
            self._gate_routing(record),
            self._gate_record_state(record),
            self._gate_current_cycle(record),
            self._gate_deadline(record),
            self._gate_format(record, facts),
            self._gate_stage(record, facts),
            self._gate_nationality(record, facts),
            self._gate_applicant_structure(record, facts),
            self._gate_region(record, facts),
            self._gate_budget(record, facts),
            self._gate_co_production(record, facts),
        ]

        verification, verification_caveats, stale_excluded = self._verification(record)
        if stale_excluded:
            gates.append(HardGateResult(
                gate="current_cycle", passed=False,
                reason_code="GATE_VERIFICATION_STALE",
                reason=f"Not verified in over {STALE_EXCLUDE_MONTHS} months.",
            ))
            self.admin_flags.append({
                "opportunity": _text(record, "canonical_title", "title"),
                "flag": "stale_excluded",
                "detail": f"last verified {verification.verified_at}",
            })

        display = self._display_fields(record, gates)
        failed = [g for g in gates if not g.passed]

        if failed:
            return MatchResult(
                opportunity_id=opportunity_id,
                eligibility_status="INELIGIBLE",
                hard_gate_results=gates,
                raw_score=0.0,
                portfolio_rank=None,
                match_reasons=[],
                caveats=[g.reason for g in failed if g.reason],
                verification=verification,
                display_fields=display,
            )

        components = self._score(record, facts, gates)
        untested = [g for g in gates if not g.tested]

        # Status precedence: an unverified current cycle is the strongest demotion,
        # because it is the one that decides whether the report may call this
        # actionable. Any other untested fact is CONDITIONAL.
        #
        # An unstated deadline does NOT demote on its own. 147 of the 253 records
        # publish no date, and a rolling or continuously-open programme whose cycle IS
        # verified is genuinely actionable — telling a producer otherwise because no
        # date exists would suppress exactly the funds that are always open.
        cycle_unverified = any(
            g.reason_code == "GATE_CYCLE_UNVERIFIED" for g in gates
        )
        if cycle_unverified:
            status = "CURRENT_CYCLE_UNVERIFIED"
        elif untested:
            status = "CONDITIONAL"
        else:
            status = "ELIGIBLE"

        caveats = ["CAVEAT_NOT_COMMITTED_FINANCE", "CAVEAT_SELECTIVE_SUPPORT"]
        caveats.extend(verification_caveats)
        if status == "CURRENT_CYCLE_UNVERIFIED":
            caveats.append("CAVEAT_CYCLE_UNVERIFIED")
        if display.deadline_state == "ROLLING":
            caveats.append("CAVEAT_ROLLING_NO_DEADLINE")
        if display.deadline_state in ("TBC", "ISO_MONTH_PARTIAL"):
            caveats.append("CAVEAT_NEXT_CYCLE_TBC")
        if display.amount_value is None:
            caveats.append("CAVEAT_AMOUNT_NOT_VERIFIED")
        if display.amount_is_per_project is False:
            caveats.append("CAVEAT_POOL_NOT_PER_PROJECT")
        if record.get("co_production_required"):
            caveats.append("CAVEAT_TREATY_ROUTE_NOT_CERTIFICATION")
        for gate in untested:
            if gate.reason_code == "GATE_APPLICANT_STRUCTURE_UNKNOWN":
                caveats.append("CAVEAT_APPLICANT_STRUCTURE_REQUIRED")

        reasons = [c.detail for c in components if c.detail]
        reasons.extend(g.reason for g in untested if g.reason)

        return MatchResult(
            opportunity_id=opportunity_id,
            eligibility_status=status,
            hard_gate_results=gates,
            raw_score=round(sum(c.points for c in components), 1),
            score_components=components,
            portfolio_rank=None,
            match_reasons=reasons,
            caveats=[CAVEATS.get(c, c) for c in dict.fromkeys(caveats)],
            verification=verification,
            display_fields=display,
            source_project_facts={
                "format": facts.format,
                "budget_usd": facts.budget_usd,
                "territories": list(facts.ranked_territories),
            },
            co_production_context=facts.treaty_context,
        )

    def match(self, records: list[dict], facts: ProjectFacts) -> list[MatchResult]:
        """Every record evaluated. No filtering, no limit, no package awareness."""
        self.admin_flags = []
        return [self.evaluate(r, facts) for r in records if isinstance(r, dict) and r]

    def build_payload(self, records: list[dict], facts: ProjectFacts,
                      package: str | None = None) -> GrantsReportPayload:
        """The one object the report consumes.

        The order here is the contract's order and it matters: rank the whole eligible
        set first, then slice. Ranking to exhaustion is what makes the Single 5 a
        strict prefix of the Producer 10, which is what acceptance_tests.md means by
        "the same eligible-match universe and scores; only visible result count
        differs".
        """
        results = self.match(records, facts)
        presentable = [r for r in results if r.is_presentable]
        ineligible = [r for r in results if not r.is_presentable]

        ranked = select_portfolio(presentable, project_stage=facts.production_stage)
        limit = display_limit(package or facts.package)

        return GrantsReportPayload(
            database_version=GRANTS_DATABASE_VERSION,
            eligible_match_count=len(ranked),
            display_limit=limit,
            recommendations=ranked[:limit],
            all_qualified_match_ids=[r.opportunity_id for r in ranked],
            narrative_context=NarrativeContext(
                summary_statement=(
                    f"{min(limit, len(ranked))} shown from {len(ranked)} eligible "
                    f"funding opportunities."
                ),
            ),
            package=package or facts.package,
            evaluated_at=self.today.isoformat(),
            ineligible_count=len(ineligible),
            routed_out_count=sum(
                1 for r in ineligible
                if any(g.reason_code == "GATE_ROUTING_NOT_GRANTS"
                       for g in r.hard_gate_results)
            ),
            admin_flags=list(self.admin_flags),
        )
