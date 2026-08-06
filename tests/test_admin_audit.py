"""Admin audit trail (handoff §4.4/§4.5).

The property that matters most is coverage-by-construction: an admin mutation is
recorded because it went through an audited router, not because an endpoint
remembered to log. ``test_every_admin_route_is_audited`` is the guard on that —
it fails the moment a router is mounted under /api/admin without the route
class, which is the only way a new admin mutation could escape the trail.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Request
from fastapi.routing import APIRoute
from sqlalchemy import delete, select

from app.core.audit import (
    MAX_JSON_CHARS,
    AuditedAPIRoute,
    client_ip,
    purge_expired_audit_logs,
    record_audit_log,
    redact,
    resolve_action,
    resolve_resource,
)
from app.core.db import get_db_context
from app.core.dependencies import get_current_admin, get_supabase
from app.models.sql_models import AdminAuditLog
from app.modules.admin.schemas import AdminUser

from tests.test_admin_routes import FakeSupabase


# ── Helpers ─────────────────────────────────────────────────────────────────


def _admin_dep(request: Request) -> AdminUser:
    """Stand-in for get_current_admin that also does what the real one does:
    attribute the request for the audit route class."""
    admin = AdminUser(
        id="admin-1", email="admin@example.com", name="Admin", role="master_admin",
    )
    request.state.audit_actor = admin
    return admin


def _data_admin_dep(request: Request) -> AdminUser:
    admin = AdminUser(
        id="admin-2", email="data@example.com", name="Data", role="data_admin",
    )
    request.state.audit_actor = admin
    return admin


def _clear_logs() -> None:
    with get_db_context() as session:
        session.execute(delete(AdminAuditLog))
        session.commit()


def _logs() -> list[AdminAuditLog]:
    with get_db_context() as session:
        return list(
            session.execute(
                select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())
            ).scalars().all()
        )


@pytest.fixture
def audit_client(client):
    _clear_logs()
    client.app.dependency_overrides[get_current_admin] = _admin_dep
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase()
    yield client
    _clear_logs()


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer token"}


# ── Coverage by construction ────────────────────────────────────────────────


def _api_routes(app) -> list[APIRoute]:
    """Every APIRoute the app serves, including those behind include_router
    wrappers in this FastAPI version."""
    routes: list[APIRoute] = []
    for entry in app.routes:
        if isinstance(entry, APIRoute):
            routes.append(entry)
        original = getattr(entry, "original_router", None)
        if original is not None:
            routes.extend(r for r in original.routes if isinstance(r, APIRoute))
        nested = getattr(entry, "routes", None)
        if nested:
            routes.extend(r for r in nested if isinstance(r, APIRoute))
    return routes


def test_every_admin_route_is_audited(client):
    """The guard against a forgotten write path.

    If this fails, a router under /api/admin was registered without
    route_class=AuditedAPIRoute and its mutations are invisible.
    """
    admin_routes = [
        r for r in _api_routes(client.app) if r.path.startswith("/api/admin")
    ]
    assert admin_routes, "no admin routes discovered — the walk is wrong, not the app"
    unaudited = [
        (r.path, sorted(r.methods))
        for r in admin_routes
        if not isinstance(r, AuditedAPIRoute)
    ]
    assert unaudited == [], (
        "these admin routes are not audited — add route_class=AuditedAPIRoute "
        f"to their router: {unaudited}"
    )


def test_admin_mutations_all_have_an_audited_route(client):
    """Specifically the mutating verbs, stated separately so the failure message
    points at the risk rather than at route plumbing."""
    mutating = [
        r for r in _api_routes(client.app)
        if r.path.startswith("/api/admin")
        and {"POST", "PUT", "PATCH", "DELETE"} & set(r.methods)
    ]
    assert len(mutating) > 20, "expected many admin mutations; the walk may be wrong"
    assert all(isinstance(r, AuditedAPIRoute) for r in mutating)


# ── The write path ──────────────────────────────────────────────────────────


def test_create_is_recorded_with_actor_and_after_state(audit_client):
    response = audit_client.post(
        "/api/admin/comparables",
        json={"payload": {"title": "Audited Film", "year": 2026}},
        headers=_headers(),
    )
    assert response.status_code == 200

    rows = _logs()
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "create.comparable"
    assert row.resource_type == "comparable"
    assert row.actor_id == "admin-1"
    assert row.actor_email == "admin@example.com"
    assert row.actor_role == "master_admin"
    assert row.method == "POST"
    assert row.path == "/api/admin/comparables"
    assert row.status_code == 200
    assert row.error_message is None
    # after_json is the persisted row the endpoint returned, not the payload.
    assert row.after_json["title"] == "Audited Film"


def test_update_records_the_resource_id(audit_client):
    response = audit_client.patch(
        "/api/admin/comparables/cp1",
        json={"payload": {"title": "Renamed"}},
        headers=_headers(),
    )
    assert response.status_code == 200

    row = _logs()[0]
    assert row.action == "update.comparable"
    assert row.resource_id == "cp1"


def test_delete_is_recorded(audit_client):
    response = audit_client.delete("/api/admin/comparables/cp1", headers=_headers())
    assert response.status_code == 200

    row = _logs()[0]
    assert row.action == "delete.comparable"
    assert row.resource_id == "cp1"


def test_reads_are_not_recorded(audit_client):
    assert audit_client.get("/api/admin/comparables", headers=_headers()).status_code == 200
    assert audit_client.get("/api/admin/metrics", headers=_headers()).status_code == 200
    assert _logs() == []


def test_failed_mutation_is_still_recorded(audit_client):
    """A mutation that errored must not vanish: 'nobody touched it' and 'someone
    tried and it broke' have to stay distinguishable."""
    response = audit_client.post(
        "/api/admin/reports/does-not-exist/reissue-pdf", headers=_headers(),
    )
    assert response.status_code == 404

    row = _logs()[0]
    assert row.status_code == 404
    assert row.resource_id == "does-not-exist"
    assert row.error_message


