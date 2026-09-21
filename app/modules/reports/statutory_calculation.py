"""The statutory qualifying-spend base, derived only from supplied cost figures.

WHY THIS MODULE EXISTS
----------------------
The Devil Wears Prada regression found a report quoting New York, UK and France
rebate amounts for a project whose territory spend fields were blank. Nothing in
that report was arithmetically wrong. The arithmetic had simply been given the
wrong number: ``ReportValidator._qualifying_spend_for`` derives qualifying spend
from ``budget_gbp``, the total project budget, because that was the only spend
figure the pre-scenario wizard collected.

The v2 implementation note states the rule the rebuild turns on::

    Total Budget != Territory Spend != Qualifying Spend

A total budget is what the production costs everywhere. A territory spend is what
it costs in one jurisdiction. A statutory qualifying spend is the subset of that
territory spend the programme's own rules admit, which is narrower again and is
defined differently by every programme. Multiplying a headline rate by the first
of those three and presenting the result as a rebate is the specific behaviour
the note forbids, and it is how a producer came to read a seven-figure New York
credit for a production with no New York spend recorded.

WHAT THIS MODULE WILL AND WILL NOT DO
-------------------------------------
It computes the qualifying base from ``calculation_inputs`` — the statutory cost
figures the producer supplied for that territory — using the engine the
programme declares in ``qs_engine_type``. It applies the programme's own
percentage and absolute qualifying-spend caps.

It returns ``None`` when any input the engine requires is unknown. That is the
whole contract. There is no fallback to the scenario spend, no fallback to the
budget, and no assumed ratio between them: an unknown statutory base is a fact
about what the producer has told us, and substituting a number we do hold for a
number we do not is exactly how the regression report was produced.

Consequently a blank territory spend yields no figure, and the section says what
is missing rather than showing an amount. ``resolve_calculation_status`` already
reports that state as ``REQUIRES_COST_BREAKDOWN``; this module is the arithmetic
half of the same contract and is deliberately silent in the same cases.

ABOVE-THE-LINE COSTS
--------------------
The legacy path deducts an assumed 15% of budget for above-the-line costs,
because a total budget includes producer, director, writer and lead cast fees
that most credits exclude. A supplied statutory base does not: the producer has
already told us the qualifying figure under the programme's own definition. So a
caller using this module must not apply the ATL assumption on top. Doing so would
discount a number that has already been discounted, by a ratio nobody sourced.

CURRENCY
--------
Every amount in and out is in one currency, and this module does not know which.
Conversion is the caller's, because the caller is the layer holding the FX rate
and its date. Mixing a GBP cap with a USD base here would be undetectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.incentives.v2_contracts import (
    ENGINE_REQUIRED_INPUTS,
    missing_required_inputs,
    resolve_statutory_amount,
)

#: Engines whose figure is not a function of production spend at all. An
#: investor tax shelter's value depends on the investor's position, and a
#: competitive grant's on a funding decision, so no quantity of supplied cost
#: detail produces an amount either one can be relied on for.
NON_SPEND_ENGINES: frozenset[str] = frozenset(
    {"INVESTOR_TAX_SHELTER", "COMPETITIVE_GRANT", "NO_PROGRAMME"}
)

#: The single supplied input each engine's base is, for the engines whose base is
#: exactly one figure. ``CORE_LOWER_OF`` and ``MULTI_BUCKET`` combine several and
#: are handled separately.
_SINGLE_INPUT_ENGINES: dict[str, str] = {
    "ELIGIBLE_LOCAL_SPEND": "eligible_local_spend",
    "TIERED_SPEND": "eligible_local_spend",
    "QUALIFIED_LABOUR": "qualified_labour",
    "QAPE": "qape",
    "QNZPE": "qnzpe",
    "VFX_ONLY": "vfx_expenditure",
    "PDV_ONLY": "pdv_expenditure",
}

#: Engines whose statutory base *is* the production's qualifying spend in the
#: territory, so the expected spend the producer already stated can stand in for
#: it as a planning assumption rather than the report showing nothing.
#:
#: Deliberately not every spend-shaped engine:
#:
#:   ``QUALIFIED_LABOUR``  pays on labour alone. Canada's CPTC counts Canadian
#:                         labour expenditure, a fraction of territory spend and
#:                         not a predictable one.
#:   ``CORE_LOWER_OF``     compares two figures. One number cannot answer it,
#:                         and substituting the same number for both would make
#:                         the comparison vacuous.
#:   ``QAPE`` / ``QNZPE``  are statutory definitions with their own exclusions.
#:   ``MULTI_BUCKET``      is a sum of programme-specific buckets.
#:
#: Seeding any of those from a single spend figure would produce a confident
#: number wrong by a large and predictable margin, which is worse than the
#: report saying it has not been told.
SPEND_SEEDABLE_ENGINES: frozenset[str] = frozenset(
    {"ELIGIBLE_LOCAL_SPEND", "TIERED_SPEND"}
)


def seed_spend_from_scenario(
    engine: str,
    scenario: dict[str, Any] | None,
    supplied: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Stand the producer's stated territory spend in for an absent base.

    Returns the inputs to calculate from, and the keys that were seeded rather
    than supplied. A seeded key is recorded as a ``planning_assumption`` by the
    caller, which is what stops the resulting figure being presented with the
    confidence of a certified cost statement.

    Until the statutory engines were classified, these programmes ran through
    the legacy estimator, which derived a qualifying base from the budget and a
    percentage. That is the substitution this module exists to remove, and this
    is not a return to it: the figure used here is one the producer typed, for
    this territory, in answer to "expected spend here".

    Seeding happens only when every one of these holds:

    * the engine's base is the territory's qualifying spend (see
      ``SPEND_SEEDABLE_ENGINES``);
    * no statutory figure was supplied for that key, so nothing is overwritten;
    * ``scenario_spend_source`` is ``user_entered``. An imported budget line or
      an unknown provenance is not the producer answering the question, and
      ``unknown`` is what the wizard sends for a field left blank.

    No currency conversion happens here, for the same reason ``absolute_cap`` is
    a parameter: the scenario spend and the statutory inputs are both entered in
    the production's budget currency, and a rate this module invented would be
    the least visible way to be wrong.
    """
    normalised = (engine or "").strip().upper()
    if normalised not in SPEND_SEEDABLE_ENGINES or not scenario:
        return supplied, ()
    key = _SINGLE_INPUT_ENGINES.get(normalised)
    if key is None or resolve_statutory_amount(supplied.get(key)) is not None:
        return supplied, ()

    source = (
        scenario.get("scenario_spend_source")
        or scenario.get("scenarioSpendSource")
    )
    if source != "user_entered":
        return supplied, ()

    raw = scenario.get("scenario_spend")
    if raw is None:
        raw = scenario.get("scenarioSpend")
    amount = resolve_statutory_amount(raw)
    if amount is None:
        return supplied, ()
    return {**supplied, key: amount}, (key,)


