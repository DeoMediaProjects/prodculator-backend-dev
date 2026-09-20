"""Loading the staged festival and market universe into engine objects.

WHAT WAS MISSING
----------------
``stage_curated_cycles.py`` writes reviewed cycles into ``opportunity_cycles``
and ``opportunity_rules``, and ``opportunity_strategy`` consumes ``Opportunity``
objects. Nothing turned one into the other, so the staged rows were write-only
and the festival and market strategies had no universe to rank.

This is the read side. It admits a cycle only when the staging row carries the
evidence the engine needs, and it never manufactures the evidence it lacks.

WHERE THE TYPED FACTS COME FROM
-------------------------------
A festival's premiere requirement and a market's programme class both take
source review rather than transcription, and each arrives by its own route.

The premiere requirement is a column on the cycle, written by
``stage_verified_cycles`` from a reviewed ``section_rules`` claim. It has to be
per-cycle: one festival's claim holds several sections' requirements, and a
Competition and a Short Film Corner do not impose the same one, so a value keyed
by festival would apply one section's rule to all of them. The ledger is still
read as a fallback, for a fact recorded directly against a record before the
bridge existed.

The programme class comes from ``market_tracks``, and from the ledger where a
reviewer recorded one against the record.

Either way a strategy acts on the fact only once a named reviewer has signed it
off — and, for festival sections, only once a second reviewer independent of the
author has. Until then the fact is UNKNOWN, the festival sequences as
NEEDS_CONFIRMATION and the market gets no lifecycle judgement, which is what
those states are for.

WHAT IT REFUSES
---------------
A cycle with no deadline, no source or no verification does not become an
``Opportunity`` with blanks in it — it does not become one at all. The engine's
own actionability gate would reject it a moment later, and building the object
first would put a record with no verified cycle one bug away from being ranked.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import sqlalchemy as sa

from app.modules.reports.opportunity_strategy import HardGate, Opportunity
from app.modules.reports.project_dna import PROJECT_FACT_FIELDS
from app.modules.reports.verification_ledger import (
    GATE_FESTIVAL_SECTION,
    GATE_MARKET_RULE,
)

CYCLES_TABLE = "opportunity_cycles"
RULES_TABLE = "opportunity_rules"
RECORDS_TABLE = "engine_handoff_records"
MARKET_TRACKS_TABLE = "market_tracks"

#: Operators the kernel evaluates. A staged rule naming anything else is dropped
#: rather than evaluated as unknown: an operator nobody implemented is a rule
#: nobody has reasoned about, and carrying it would make the gate look tested.
_OPERATORS: frozenset[str] = frozenset(
    {
        "equals",
        "one_of",
        "at_least",
        "at_most",
        "greater_than",
        "less_than",
        "overlaps",
        "manual_confirmation",
    }
)


def _has(conn: sa.Connection, table: str) -> bool:
    return sa.inspect(conn).has_table(table)


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _gates_for(rows: list[Any]) -> tuple[HardGate, ...]:
    gates = []
    for row in rows:
        mapping = row._mapping
        field = str(mapping["project_field"])
        operator = str(mapping["operator"])
        if field not in PROJECT_FACT_FIELDS or operator not in _OPERATORS:
            continue
        if not mapping["source_url"]:
            continue
        gates.append(
            HardGate(
                field=field,
                operator=operator,  # type: ignore[arg-type]
                expected=mapping["expected"],
                source_url=str(mapping["source_url"]),
                condition=str(mapping["condition"]),
            )
        )
    return tuple(gates)


def _verified_facts(engine: sa.Engine, gate: str, *, today: date) -> dict[str, dict]:
    """Ledger facts for one gate, or nothing when the ledger is absent.

    An unavailable ledger yields no facts rather than an error. The strategies
    handle an unknown premiere requirement and an unrecorded programme class
    already, and a report should not fail because the research table has not
    been migrated into this environment yet.
    """
    from app.modules.reports import verification_store

    try:
        return verification_store.readable_values(engine, gate, today=today)
    except verification_store.LedgerUnavailable:
        return {}


def load_opportunities(
    engine: sa.Engine, *, kind: str, today: date
) -> list[Opportunity]:
    """Every staged cycle of one kind that carries the evidence to be ranked."""
    with engine.connect() as conn:
        if not _has(conn, CYCLES_TABLE):
            return []

        cycles = sa.Table(CYCLES_TABLE, sa.MetaData(), autoload_with=conn)
        cycle_rows = list(
            conn.execute(sa.select(cycles).where(cycles.c.kind == kind))
        )
        if not cycle_rows:
            return []

        rules_by_cycle: dict[str, list[Any]] = {}
        if _has(conn, RULES_TABLE):
            rules = sa.Table(RULES_TABLE, sa.MetaData(), autoload_with=conn)
            for row in conn.execute(sa.select(rules)):
                rules_by_cycle.setdefault(str(row._mapping["cycle_id"]), []).append(row)

        names: dict[str, str] = {}
        if _has(conn, RECORDS_TABLE):
            records = sa.Table(RECORDS_TABLE, sa.MetaData(), autoload_with=conn)
            for row in conn.execute(
                sa.select(records.c.record_id, records.c.name).where(
                    records.c.kind == kind
                )
            ):
                names[str(row._mapping["record_id"])] = str(row._mapping["name"])

        classes: dict[str, str] = {}
        if kind == "MARKET_LAB_WIP" and _has(conn, MARKET_TRACKS_TABLE):
            tracks = sa.Table(MARKET_TRACKS_TABLE, sa.MetaData(), autoload_with=conn)
            for row in conn.execute(
                sa.select(tracks.c.id, tracks.c.programme_class)
            ):
                value = row._mapping["programme_class"]
                if value:
                    classes[str(row._mapping["id"])] = str(value).strip().upper()

    ledger_gate = (
        GATE_FESTIVAL_SECTION if kind == "FESTIVAL" else GATE_MARKET_RULE
    )
    verified = _verified_facts(engine, ledger_gate, today=today)

    opportunities: list[Opportunity] = []
    for row in cycle_rows:
        mapping = row._mapping
        cycle_id = str(mapping["id"])
        record_id = str(mapping["record_id"])
        deadline = _as_date(mapping["cycle_deadline"])
        verified_on = _as_date(mapping["verified_on"])
        source_url = str(mapping["source_url"] or "")

        # Refused rather than loaded with blanks. The engine's actionability
        # gate would reject these a moment later, and building the object first
        # puts an unverified cycle one bug away from being ranked.
        if not deadline or not verified_on or not source_url:
            continue

        section = str(mapping["section_name"] or "").strip()
        base_name = names.get(record_id, record_id)
        facts = verified.get(cycle_id) or verified.get(record_id) or {}

        opportunities.append(
            Opportunity(
                id=cycle_id,
                name=f"{base_name} — {section}" if section else base_name,
                kind=kind,  # type: ignore[arg-type]
                source_url=source_url,
                verified_on=verified_on,
                cycle_open=_as_date(mapping["cycle_open"]),
                cycle_deadline=deadline,
                cycle_verified=bool(mapping["cycle_verified"]),
                rules_complete=bool(mapping["rules_complete"]),
                gates=_gates_for(rules_by_cycle.get(cycle_id, [])),
                observed_open_on=_as_date(
                    mapping["observed_open_on"]
                    if "observed_open_on" in mapping
                    else None
                ),
                record_id=record_id,
                # Verified research, or nothing. An unrecorded premiere
                # requirement sequences as NEEDS_CONFIRMATION and an unrecorded
                # class gets no lifecycle judgement, which is what those states
                # exist for.
                #
                # The cycle's own column wins over the ledger: it is the only
                # one of the two that can differ between two sections of the
                # same festival.
                premiere_requirement=(
                    str(mapping["premiere_requirement"]).strip().upper()
                    if "premiere_requirement" in mapping
                    and mapping["premiere_requirement"]
                    else facts.get("premiere_requirement")
                ),
                opportunity_class=facts.get("opportunity_class")
                or classes.get(record_id),
            )
        )
    return opportunities
