"""Milestone CRUD + seeding service."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient
from app.modules.milestones.schemas import (
    MilestoneCreate,
    MilestoneUpdate,
    MilestoneResponse,
    TaskCreate,
    TaskUpdate,
    TaskResponse,
)

logger = logging.getLogger(__name__)


def _ts(dt: Any) -> str:
    """Convert a datetime or string to ISO string."""
    if dt is None:
        return ""
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def _task_from_row(row: dict) -> TaskResponse:
    return TaskResponse(
        id=row["id"],
        milestone_id=row["milestone_id"],
        text=row.get("text", ""),
        completed=row.get("completed", False),
        territory=row.get("territory"),
        deadline=_ts(row.get("deadline")) if row.get("deadline") else None,
        sort_order=row.get("sort_order", 0),
        completed_at=_ts(row.get("completed_at")) if row.get("completed_at") else None,
        created_at=_ts(row.get("created_at")),
    )


def _milestone_from_row(row: dict, tasks: list[dict] | None = None) -> MilestoneResponse:
    task_responses = [_task_from_row(t) for t in (tasks or [])]
    return MilestoneResponse(
        id=row["id"],
        user_id=row["user_id"],
        report_id=row.get("report_id"),
        title=row.get("title", ""),
        description=row.get("description"),
        status=row.get("status", "upcoming"),
        due_date=_ts(row.get("due_date")) if row.get("due_date") else None,
        sort_order=row.get("sort_order", 0),
        is_template=row.get("is_template", False),
        is_custom=row.get("is_custom", False),
        completed_at=_ts(row.get("completed_at")) if row.get("completed_at") else None,
        created_at=_ts(row.get("created_at")),
        updated_at=_ts(row.get("updated_at")),
        tasks=task_responses,
    )


class MilestoneService:
    """CRUD operations for production milestones and tasks."""

    def __init__(self, supabase: DatabaseClient) -> None:
        self.supabase = supabase

    # ── Milestones ────────────────────────────────────────────────────────

    def list_milestones(
        self, user_id: str, report_id: str | None = None,
    ) -> list[MilestoneResponse]:
        query = (
            self.supabase.table("production_milestones")
            .select("*")
            .eq("user_id", user_id)
            .order("sort_order", desc=False)
        )
        if report_id:
            query = query.eq("report_id", report_id)

        result = query.execute()
        milestones = result.data or []

        if not milestones:
            return []

        # Fetch all tasks for these milestones in one query
        milestone_ids = [m["id"] for m in milestones]
        tasks_result = (
            self.supabase.table("milestone_tasks")
            .select("*")
            .in_("milestone_id", milestone_ids)
            .order("sort_order", desc=False)
            .execute()
        )
        all_tasks = tasks_result.data or []

        # Group tasks by milestone_id
        tasks_by_milestone: dict[str, list[dict]] = {}
        for t in all_tasks:
            mid = t.get("milestone_id", "")
            tasks_by_milestone.setdefault(mid, []).append(t)

        return [
            _milestone_from_row(m, tasks_by_milestone.get(m["id"], []))
            for m in milestones
        ]

    def create_milestone(
        self, user_id: str, data: MilestoneCreate,
    ) -> MilestoneResponse:
        now = datetime.now(timezone.utc).isoformat()
        # Get next sort_order
        existing = (
            self.supabase.table("production_milestones")
            .select("sort_order")
            .eq("user_id", user_id)
            .order("sort_order", desc=True)
            .limit(1)
            .execute()
        )
        next_order = ((existing.data or [{}])[0].get("sort_order", -1) + 1) if existing.data else 0

        row = {
            "id": str(uuid4()),
            "user_id": user_id,
            "report_id": data.report_id,
            "title": data.title,
            "description": data.description,
            "status": "upcoming",
            "due_date": data.due_date,
            "sort_order": next_order,
            "is_template": False,
            "is_custom": True,
            "created_at": now,
            "updated_at": now,
        }
        self.supabase.table("production_milestones").insert(row).execute()
        return _milestone_from_row(row, [])

    def update_milestone(
        self, user_id: str, milestone_id: str, data: MilestoneUpdate,
    ) -> MilestoneResponse | None:
        updates: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if data.title is not None:
            updates["title"] = data.title
        if data.description is not None:
            updates["description"] = data.description
        if data.status is not None:
            updates["status"] = data.status
            if data.status == "completed":
                updates["completed_at"] = datetime.now(timezone.utc).isoformat()
        if data.due_date is not None:
            updates["due_date"] = data.due_date
        if data.sort_order is not None:
            updates["sort_order"] = data.sort_order

        result = (
            self.supabase.table("production_milestones")
            .update(updates)
            .eq("id", milestone_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not result.data:
            return None

        # Fetch tasks
        tasks_result = (
            self.supabase.table("milestone_tasks")
            .select("*")
            .eq("milestone_id", milestone_id)
            .order("sort_order", desc=False)
            .execute()
        )
        return _milestone_from_row(result.data[0], tasks_result.data or [])

    def delete_milestone(self, user_id: str, milestone_id: str) -> bool:
        # Tasks cascade-delete via FK
        result = (
            self.supabase.table("production_milestones")
            .delete()
            .eq("id", milestone_id)
            .eq("user_id", user_id)
            .eq("is_custom", True)
            .execute()
        )
        return bool(result.data)

    # ── Tasks ─────────────────────────────────────────────────────────────

    def create_task(
        self, user_id: str, milestone_id: str, data: TaskCreate,
    ) -> TaskResponse | None:
        # Verify milestone belongs to user
        ms = (
            self.supabase.table("production_milestones")
            .select("id")
            .eq("id", milestone_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not ms.data:
            return None

        # Next sort_order
        existing = (
            self.supabase.table("milestone_tasks")
            .select("sort_order")
            .eq("milestone_id", milestone_id)
            .order("sort_order", desc=True)
            .limit(1)
            .execute()
        )
        next_order = ((existing.data or [{}])[0].get("sort_order", -1) + 1) if existing.data else 0

        row = {
            "id": str(uuid4()),
            "milestone_id": milestone_id,
            "text": data.text,
            "completed": False,
            "territory": data.territory,
            "deadline": data.deadline,
            "sort_order": next_order,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.supabase.table("milestone_tasks").insert(row).execute()
        return _task_from_row(row)

    def update_task(
        self, user_id: str, milestone_id: str, task_id: str, data: TaskUpdate,
    ) -> TaskResponse | None:
        # Verify milestone belongs to user
        ms = (
            self.supabase.table("production_milestones")
            .select("id")
            .eq("id", milestone_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not ms.data:
            return None

        updates: dict[str, Any] = {}
        if data.text is not None:
            updates["text"] = data.text
        if data.completed is not None:
            updates["completed"] = data.completed
            if data.completed:
                updates["completed_at"] = datetime.now(timezone.utc).isoformat()
            else:
                updates["completed_at"] = None
        if data.territory is not None:
            updates["territory"] = data.territory
        if data.deadline is not None:
            updates["deadline"] = data.deadline

        if not updates:
            return None

        result = (
            self.supabase.table("milestone_tasks")
            .update(updates)
            .eq("id", task_id)
            .eq("milestone_id", milestone_id)
            .execute()
        )
        if not result.data:
            return None
        return _task_from_row(result.data[0])

    def delete_task(
        self, user_id: str, milestone_id: str, task_id: str,
    ) -> bool:
        # Verify milestone belongs to user
        ms = (
            self.supabase.table("production_milestones")
            .select("id")
            .eq("id", milestone_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not ms.data:
            return False

        result = (
            self.supabase.table("milestone_tasks")
            .delete()
            .eq("id", task_id)
            .eq("milestone_id", milestone_id)
            .execute()
        )
        return bool(result.data)

    # ── Seeding from report data ──────────────────────────────────────────

    def seed_from_report(self, user_id: str, report_id: str) -> list[MilestoneResponse]:
        """Generate template milestones from a report's analysis data."""
        # Fetch report
        report_result = (
            self.supabase.table("reports")
            .select("id, script_title, report_data")
            .eq("id", report_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        if not report_result.data:
            return []

        report = report_result.data
        report_data: dict = report.get("report_data") or {}
        script_title = report.get("script_title", "Untitled")

        # Delete existing template milestones for this report
        (
            self.supabase.table("production_milestones")
            .delete()
            .eq("user_id", user_id)
            .eq("report_id", report_id)
            .eq("is_template", True)
            .execute()
        )

        today = date.today()
        now = datetime.now(timezone.utc).isoformat()
        milestones_to_create: list[dict] = []
        tasks_to_create: list[dict] = []

        # ── Milestone 1: Script Analysis Complete ─────────────────────────
        m1_id = str(uuid4())
        milestones_to_create.append({
            "id": m1_id,
            "user_id": user_id,
            "report_id": report_id,
            "title": "Script Analysis Complete",
            "description": f'Your production intelligence report for "{script_title}" is ready',
            "status": "completed",
            "sort_order": 0,
            "is_template": True,
            "is_custom": False,
            "completed_at": now,
            "created_at": now,
            "updated_at": now,
        })
        for i, text in enumerate(["Upload script", "Review territory recommendations", "Download PDF report"]):
            tasks_to_create.append({
                "id": str(uuid4()),
                "milestone_id": m1_id,
                "text": text,
                "completed": True,
                "sort_order": i,
                "completed_at": now,
                "created_at": now,
            })

        # ── Milestone 2: Contact Film Commissions ─────────────────────────
        location_rankings = report_data.get("locationRankings", [])
        top_territories = []
        for lr in location_rankings[:3]:
            name = lr.get("name") or lr.get("country") or ""
            if name:
                top_territories.append(name)

        m2_id = str(uuid4())
        due_2 = (today + timedelta(weeks=2)).isoformat()
        milestones_to_create.append({
            "id": m2_id,
            "user_id": user_id,
            "report_id": report_id,
            "title": "Contact Film Commissions",
            "description": "Reach out to film commissions in your top territories",
            "status": "in-progress",
            "due_date": due_2,
            "sort_order": 1,
            "is_template": True,
            "is_custom": False,
            "created_at": now,
            "updated_at": now,
        })
        for i, territory in enumerate(top_territories):
            deadline = (today + timedelta(weeks=2, days=i * 3)).isoformat()
            tasks_to_create.append({
                "id": str(uuid4()),
                "milestone_id": m2_id,
                "text": f"Contact {territory} film commission",
                "completed": False,
                "territory": territory,
                "deadline": deadline,
                "sort_order": i,
                "created_at": now,
            })
        if not top_territories:
            tasks_to_create.append({
                "id": str(uuid4()),
                "milestone_id": m2_id,
                "text": "Research film commissions in target territories",
                "completed": False,
                "sort_order": 0,
                "created_at": now,
            })

        # ── Milestone 3: Tax Advisor Consultation ─────────────────────────
        m3_id = str(uuid4())
        due_3 = (today + timedelta(weeks=3)).isoformat()
        milestones_to_create.append({
            "id": m3_id,
            "user_id": user_id,
            "report_id": report_id,
            "title": "Tax Advisor Consultation",
            "description": "Book consultation with entertainment tax specialists",
            "status": "upcoming",
            "due_date": due_3,
            "sort_order": 2,
            "is_template": True,
            "is_custom": False,
            "created_at": now,
            "updated_at": now,
        })
        for i, text in enumerate([
            "Research qualified tax advisors",
            "Schedule initial consultation",
            "Prepare budget breakdown",
        ]):
            tasks_to_create.append({
                "id": str(uuid4()),
                "milestone_id": m3_id,
                "text": text,
                "completed": False,
                "sort_order": i,
                "created_at": now,
            })

        # ── Milestone 4: Submit Incentive Applications ────────────────────
        incentive_estimates = report_data.get("incentiveEstimates", [])
        m4_id = str(uuid4())
        due_4 = (today + timedelta(weeks=6)).isoformat()
        milestones_to_create.append({
            "id": m4_id,
            "user_id": user_id,
            "report_id": report_id,
            "title": "Submit Incentive Applications",
            "description": "Apply for tax credits and rebates",
            "status": "upcoming",
            "due_date": due_4,
            "sort_order": 3,
            "is_template": True,
            "is_custom": False,
            "created_at": now,
            "updated_at": now,
        })
        if incentive_estimates:
            for i, ie in enumerate(incentive_estimates[:4]):
                territory = ie.get("territory", "")
                program = ie.get("program", "")
                text = f"{territory} — {program} application" if territory and program else f"Incentive application {i + 1}"
                tasks_to_create.append({
                    "id": str(uuid4()),
                    "milestone_id": m4_id,
                    "text": text,
                    "completed": False,
                    "territory": territory or None,
                    "deadline": due_4,
                    "sort_order": i,
                    "created_at": now,
                })
        else:
            tasks_to_create.append({
                "id": str(uuid4()),
                "milestone_id": m4_id,
                "text": "Prepare supporting documents for incentive applications",
                "completed": False,
                "sort_order": 0,
                "created_at": now,
            })

        # ── Milestone 5: Location Scouting ────────────────────────────────
        m5_id = str(uuid4())
        due_5 = (today + timedelta(weeks=8)).isoformat()
        milestones_to_create.append({
            "id": m5_id,
            "user_id": user_id,
            "report_id": report_id,
            "title": "Location Scouting",
            "description": "Visit and assess filming locations",
            "status": "upcoming",
            "due_date": due_5,
            "sort_order": 4,
            "is_template": True,
            "is_custom": False,
            "created_at": now,
            "updated_at": now,
        })
        for i, text in enumerate([
            "Hire location scout",
            "Schedule territory visits",
            "Create location package",
        ]):
            tasks_to_create.append({
                "id": str(uuid4()),
                "milestone_id": m5_id,
                "text": text,
                "completed": False,
                "sort_order": i,
                "created_at": now,
            })

        # ── Milestone 6 (optional): Urgent Deadlines ─────────────────────
        exec_summary = report_data.get("executiveSummary", {})
        action_timeline = exec_summary.get("actionTimeline", []) if isinstance(exec_summary, dict) else []
        urgent_items = [a for a in action_timeline if isinstance(a, dict) and "URGENT" in str(a.get("action", ""))]
        if urgent_items:
            m6_id = str(uuid4())
            milestones_to_create.append({
                "id": m6_id,
                "user_id": user_id,
                "report_id": report_id,
                "title": "Urgent Deadlines",
                "description": "Time-sensitive opportunities from your report",
                "status": "in-progress",
                "sort_order": -1,  # Top of list
                "is_template": True,
                "is_custom": False,
                "created_at": now,
                "updated_at": now,
            })
            for i, item in enumerate(urgent_items[:5]):
                tasks_to_create.append({
                    "id": str(uuid4()),
                    "milestone_id": m6_id,
                    "text": item.get("action", "Urgent deadline"),
                    "completed": False,
                    "deadline": item.get("deadline"),
                    "sort_order": i,
                    "created_at": now,
                })

        # Batch insert — normalise keys so every dict has the same columns
        if milestones_to_create:
            all_keys = {k for row in milestones_to_create for k in row}
            for row in milestones_to_create:
                for key in all_keys:
                    row.setdefault(key, None)
            self.supabase.table("production_milestones").insert(milestones_to_create).execute()
        if tasks_to_create:
            all_keys = {k for row in tasks_to_create for k in row}
            for row in tasks_to_create:
                for key in all_keys:
                    row.setdefault(key, None)
            self.supabase.table("milestone_tasks").insert(tasks_to_create).execute()

        # Return the created milestones
        return self.list_milestones(user_id, report_id)