def test_permission_denial_is_recorded_against_the_admin(audit_client):
    """A real admin reaching for something their role does not hold is exactly
    what the trail exists to capture."""
    audit_client.app.dependency_overrides[get_current_admin] = _data_admin_dep

    response = audit_client.post(
        "/api/admin/b2b/subscriptions",
        json={"user_id": "u1", "product_type": "territory"},
        headers=_headers(),
    )
    assert response.status_code == 403

    row = _logs()[0]
    assert row.status_code == 403
    assert row.actor_id == "admin-2"
    assert row.actor_role == "data_admin"


def test_unauthenticated_attempt_is_not_recorded(client):
    """401s carry no identity to attribute and would let an anonymous caller
    fill the audit table, so they are deliberately skipped."""
    _clear_logs()
    client.app.dependency_overrides.clear()
    response = client.post(
        "/api/admin/comparables", json={"payload": {"title": "x"}},
    )
    assert response.status_code in (401, 403)
    assert _logs() == []
    _clear_logs()


def test_secrets_never_reach_the_table(audit_client):
    audit_client.post(
        "/api/admin/comparables",
        json={"payload": {"title": "Film", "api_key": "sk-live-123", "password": "hunter2"}},
        headers=_headers(),
    )
    row = _logs()[0]
    serialised = f"{row.before_json}{row.after_json}"
    assert "sk-live-123" not in serialised
    assert "hunter2" not in serialised


def test_client_ip_is_captured(audit_client):
    audit_client.post(
        "/api/admin/comparables",
        json={"payload": {"title": "Film"}},
        headers={**_headers(), "X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
    )
    assert _logs()[0].ip_address == "203.0.113.9"


def test_audit_failure_does_not_fail_the_admin_action(audit_client, monkeypatch):
    """The action has already happened by the time the row is written. Losing the
    row is an ops problem; failing the request would be a worse one."""
    def _boom(*_args, **_kwargs):
        raise RuntimeError("audit table unreachable")

    monkeypatch.setattr("app.core.audit.record_audit_log", _boom)
    response = audit_client.post(
        "/api/admin/comparables",
        json={"payload": {"title": "Still Works"}},
        headers=_headers(),
    )
    assert response.status_code == 200


def test_record_audit_log_never_raises(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.core.db.get_db_context", _boom)
    assert record_audit_log(action="create.thing", resource_type="thing") is None


# ── Path resolution ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,expected_type",
    [
        ("/api/admin/incentives", "incentive"),
        ("/api/admin/incentives/abc", "incentive"),
        ("/api/admin/festivals/1", "festival"),
        ("/api/admin/grants/1", "grant"),
        ("/api/admin/comparables/1", "comparable"),
        ("/api/admin/territory-profiles/UK", "territory_profile"),
        ("/api/admin/admin-users/1", "admin_user"),
        ("/api/admin/email-gating/1/block", "email_gating_record"),
        ("/api/admin/b2b/subscriptions/1", "b2b_subscription"),
        ("/api/admin/b2b/entitlements/1", "b2b_entitlement"),
        ("/api/admin/b2b/invites/1", "b2b_invite"),
        # Unmapped paths still resolve to something usable rather than failing.
        ("/api/admin/brand-new-thing/1", "brand_new_thing"),
    ],
)
def test_resource_resolution(path, expected_type):
    resource_type, _table = resolve_resource(path)
    assert resource_type == expected_type


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("POST", "/api/admin/comparables", "create.comparable"),
        ("PATCH", "/api/admin/comparables/1", "update.comparable"),
        ("PUT", "/api/admin/comparables/1", "update.comparable"),
        ("DELETE", "/api/admin/comparables/1", "delete.comparable"),
        # A verb in the path tail must win over the method's default verb, or a
        # hold would be indistinguishable from a creation.
        ("POST", "/api/admin/b2b/subscriptions/1/hold", "hold.b2b_subscription"),
        ("POST", "/api/admin/email-gating/1/block", "block.email_gating_record"),
        ("POST", "/api/admin/comparables/sync-tmdb", "sync-tmdb.comparable"),
    ],
)
def test_action_resolution(method, path, expected):
    resource_type, _ = resolve_resource(path)
    assert resolve_action(method, path, resource_type) == expected


