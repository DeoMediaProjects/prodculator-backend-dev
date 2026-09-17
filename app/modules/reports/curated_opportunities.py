"""Validate source-reviewed cycle records before they enter the v2 engine.

This is a staging contract, not a paid report data feed. Only explicitly curated
records pass, and incomplete source rules remain potentially eligible at most.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

from app.modules.reports.opportunity_strategy import HardGate, Opportunity
from app.modules.reports.project_dna import PROJECT_FACT_FIELDS

_OPERATORS = {
    "equals",
    "one_of",
    "at_least",
    "at_most",
    "greater_than",
    "less_than",
    "overlaps",
    "manual_confirmation",
}
_KINDS = {"FESTIVAL", "MARKET_LAB_WIP"}


def _official_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and bool(parsed.path)


def parse_curated_cycles(payload: dict, *, today: date) -> list[Opportunity]:
    if payload.get("review_status") != "INITIAL_SOURCE_CHECK_NOT_PAID_CUTOVER":
        raise ValueError("Curated source payload lacks its non-cutover review state")
    rows = payload.get("cycles")
    if not isinstance(rows, list):
        raise ValueError("Curated cycles must be a list")
    results = []
    seen = set()
    for row in rows:
        if row.get("kind") not in _KINDS or not row.get("record_id") or not row.get("section_name"):
            raise ValueError("Curated cycle has invalid routing or identity")
        if row.get("cycle_verified") is not True or row.get("rules_complete") is not False:
            raise ValueError("Initial curated cycle must be verified but rules-incomplete")
        if not _official_url(row.get("source_url") or ""):
            raise ValueError("Curated cycle needs an official HTTPS source page")
        opened = date.fromisoformat(row["cycle_open"])
        deadline = date.fromisoformat(row["cycle_deadline"])
        verified = date.fromisoformat(row["verified_on"])
        if opened > deadline or verified > today:
            raise ValueError("Curated cycle has inconsistent dates")
        identity = (row["kind"], row["record_id"], row["section_name"], deadline)
        if identity in seen:
            raise ValueError(f"Duplicate curated cycle: {identity}")
        seen.add(identity)
        gates = []
        for rule in row.get("gates") or []:
            operator = rule.get("operator")
            if rule.get("project_field") not in PROJECT_FACT_FIELDS or operator not in _OPERATORS:
                raise ValueError("Curated rule has unknown Project DNA field or operator")
            if not _official_url(rule.get("source_url") or "") or not rule.get("condition"):
                raise ValueError("Curated rule requires a source URL and condition")
            if operator != "manual_confirmation" and rule.get("expected") is None:
                raise ValueError("Machine-testable curated rule lacks an expected value")
            gates.append(
                HardGate(
                    field=rule["project_field"],
                    operator=operator,
                    expected=rule.get("expected"),
                    source_url=rule["source_url"],
                    condition=rule["condition"],
                )
            )
        if not gates:
            raise ValueError("Curated cycle has no source-linked rules")
        cycle_id = str(uuid5(NAMESPACE_URL, ":".join(str(value) for value in identity)))
        results.append(
            Opportunity(
                id=cycle_id,
                name=row["name"],
                kind=row["kind"],
                source_url=row["source_url"],
                verified_on=verified,
                cycle_open=opened,
                cycle_deadline=deadline,
                cycle_verified=True,
                rules_complete=False,
                gates=tuple(gates),
            )
        )
    return results
