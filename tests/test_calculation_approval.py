"""Promoting a programme's formula out of the blocked state.

A gate whose value can be changed with no record of who changed it or why is not
a verification gate, it is a flag. So the load-bearing behaviours here are that a
promotion names its reviewer and says what was checked, that a programme with no
formula cannot have one approved, and that withdrawing an approval is never
harder than granting it.
"""
from __future__ import annotations

import pytest

from app.core.dependencies import get_current_admin, get_supabase
from app.modules.incentives.calculation_approval import (
    ApprovalRefused,
    CalculationApprovalService,
)


class _Query:
    def __init__(self, table, store):
        self._table = table
        self._store = store
        self._filters: list[tuple[str, object]] = []
        self._update: dict | None = None

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self._update = payload
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def _matching(self):
        rows = self._store.get(self._table, [])
        for field, value in self._filters:
            rows = [r for r in rows if r.get(field) == value]
        return rows

    def execute(self):
        matched = self._matching()
        if self._update is not None:
            for row in matched:
                row.update(self._update)
        return type("Result", (), {"data": [dict(r) for r in matched]})()


class _Supabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _programme(**overrides):
    row = {
        "id": "1",
        "programme_id": "GB_AVEC",
        "program": "UK AVEC",
        "territory": "United Kingdom",
        "status": "active",
        "qs_engine_type": "CORE_LOWER_OF",
        "calculation_verification_status": "blocked",
        "source_url": "https://www.gov.uk/avec",
    }
    row.update(overrides)
    return row


def _store(programmes=None, inputs=None):
    return {
        "incentive_programs": programmes if programmes is not None else [_programme()],
        "programme_required_inputs": inputs if inputs is not None else [
            {"programme_id": "GB_AVEC", "input_key": "local_core_expenditure"},
            {"programme_id": "GB_AVEC", "input_key": "global_core_expenditure"},
        ],
    }


@pytest.fixture()
def service():
    return CalculationApprovalService(_Supabase(_store()))


# ── attribution ──────────────────────────────────────────────────────────────


class TestAttribution:
    def test_a_promotion_records_who_and_what_was_checked(self, service):
        result = service.set_status(
            "GB_AVEC", status="ready",
            reviewer="reviewer@prodculator.com",
            note="Rates and the 80 percent restriction checked against HMRC CIRD.",
        )
        assert result["status"] == "ready"
        assert result["previousStatus"] == "blocked"
        assert result["reviewer"] == "reviewer@prodculator.com"
        assert "HMRC" in result["note"]
        assert result["verifiedAt"]

    def test_an_unnamed_reviewer_is_refused(self, service):
        with pytest.raises(ApprovalRefused, match="name the reviewer"):
            service.set_status("GB_AVEC", status="ready", reviewer="  ", note="ok")

    def test_an_unexplained_approval_is_refused(self, service):
        """It cannot be reviewed later, which is the whole point of the gate."""
        with pytest.raises(ApprovalRefused, match="what was checked"):
            service.set_status("GB_AVEC", status="ready", reviewer="a@b.com", note="")

    def test_an_unknown_status_is_refused(self, service):
        with pytest.raises(ApprovalRefused, match="status must be one of"):
            service.set_status(
                "GB_AVEC", status="approved", reviewer="a@b.com", note="x",
            )

    def test_an_unknown_programme_is_refused(self, service):
        with pytest.raises(ApprovalRefused, match="No programme"):
            service.set_status("XX_NOPE", status="ready", reviewer="a@b.com", note="x")


# ── what may be approved ─────────────────────────────────────────────────────