# ── Redaction and size limits ───────────────────────────────────────────────


def test_redact_covers_nested_and_cased_keys():
    payload = {
        "name": "keep",
        "Password": "secret",
        "nested": {"stripe_secret_key": "sk", "items": [{"apiKey": "k"}]},
    }
    result = redact(payload)
    assert result["name"] == "keep"
    assert result["Password"] == "[redacted]"
    assert result["nested"]["stripe_secret_key"] == "[redacted]"
    assert result["nested"]["items"][0]["apiKey"] == "[redacted]"


def test_oversize_payload_is_marked_not_truncated():
    """A truncated blob reads as complete state while being silently partial."""
    huge = {"blob": "x" * (MAX_JSON_CHARS + 100)}
    log_id = record_audit_log(
        action="update.thing", resource_type="thing", after=huge,
    )
    assert log_id
    with get_db_context() as session:
        row = session.get(AdminAuditLog, log_id)
        assert "_audit_note" in row.after_json
        assert "x" * 100 not in str(row.after_json)
        session.delete(row)
        session.commit()


# ── Retention ───────────────────────────────────────────────────────────────


def test_retention_purges_only_expired_rows():
    _clear_logs()
    now = datetime.now(timezone.utc)
    with get_db_context() as session:
        session.add(AdminAuditLog(
            action="a.b", resource_type="b", created_at=now - timedelta(days=800),
        ))
        session.add(AdminAuditLog(
            action="a.b", resource_type="b", created_at=now - timedelta(days=10),
        ))
        session.commit()

    removed = purge_expired_audit_logs(730)
    assert removed == 1
    assert len(_logs()) == 1
    _clear_logs()


def test_zero_retention_means_retain_indefinitely():
    _clear_logs()
    with get_db_context() as session:
        session.add(AdminAuditLog(
            action="a.b", resource_type="b",
            created_at=datetime.now(timezone.utc) - timedelta(days=5000),
        ))
        session.commit()

    assert purge_expired_audit_logs(0) == 0
    assert purge_expired_audit_logs(-1) == 0
    assert len(_logs()) == 1
    _clear_logs()


def test_retention_job_is_registered():
    from app.core import scheduler as scheduler_module

    assert hasattr(scheduler_module, "_run_admin_audit_retention")


# ── The reader ──────────────────────────────────────────────────────────────


def _seed_logs() -> None:
    _clear_logs()
    now = datetime.now(timezone.utc)
    with get_db_context() as session:
        session.add_all([
            AdminAuditLog(
                id="log-1", actor_id="admin-1", actor_email="admin@example.com",
                actor_role="master_admin", action="update.incentive",
                resource_type="incentive", resource_id="i1", status_code=200,
                path="/api/admin/incentives/i1", created_at=now - timedelta(hours=1),
                before_json={"rate_gross": 30}, after_json={"rate_gross": 34},
            ),
            AdminAuditLog(
                id="log-2", actor_id="admin-2", actor_email="data@example.com",
                actor_role="data_admin", action="delete.comparable",
                resource_type="comparable", resource_id="cp1", status_code=403,
                path="/api/admin/comparables/cp1", error_message="Permission required",
                created_at=now - timedelta(hours=2),
            ),
            AdminAuditLog(
                id="log-3", actor_id="admin-1", actor_email="admin@example.com",
                actor_role="master_admin", action="create.festival",
                resource_type="festival", resource_id="f9", status_code=200,
                path="/api/admin/festivals", created_at=now - timedelta(days=400),
            ),
        ])
        session.commit()


