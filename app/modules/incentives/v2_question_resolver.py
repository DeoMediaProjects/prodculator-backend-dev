"""Resolving which statutory questions the wizard asks for a scenario.

The specification is emphatic that the frontend must contain a renderer and no
business logic: no ``if British Columbia, ask labour``. Programme records declare
what they need, and this resolves them for one jurisdiction.

Two rules shape the output.

Deduplicate by canonical input key. Selecting the United Kingdom brings AVEC, IFTC
and the VFX credit into scope, and AVEC and IFTC both need local and global core
expenditure. Asking twice would make the form look broken and would let a producer
enter two different figures for the same statutory quantity.

Never ask a non-entitlement programme for a spend base. An investor shelter and a
competitive grant are not calculated from production spend, so a question implying
they are would be misleading even before an answer is given.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.modules.incentives.v2_contracts import (
    CANONICAL_INPUTS,
    ENGINE_REQUIRED_INPUTS,
)
from app.modules.incentives.v2_jurisdictions import Jurisdiction
from app.modules.reports.helpers import non_entitlement_mechanism


@dataclass(frozen=True)
class ScenarioQuestion:
    """One statutory input the producer may supply for a scenario."""

    input_key: str
    label: str
    help_text: str
    input_type: str = "currency"
    required_for_exact: bool = True
    #: Every programme in this scenario that uses the answer. A producer seeing
    #: one question that serves three programmes should be told so, rather than
    #: assuming it applies to whichever they had in mind.
    used_by: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "inputKey": self.input_key,
            "label": self.label,
            "helpText": self.help_text,
            "inputType": self.input_type,
            "requiredForExact": self.required_for_exact,
            "usedBy": list(self.used_by),
        }


@dataclass
class ScenarioQuestionSet:
    """Everything the wizard needs to render one scenario card."""

    jurisdiction_label: str
    territory_id: str
    subdivision_id: str | None
    questions: list[ScenarioQuestion] = field(default_factory=list)
    #: Programmes in scope, for the card subtitle.
    programmes: list[dict[str, Any]] = field(default_factory=list)
    #: Programmes in scope that cannot produce a figure whatever is entered, with
    #: the reason. Surfaced so an empty accordion reads as a statement rather than
    #: an oversight.
    non_calculating: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "jurisdiction": self.jurisdiction_label,
            "territoryId": self.territory_id,
            "subdivisionId": self.subdivision_id,
            "questions": [q.as_dict() for q in self.questions],
            "programmes": self.programmes,
            "nonCalculating": self.non_calculating,
        }


def programmes_for(
    jurisdiction: Jurisdiction, rows: Iterable[dict],
) -> list[dict]:
    """Programme rows in scope for a scenario.

    A statutory subdivision resolves to its own programmes only. British Columbia
    must not pick up every Canadian record, or a scenario for one province would
    ask for another's statutory bases.

    A country resolves to its national programmes, excluding subdivision level
    ones, which are reached by selecting the subdivision.
    """
    in_scope: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "active").strip().lower()
        if status not in ("active", ""):
            continue
        subdivision = row.get("jurisdiction_subdivision")
        country = row.get("jurisdiction_country")
        if jurisdiction.subdivision_id:
            if subdivision == jurisdiction.subdivision_id:
                in_scope.append(row)
        else:
            if country == jurisdiction.territory_id and not subdivision:
                in_scope.append(row)
    return in_scope


def resolve_questions(
    jurisdiction: Jurisdiction,
    programme_rows: Iterable[dict],
    declared_inputs: Iterable[dict],
) -> ScenarioQuestionSet:
    """Build the question set for one scenario.

    ``declared_inputs`` are ``programme_required_inputs`` rows. A programme with no
    declared inputs contributes none: the engine default is used only to decide
    whether it can calculate, never to invent a question whose wording nobody
    reviewed.
    """
    rows = list(programme_rows)
    in_scope = programmes_for(jurisdiction, rows)

    declared_by_programme: dict[str, list[dict]] = {}
    for row in declared_inputs:
        if not isinstance(row, dict):
            continue
        declared_by_programme.setdefault(row.get("programme_id"), []).append(row)

    questions: dict[str, ScenarioQuestion] = {}
    programmes: list[dict[str, Any]] = []
    non_calculating: list[dict[str, Any]] = []

    for row in in_scope:
        programme_id = row.get("programme_id")
        name = row.get("program") or programme_id or "Unnamed programme"
        engine = str(row.get("qs_engine_type") or "").strip().upper()
        programmes.append({
            "programmeId": programme_id,
            "name": name,
            "engine": engine or None,
            "calculationVerification": row.get("calculation_verification_status"),
        })

        if non_entitlement_mechanism(row):
            from app.modules.reports.helpers import mechanism_no_figure_reason

            non_calculating.append({
                "programmeId": programme_id,
                "name": name,
                "reason": mechanism_no_figure_reason(row),
            })
            continue

        for declared in declared_by_programme.get(programme_id, []):
            key = declared.get("input_key")
            if key not in CANONICAL_INPUTS:
                # A key outside the registry has no engine that reads it, so a
                # question for it could never change a result.
                continue
            existing = questions.get(key)
            if existing is None:
                questions[key] = ScenarioQuestion(
                    input_key=key,
                    label=declared.get("label") or CANONICAL_INPUTS[key],
                    help_text=declared.get("help_text") or "",
                    input_type=declared.get("input_type") or "currency",
                    required_for_exact=bool(declared.get("required_for_exact", True)),
                    used_by=(name,),
                )
            else:
                # Shared between programmes. Keep the first wording, which is
                # stable, and record that the answer serves more than one.
                questions[key] = ScenarioQuestion(
                    input_key=existing.input_key,
                    label=existing.label,
                    help_text=existing.help_text,
                    input_type=existing.input_type,
                    required_for_exact=(
                        existing.required_for_exact
                        or bool(declared.get("required_for_exact", True))
                    ),
                    used_by=existing.used_by + (name,),
                )

    ordered = sorted(
        questions.values(),
        key=lambda q: (not q.required_for_exact, q.input_key),
    )
    return ScenarioQuestionSet(
        jurisdiction_label=jurisdiction.label,
        territory_id=jurisdiction.territory_id,
        subdivision_id=jurisdiction.subdivision_id,
        questions=ordered,
        programmes=programmes,
        non_calculating=non_calculating,
    )


def engine_default_inputs(engine: str) -> tuple[str, ...]:
    """What an engine needs when a programme declares nothing.

    For diagnostics and admin warnings, not for rendering. A programme whose
    engine requires a base it has not declared will never calculate, and that is
    worth surfacing to an administrator rather than papering over with a
    generated question.
    """
    return ENGINE_REQUIRED_INPUTS.get(engine.strip().upper(), ())


def undeclared_requirements(row: dict, declared: Iterable[dict]) -> list[str]:
    """Inputs an engine needs that the programme has not declared."""
    engine = str(row.get("qs_engine_type") or "").strip().upper()
    if not engine or non_entitlement_mechanism(row):
        return []
    declared_keys = {
        d.get("input_key") for d in declared
        if d.get("programme_id") == row.get("programme_id")
    }
    return [k for k in engine_default_inputs(engine) if k not in declared_keys]