class TestPreflight:
    def test_a_programme_with_no_engine_cannot_be_approved(self):
        """There is no formula to approve, so approving it would assert something
        that does not exist."""
        svc = CalculationApprovalService(_Supabase(_store(
            programmes=[_programme(qs_engine_type=None)],
        )))
        with pytest.raises(ApprovalRefused, match="no formula to approve"):
            svc.set_status("GB_AVEC", status="ready", reviewer="a@b.com", note="x")

    def test_an_engine_needing_inputs_must_declare_them_first(self):
        """Otherwise "approved" means the engine runs without asking for the cost
        base it calculates from."""
        svc = CalculationApprovalService(_Supabase(_store(inputs=[])))
        with pytest.raises(ApprovalRefused, match="declares no required inputs"):
            svc.set_status("GB_AVEC", status="ready", reviewer="a@b.com", note="x")

    def test_no_official_source_blocks_approval(self):
        svc = CalculationApprovalService(_Supabase(_store(
            programmes=[_programme(source_url=None)],
        )))
        with pytest.raises(ApprovalRefused, match="No official source"):
            svc.set_status("GB_AVEC", status="ready", reviewer="a@b.com", note="x")

    def test_a_mechanism_programme_needs_no_declared_inputs(self):
        """A competitive grant's approval covers the refusal and its wording, not
        an amount, so there is no cost base to declare."""
        svc = CalculationApprovalService(_Supabase(_store(
            programmes=[_programme(
                programme_id="EU_EURIMAGES", qs_engine_type="COMPETITIVE_GRANT",
            )],
            inputs=[],
        )))
        result = svc.set_status(
            "EU_EURIMAGES", status="ready", reviewer="a@b.com",
            note="Refusal wording checked.",
        )
        assert result["status"] == "ready"

    def test_an_override_is_allowed_and_marked_as_one(self):
        """Refusing outright would push the change into raw SQL, where nothing is
        recorded at all."""
        svc = CalculationApprovalService(_Supabase(_store(inputs=[])))
        result = svc.set_status(
            "GB_AVEC", status="ready", reviewer="a@b.com",
            note="Inputs declared in a follow-up migration.", force=True,
        )
        assert result["status"] == "ready"
        assert result["note"].startswith("[override]")


class TestDemotion:
    def test_withdrawing_an_approval_needs_no_preflight(self):
        """Withdrawing must never be harder than granting."""
        svc = CalculationApprovalService(_Supabase(_store(
            programmes=[_programme(
                qs_engine_type=None, calculation_verification_status="ready",
            )],
            inputs=[],
        )))
        result = svc.set_status(
            "GB_AVEC", status="blocked", reviewer="a@b.com",
            note="Rate conflict found in the guidelines.",
        )
        assert result["status"] == "blocked"

    def test_a_demotion_still_records_who_and_why(self):
        svc = CalculationApprovalService(_Supabase(_store(
            programmes=[_programme(calculation_verification_status="ready")],
        )))
        result = svc.set_status(
            "GB_AVEC", status="blocked", reviewer="a@b.com", note="Rate conflict.",
        )
        assert result["reviewer"] == "a@b.com"
        assert result["note"] == "Rate conflict."


# ── the queue ────────────────────────────────────────────────────────────────


class TestQueue:
    def test_the_queue_says_what_each_programme_still_needs(self, service):
        entries = service.queue("blocked")
        assert len(entries) == 1
        assert entries[0]["canApprove"] is True
        assert entries[0]["approvalBlockers"] == []

    def test_reviewable_programmes_sort_first(self):
        """A queue that mixes ready-for-review with cannot-be-reviewed-yet gets
        worked from the top and stalls."""
        svc = CalculationApprovalService(_Supabase(_store(
            programmes=[
                _programme(programme_id="AA_BLOCKED", territory="Andorra",
                           qs_engine_type=None),
                _programme(programme_id="GB_AVEC", territory="United Kingdom"),
            ],
        )))
        entries = svc.queue("blocked")
        assert [e["programme_id"] for e in entries] == ["GB_AVEC", "AA_BLOCKED"]


# ── over the wire ────────────────────────────────────────────────────────────


class TestEndpoint:
    @pytest.fixture()
    def api(self, client):
        store = _store()
        client.app.dependency_overrides[get_supabase] = lambda: _Supabase(store)
        client.app.dependency_overrides[get_current_admin] = lambda: type(
            "Admin", (), {"email": "admin@prodculator.com", "id": "admin-1"},
        )()
        yield client
        client.app.dependency_overrides.clear()

    def test_the_queue_is_served(self, api):
        response = api.get("/api/admin/calculation-approval/queue")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["readyForReview"] == 1

    def test_the_reviewer_comes_from_the_session_not_the_body(self, api):
        """A caller must not be able to attribute an approval to somebody else."""
        response = api.post(
            "/api/admin/calculation-approval/GB_AVEC",
            json={"status": "ready", "note": "Checked against HMRC CIRD.",
                  "reviewer": "someone.else@example.com"},
        )
        assert response.status_code == 200
        assert response.json()["reviewer"] == "admin@prodculator.com"

    def test_a_refused_promotion_returns_the_reason(self, api):
        response = api.post(
            "/api/admin/calculation-approval/GB_AVEC",
            json={"status": "ready", "note": ""},
        )
        assert response.status_code == 422

    def test_an_unknown_queue_status_is_rejected(self, api):
        response = api.get(
            "/api/admin/calculation-approval/queue", params={"status": "whatever"},
        )
        assert response.status_code == 422

    def test_the_endpoint_requires_an_admin(self, client):
        assert client.get(
            "/api/admin/calculation-approval/queue",
        ).status_code in (401, 403)