@pytest.fixture
def reader_client(client):
    _seed_logs()
    client.app.dependency_overrides[get_current_admin] = _admin_dep
    yield client
    _clear_logs()


def test_reader_lists_newest_first(reader_client):
    response = reader_client.get("/api/admin/audit-logs", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == ["log-1", "log-2", "log-3"]


def test_reader_derives_success_from_status(reader_client):
    body = reader_client.get("/api/admin/audit-logs", headers=_headers()).json()
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id["log-1"]["succeeded"] is True
    assert by_id["log-2"]["succeeded"] is False


@pytest.mark.parametrize(
    "query,expected_ids",
    [
        ("actor_id=admin-1", ["log-1", "log-3"]),
        ("actor_email=data@", ["log-2"]),
        ("action=update.incentive", ["log-1"]),
        ("resource_type=comparable", ["log-2"]),
        ("resource_id=i1", ["log-1"]),
        ("status=failed", ["log-2"]),
        ("status=success", ["log-1", "log-3"]),
        ("search=incentives", ["log-1"]),
        ("search=Permission", ["log-2"]),
    ],
)
def test_reader_filters(reader_client, query, expected_ids):
    body = reader_client.get(
        f"/api/admin/audit-logs?{query}", headers=_headers(),
    ).json()
    assert [item["id"] for item in body["items"]] == expected_ids


def test_reader_rejects_an_unknown_status(reader_client):
    response = reader_client.get(
        "/api/admin/audit-logs?status=maybe", headers=_headers(),
    )
    assert response.status_code == 400


def test_reader_rejects_an_inverted_date_range(reader_client):
    response = reader_client.get(
        "/api/admin/audit-logs?start_date=2026-08-01&end_date=2026-07-01",
        headers=_headers(),
    )
    assert response.status_code == 400


def test_reader_rejects_an_unparseable_date(reader_client):
    response = reader_client.get(
        "/api/admin/audit-logs?start_date=last-tuesday", headers=_headers(),
    )
    assert response.status_code == 400


def test_reader_date_filter_narrows_results(reader_client):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    body = reader_client.get(
        f"/api/admin/audit-logs?start_date={cutoff}", headers=_headers(),
    ).json()
    assert [item["id"] for item in body["items"]] == ["log-1", "log-2"]


def test_reader_paginates(reader_client):
    body = reader_client.get(
        "/api/admin/audit-logs?limit=1&offset=1", headers=_headers(),
    ).json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == ["log-2"]


def test_reader_returns_one_entry_with_before_and_after(reader_client):
    body = reader_client.get("/api/admin/audit-logs/log-1", headers=_headers()).json()
    assert body["before_json"] == {"rate_gross": 30}
    assert body["after_json"] == {"rate_gross": 34}


def test_reader_404s_on_an_unknown_entry(reader_client):
    response = reader_client.get("/api/admin/audit-logs/nope", headers=_headers())
    assert response.status_code == 404


def test_reader_facets_offer_real_filter_values(reader_client):
    body = reader_client.get("/api/admin/audit-logs/facets", headers=_headers()).json()
    assert sorted(body["actions"]) == [
        "create.festival", "delete.comparable", "update.incentive",
    ]
    assert sorted(body["resource_types"]) == ["comparable", "festival", "incentive"]
    actors = {a["actor_id"]: a["count"] for a in body["actors"]}
    assert actors == {"admin-1": 2, "admin-2": 1}


def test_reader_reports_retention_and_coverage(reader_client):
    body = reader_client.get("/api/admin/audit-logs/retention", headers=_headers()).json()
    assert body["retention_days"] == 730
    assert body["retains_indefinitely"] is False
    assert body["total_entries"] == 3
    assert body["failed_entries"] == 1
    assert body["oldest_entry_at"] and body["newest_entry_at"]


def test_reader_requires_the_highest_admin_permission(reader_client):
    """The trail holds before/after state for users, subscriptions and
    entitlements, so a narrower role must not be able to read it."""
    reader_client.app.dependency_overrides[get_current_admin] = _data_admin_dep
    response = reader_client.get("/api/admin/audit-logs", headers=_headers())
    assert response.status_code == 403


def test_reader_exposes_no_write_endpoints(client):
    """The table is append-only. A PATCH or DELETE route here would be a way to
    edit history."""
    audit_routes = [
        r for r in _api_routes(client.app)
        if r.path.startswith("/api/admin/audit-logs")
    ]
    assert audit_routes
    for route in audit_routes:
        assert set(route.methods) <= {"GET", "HEAD", "OPTIONS"}, route.path