@dataclass(frozen=True)
class StatutoryQualifyingSpend:
    """One programme's qualifying base, with the inputs it was built from.

    ``inputs_used`` exists so a report can show its working. A producer reading
    "qualifying spend £4,200,000" cannot check it; one reading "the lower of your
    £4,200,000 local core costs and 80% of your £9,000,000 global core costs"
    can, and that is the difference between a figure and a claim.
    """

    amount: float
    engine: str
    inputs_used: dict[str, float]
    notes: list[str] = field(default_factory=list)
    #: Which of the programme's caps bound the result, if either did. Kept apart
    #: from ``notes`` so a caller can test it without parsing prose.
    cap_applied: str | None = None

    @property
    def note(self) -> str | None:
        return " ".join(self.notes) if self.notes else None


def engine_of(row: dict[str, Any]) -> str:
    """The programme's declared calculation engine, or the empty string.

    Matches ``calculation_status._engine_of`` exactly. A row with no engine has
    not been migrated to the v2 contract, and this module declines to guess one
    from its other columns: an engine is a statutory classification, and the
    difference between ``QUALIFIED_LABOUR`` and ``ELIGIBLE_LOCAL_SPEND`` is a
    difference of several million pounds on the same production.
    """
    return str(row.get("qs_engine_type") or "").strip().upper()


