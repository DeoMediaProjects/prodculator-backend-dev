"""Promoting a programme's formula from blocked to approved.

Every programme starts at ``blocked``, which is the safe end of the gate: a
source-verified rate says the statute reads that way, not that our formula
applying it is right. Promotion is therefore a deliberate human act, and this
module is what makes it one rather than an UPDATE somebody runs.

WHAT IS ENFORCED HERE
---------------------
- A promotion names a reviewer and says what was checked. A status change with no
  attribution is a flag, not a verification.
- A programme with no statutory engine cannot be approved. There is no formula to
  approve, so approving it would assert something that does not exist.
- A programme whose engine requires statutory inputs must declare them first,
  otherwise "approved" means the engine will silently fall back rather than ask.
- Demotion back to blocked is always allowed and needs no engine. Withdrawing an
  approval must never be harder than granting it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.database_client import DatabaseClient
from app.modules.incentives.v2_contracts import (
    CALCULATION_VERIFICATION,
    ENGINE_REQUIRED_INPUTS,
)

logger = logging.getLogger(__name__)


class ApprovalRefused(ValueError):
    """The promotion cannot be made, with the reason a reviewer needs."""


#: The gate values a reviewer may set, from the contract.
ALLOWED_STATUSES = tuple(CALCULATION_VERIFICATION)

#: Engines that never produce a deterministic figure. They may be approved: the
#: approval is of the refusal and its wording, not of an amount.
_MECHANISM_ENGINES = frozenset(
    {"INVESTOR_TAX_SHELTER", "COMPETITIVE_GRANT", "NO_PROGRAMME"}
)


class CalculationApprovalService:
    def __init__(self, supabase: DatabaseClient) -> None:
        self.supabase = supabase

    # ── reading the queue ────────────────────────────────────────────────────

    def queue(self, status: str = "blocked") -> list[dict[str, Any]]:
        """Programmes at a given gate value, with what each still needs.

        Ordered so the ones that can actually be approved today come first. A
        queue that mixes "ready for review" with "cannot be reviewed yet" gets
        worked from the top and stalls.
        """
        rows = (
            self.supabase.table("incentive_programs")
            .select(
                "id, programme_id, program, territory, status, qs_engine_type,"
                " calculation_verification_status, calculation_verified_by,"
                " calculation_verified_at, calculation_verification_note,"
                " source_verification_status, source_url, last_verified_at"
            )
            .eq("calculation_verification_status", status)
            .execute()
        ).data or []

        declared = self._declared_inputs()
        entries = []
        for row in rows:
            blockers = self._blockers(row, declared.get(row.get("programme_id"), []))
            entries.append({
                **row,
                "approvalBlockers": blockers,
                "canApprove": not blockers,
            })
        entries.sort(key=lambda e: (not e["canApprove"], e.get("territory") or ""))
        return entries

    def _declared_inputs(self) -> dict[str, list[str]]:
        rows = (
            self.supabase.table("programme_required_inputs")
            .select("programme_id, input_key")
            .execute()
        ).data or []
        declared: dict[str, list[str]] = {}
        for row in rows:
            if row.get("programme_id") and row.get("input_key"):
                declared.setdefault(row["programme_id"], []).append(row["input_key"])
        return declared

    @staticmethod
    def _blockers(row: dict[str, Any], declared: list[str]) -> list[str]:
        """What stands between this programme and an approval it would deserve."""
        blockers: list[str] = []
        engine = str(row.get("qs_engine_type") or "").strip().upper()
        if not engine:
            blockers.append(
                "No statutory engine recorded. There is no formula to approve."
            )
            return blockers
        if engine in _MECHANISM_ENGINES:
            # Nothing further to check. The approval covers the refusal wording
            # rather than an amount, so declared inputs are irrelevant.
            return blockers
        required = ENGINE_REQUIRED_INPUTS.get(engine, ())
        if required and not declared:
            blockers.append(
                f"{engine} calculates from "
                f"{', '.join(k.replace('_', ' ') for k in required)}, and this "
                f"programme declares no required inputs. Approving it would let "
                f"the engine run without asking for them."
            )
        if not row.get("source_url"):
            blockers.append(
                "No official source recorded, so the rates cannot be checked "
                "against the statute."
            )
        return blockers

    # ── changing the gate ────────────────────────────────────────────────────

    def set_status(
        self,
        programme_id: str,
        *,
        status: str,
        reviewer: str,
        note: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Set a programme's calculation gate, recording who and why.

        ``force`` overrides the blockers. It exists because a reviewer with the
        statute open may legitimately know better than the checks, and refusing
        them entirely would push the change into raw SQL where nothing is
        recorded at all. It is recorded in the note.
        """
        if status not in ALLOWED_STATUSES:
            raise ApprovalRefused(
                f"status must be one of {', '.join(ALLOWED_STATUSES)}, got {status!r}"
            )
        reviewer = (reviewer or "").strip()
        note = (note or "").strip()
        if not reviewer:
            raise ApprovalRefused("A promotion must name the reviewer making it.")
        if not note:
            raise ApprovalRefused(
                "A promotion must say what was checked. An unexplained approval "
                "cannot be reviewed later."
            )

        rows = (
            self.supabase.table("incentive_programs")
            .select("*")
            .eq("programme_id", programme_id)
            .execute()
        ).data or []
        if not rows:
            raise ApprovalRefused(f"No programme with programme_id {programme_id!r}")
        row = rows[0]

        # Demotion is always permitted. Withdrawing an approval must never be
        # harder than granting one.
        if status != "blocked" and not force:
            blockers = self._blockers(
                row, self._declared_inputs().get(programme_id, []),
            )
            if blockers:
                raise ApprovalRefused(" ".join(blockers))

        recorded_note = note if not force else f"[override] {note}"
        payload = {
            "calculation_verification_status": status,
            "calculation_verified_by": reviewer,
            "calculation_verified_at": datetime.now(timezone.utc).isoformat(),
            "calculation_verification_note": recorded_note,
        }
        (
            self.supabase.table("incentive_programs")
            .update(payload)
            .eq("programme_id", programme_id)
            .execute()
        )
        logger.info(
            "Calculation gate changed: programme=%s %s -> %s by=%s force=%s",
            programme_id,
            row.get("calculation_verification_status"),
            status,
            reviewer,
            force,
        )
        return {
            "programmeId": programme_id,
            "program": row.get("program"),
            "territory": row.get("territory"),
            "previousStatus": row.get("calculation_verification_status"),
            "status": status,
            "reviewer": reviewer,
            "note": recorded_note,
            "verifiedAt": payload["calculation_verified_at"],
        }
