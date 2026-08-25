"""Serving scenario question sets to the wizard.

The wizard asks "for these territories, what should I ask the producer?" and gets
back one question set per jurisdiction. It contains no territory logic of its own,
which is the specification's requirement: the frontend is a renderer.

Loading is done once per request rather than per territory. Selecting five
territories would otherwise issue ten queries, and the programme table is small
enough that one read of the active rows is cheaper than five.
"""
from __future__ import annotations

from typing import Any, Iterable

from app.core.database_client import DatabaseClient
from app.modules.incentives.v2_contracts import (
    COMPARISON_TERRITORY_LIMITS,
    MAX_COPRODUCTION_PARTNERS,
    is_comparison_mode,
)
from app.modules.incentives.v2_jurisdictions import (
    UnknownJurisdiction,
    resolve_jurisdiction,
)
from app.modules.incentives.v2_question_resolver import (
    ScenarioQuestionSet,
    resolve_questions,
)


class ScenarioLimitExceeded(ValueError):
    """More jurisdictions were requested than the plan and mode allow."""


class ScenarioQuestionService:
    def __init__(self, supabase: DatabaseClient) -> None:
        self.supabase = supabase

    # ── loading ──────────────────────────────────────────────────────────────

    def _programme_rows(self) -> list[dict[str, Any]]:
        """Active programme rows, with the v2 identity fields the resolver reads.

        A row with no ``programme_id`` has not been migrated to a v2 engine yet
        and contributes no questions, which is why the resolver tolerates it
        rather than treating it as an error.
        """
        result = (
            self.supabase.table("incentive_programs")
            .select("*")
            .eq("status", "active")
            .execute()
        )
        return result.data or []

    def _declared_inputs(self) -> list[dict[str, Any]]:
        result = (
            self.supabase.table("programme_required_inputs").select("*").execute()
        )
        return result.data or []

    # ── the public call ──────────────────────────────────────────────────────

    def question_sets(
        self,
        territories: Iterable[str],
        *,
        mode: str = "comparison",
        plan: str = "free",
    ) -> dict[str, Any]:
        """Question sets for the selected jurisdictions.

        Raises ``UnknownJurisdiction`` for anything that cannot resolve, rather
        than skipping it. A silently dropped territory would leave the wizard
        showing a card with no questions and no reason.
        """
        names = [t for t in territories if t and str(t).strip()]
        if not names:
            return {"mode": mode, "scenarios": [], "limit": self.limit_for(mode, plan)}

        jurisdictions = [resolve_jurisdiction(name) for name in names]

        # Deduplicate by scenario key, so selecting both "United Kingdom" and "GB"
        # yields one card rather than two competing ones.
        unique: dict[str, Any] = {}
        for jurisdiction in jurisdictions:
            unique.setdefault(jurisdiction.scenario_key, jurisdiction)

        limit = self.limit_for(mode, plan)
        if limit is not None and len(unique) > limit:
            raise ScenarioLimitExceeded(self.limit_message(mode, plan, limit))

        programme_rows = self._programme_rows()
        declared = self._declared_inputs()

        sets: list[ScenarioQuestionSet] = [
            resolve_questions(jurisdiction, programme_rows, declared)
            for jurisdiction in unique.values()
        ]
        return {
            "mode": mode,
            "limit": limit,
            "scenarios": [s.as_dict() for s in sets],
        }

    # ── limits ───────────────────────────────────────────────────────────────

    @staticmethod
    def limit_for(mode: str, plan: str) -> int | None:
        """How many jurisdictions this mode and plan allow.

        Two different limits on purpose. The comparison limit bounds how many
        alternatives a tier may explore, which is a product lever. The
        co-production limit bounds partners in one production, and a multilateral
        co-production needs at least three co-producers, so reusing the
        Explorer comparison limit of three would make any four-partner structure
        unmodellable. Blocking a real structure is not the same as bounding
        exploration.
        """
        if is_comparison_mode(mode):
            return COMPARISON_TERRITORY_LIMITS.get(
                (plan or "free").strip().lower(), COMPARISON_TERRITORY_LIMITS["free"]
            )
        return MAX_COPRODUCTION_PARTNERS

    @staticmethod
    def limit_message(mode: str, plan: str, limit: int) -> str:
        if is_comparison_mode(mode):
            return (
                f"Your plan compares up to {limit} territories at once. Remove one "
                f"to add another, or upgrade to compare more."
            )
        return (
            f"A co-production structure can hold up to {limit} partner territories. "
            f"That is a limit on this form rather than on the treaty framework."
        )