def supplied_inputs(scenario: dict[str, Any] | None) -> dict[str, Any]:
    """Statutory amounts supplied for one territory, keyed by canonical input.

    An absent key and an explicit null both mean unknown; zero means the producer
    told us the figure is nil. Nothing here defaults a missing key, which is the
    same rule ``calculation_status._supplied_inputs`` keeps, for the same reason.
    """
    if not scenario:
        return {}
    inputs = scenario.get("calculation_inputs") or scenario.get("calculationInputs")
    if not inputs:
        return {}
    resolved: dict[str, Any] = {}
    for entry in inputs:
        if not isinstance(entry, dict):
            entry = entry.model_dump() if hasattr(entry, "model_dump") else None
        if not entry:
            continue
        key = entry.get("input_key") or entry.get("inputKey")
        if key:
            resolved[key] = entry.get("amount")
    return resolved


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def resolve_statutory_qualifying_spend(
    row: dict[str, Any],
    scenario: dict[str, Any] | None,
    *,
    declared_inputs: tuple[str, ...] | list[str] | None = None,
    absolute_cap: float | None = None,
) -> StatutoryQualifyingSpend | None:
    """The qualifying base for one programme, or ``None`` if it is unknown.

    ``row`` is an ``incentive_programs`` record. ``scenario`` is the producer's
    entry for this territory, whose ``calculation_inputs`` carry the statutory
    figures. ``declared_inputs`` overrides the engine's default requirement,
    which is how a ``MULTI_BUCKET`` programme names its own buckets.

    ``absolute_cap`` is the programme's ``qualifying_spend_cap_amount`` already
    converted into the currency the supplied inputs use. It is a parameter rather
    than a column read because the conversion needs an FX rate this module has no
    business holding; passing ``None`` when the column is set but unconvertible
    means the cap is not applied, so the caller must not do that silently.

    Returns ``None`` — never a zero, and never a figure derived from the budget —
    when a required input is missing or the engine is not spend-derived.
    """
    engine = engine_of(row)
    if not engine or engine in NON_SPEND_ENGINES:
        return None

    supplied = supplied_inputs(scenario)
    supplied, seeded = seed_spend_from_scenario(engine, scenario, supplied)
    if missing_required_inputs(engine, supplied, declared_inputs):
        return None

    notes: list[str] = []
    used: dict[str, float] = {}
    cap_applied: str | None = None

    # ── The base, by engine ──────────────────────────────────────────────────
    cap_pct = _to_float(row.get("qualifying_spend_cap_pct"))

    if engine == "CORE_LOWER_OF":
        # The UK AVEC model: qualifying spend is the lower of local core
        # expenditure and a stated percentage of global core expenditure. The
        # percentage is part of the statutory formula here, not a separate cap
        # applied afterwards, so it is consumed once and not again below. Applying
        # it twice would take 80% of a figure that is already at most 80%.
        local = resolve_statutory_amount(supplied.get("local_core_expenditure"))
        global_core = resolve_statutory_amount(supplied.get("global_core_expenditure"))
        if local is None or global_core is None:
            return None
        used = {
            "local_core_expenditure": local,
            "global_core_expenditure": global_core,
        }
        if cap_pct is not None and 0 < cap_pct < 100:
            ceiling = global_core * (cap_pct / 100.0)
            amount = min(local, ceiling)
            if ceiling < local:
                cap_applied = "percentage_of_global_core"
                notes.append(
                    f"Qualifying spend is the lower of local core expenditure and "
                    f"{cap_pct:g}% of global core expenditure. The global core "
                    f"limb binds here."
                )
            else:
                notes.append(
                    f"Qualifying spend is the lower of local core expenditure and "
                    f"{cap_pct:g}% of global core expenditure. The local limb binds "
                    f"here."
                )
        else:
            # No sourced percentage means no second limb to compare against, so
            # the local figure stands alone. Inventing the usual 80% because most
            # programmes of this shape use it would be a fabricated statutory term.
            amount = local
            notes.append(
                "Qualifying spend is the supplied local core expenditure. This "
                "programme's percentage limit on global core expenditure is not "
                "recorded, so it has not been applied."
            )
        cap_pct = None  # consumed by the formula above

    elif engine == "MULTI_BUCKET":
        # The buckets are programme-specific and arrive through declared_inputs.
        # With none declared there is nothing to add up, and a sum of zero buckets
        # is zero, which would read as "this programme qualifies nothing" rather
        # than "we were never told what it qualifies".
        buckets = tuple(declared_inputs or ENGINE_REQUIRED_INPUTS.get(engine, ()))
        if not buckets:
            return None
        amount = 0.0
        for key in buckets:
            value = resolve_statutory_amount(supplied.get(key))
            if value is None:
                return None
            used[key] = value
            amount += value
        notes.append(
            "Qualifying spend is the sum of this programme's declared cost "
            "buckets: " + ", ".join(key.replace("_", " ") for key in buckets) + "."
        )

    else:
        key = _SINGLE_INPUT_ENGINES.get(engine)
        if key is None:
            # An engine this module has no rule for. Declining is correct: a
            # default of "treat it as total eligible spend" is how an engine
            # distinction stops meaning anything.
            return None
        value = resolve_statutory_amount(supplied.get(key))
        if value is None:
            return None
        used = {key: value}
        amount = value

    # Said before the caps, because it qualifies the base every later line
    # works from. A reader who sees only "qualifying spend $8,000,000" cannot
    # tell a certified cost statement from a figure typed into an intake form.
    if seeded:
        notes.append(
            "No statutory cost breakdown was supplied for this programme, so "
            "the expected spend stated for this territory is used as the "
            "qualifying base. That is a planning assumption rather than "
            "certified eligible spend, and the figure will move once the "
            "actual qualifying costs are known."
        )

    # ── The programme's own caps on that base ────────────────────────────────
    if cap_pct is not None and 0 < cap_pct < 100:
        capped = amount * (cap_pct / 100.0)
        if capped < amount:
            cap_applied = "percentage"
            notes.append(
                f"This programme admits at most {cap_pct:g}% of the supplied base "
                f"as qualifying spend."
            )
        amount = capped

    if absolute_cap is not None and absolute_cap > 0 and amount > absolute_cap:
        amount = absolute_cap
        cap_applied = "absolute"
        notes.append(
            "Qualifying spend is capped at a fixed amount by this programme's "
            "rules — a ceiling that does not rise with the production's spend."
        )

    return StatutoryQualifyingSpend(
        amount=amount,
        engine=engine,
        inputs_used=used,
        notes=notes,
        cap_applied=cap_applied,
    )
