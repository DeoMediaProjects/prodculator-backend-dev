from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database_client import DatabaseClient
from app.core.db import get_db_context
from app.core.dependencies import get_current_admin, get_current_user
from app.modules.admin.schemas import AdminUser
from app.modules.auth.schemas import AuthUser
from app.modules.b2b.router import get_stripe_service
from app.modules.b2b.service import B2BService, process_request_task
from app.modules.email.service import EmailService
from app.modules.payments import router as payments_router
from app.modules.reports.pdf_service import PDFService

HEADERS = {"Authorization": "Bearer token"}


class FakeB2BStripeService:
    def create_b2b_subscription_checkout(self, **kwargs):
        self.kwargs = kwargs
        return {"session_id": "cs_b2b", "url": "https://checkout.stripe.test/b2b"}


def _db() -> DatabaseClient:
    ctx = get_db_context()
    session = ctx.__enter__()
    client = DatabaseClient(session, get_settings())
    client._ctx = ctx  # type: ignore[attr-defined]
    return client


def _clear_tables() -> None:
    with get_db_context() as session:
        for table in [
            "b2b_intelligence_requests",
            "b2b_subscriptions",
            "production_signals",
            "subscriptions",
            "processed_webhook_events",
            "users",
        ]:
            session.execute(text(f"DELETE FROM {table}"))
        session.commit()


def _seed_user(db: DatabaseClient, user_id: str = "user-1", email: str = "user@example.com") -> None:
    db.table("users").insert(
        {
            "id": user_id,
            "email": email,
            "password_hash": None,
            "name": "User",
            "user_type": "free",
            "credits_remaining": 0,
            "plan": "free",
            "email_verified": True,
            "is_blocked": False,
            "created_at": datetime.now(timezone.utc),
        }
    ).execute()


def _seed_b2b_subscription(
    db: DatabaseClient,
    user_id: str = "user-1",
    product_type: str = "camera_equipment",
) -> str:
    row = db.table("b2b_subscriptions").insert(
        {
            "id": "b2b-sub-1",
            "user_id": user_id,
            "product_type": product_type,
            "status": "active",
            "source": "stripe",
            "delivery_frequency": "monthly",
            "cancel_at_period_end": False,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    ).execute().data[0]
    return row["id"]


def _seed_signals(db: DatabaseClient, count: int, *, territory: str = "United Kingdom") -> None:
    payload = []
    for index in range(count):
        payload.append(
            {
                "id": f"signal-{territory}-{index}",
                "script_id": f"script-{territory}-{index}",
                "territory": territory,
                "submission_date": date(2026, 1, 10),
                "camera_equipment": ["ARRI Alexa"],
                "crew_size": 30 + index,
                "principal_cast": 3,
                "supporting_cast": 7,
                "background_extras": 25,
                "budget_range": "£1m-£5m",
                "format": "Feature",
                "genres": ["Drama"],
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
    db.table("production_signals").insert(payload).execute()


def test_b2b_checkout_is_independent_from_normal_subscription(client, auth_user, monkeypatch):
    _clear_tables()
    settings = get_settings()
    monkeypatch.setattr(settings, "STRIPE_PRICE_B2B_CAMERA_EQUIPMENT_GBP", "price_b2b_camera_gbp")

    db = _db()
    try:
        db.table("subscriptions").insert(
            {
                "id": "normal-sub-1",
                "user_id": auth_user.id,
                "stripe_subscription_id": "sub_normal",
                "plan_type": "producer",
                "status": "active",
                "report_limit": 3,
                "cancel_at_period_end": False,
                "created_at": datetime.now(timezone.utc),
            }
        ).execute()
    finally:
        db.close()

    fake_stripe = FakeB2BStripeService()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_stripe_service] = lambda: fake_stripe

    response = client.post(
        "/api/b2b/checkout",
        headers=HEADERS,
        json={
            "product_type": "camera_equipment",
            "currency": "gbp",
            "delivery_frequency": "monthly",
        },
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "cs_b2b"
    assert fake_stripe.kwargs["price_id"] == "price_b2b_camera_gbp"


def test_b2b_request_requires_active_matching_subscription(client, auth_user):
    _clear_tables()
    client.app.dependency_overrides[get_current_user] = lambda: auth_user

    response = client.post(
        "/api/b2b/requests",
        headers=HEADERS,
        json={
            "product_type": "camera_equipment",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
        },
    )

    assert response.status_code == 403


def test_b2b_request_records_history_without_running_pdf_in_route(client, auth_user, monkeypatch):
    _clear_tables()
    db = _db()
    try:
        _seed_b2b_subscription(db, user_id=auth_user.id)
    finally:
        db.close()

    processed: list[str] = []
    monkeypatch.setattr(B2BService, "process_request", lambda self, request_id: processed.append(request_id))
    client.app.dependency_overrides[get_current_user] = lambda: auth_user

    response = client.post(
        "/api/b2b/requests",
        headers=HEADERS,
        json={
            "product_type": "camera_equipment",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "extra_recipient_email": "finance@example.com",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processing"
    assert body["extra_recipient_email"] == "finance@example.com"
    assert processed == [body["id"]]

    list_response = client.get("/api/b2b/requests", headers=HEADERS)
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1


def test_b2b_metrics_apply_overall_and_segment_privacy_thresholds():
    _clear_tables()
    db = _db()
    try:
        service = B2BService(db, get_settings())
        _seed_signals(db, 9, territory="United Kingdom")
        insufficient = service.build_metrics(
            product_type="camera_equipment",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
        assert insufficient["insufficient_data"] is True
        assert insufficient["source_signal_count"] == 9

        _clear_tables()
        _seed_signals(db, 6, territory="United Kingdom")
        _seed_signals(db, 4, territory="Ireland")
        metrics = service.build_metrics(
            product_type="camera_equipment",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
        assert metrics["insufficient_data"] is False
        territory_section = next(section for section in metrics["sections"] if section["title"] == "Production Volume by Territory")
        assert [row["label"] for row in territory_section["rows"]] == ["United Kingdom"]
        assert any(segment["label"] == "Ireland" for segment in metrics["suppressed_segments"])
    finally:
        db.close()


def test_b2b_webhook_creates_b2b_subscription_without_changing_normal_plan(client, monkeypatch):
    _clear_tables()
    db = _db()
    try:
        _seed_user(db)
    finally:
        db.close()

    fake_event = SimpleNamespace(
        id="evt_b2b_checkout",
        type="checkout.session.completed",
        data=SimpleNamespace(
            object={
                "id": "cs_b2b",
                "mode": "subscription",
                "metadata": {
                    "userId": "user-1",
                    "subscriptionKind": "b2b",
                    "productType": "camera_equipment",
                    "priceId": "price_b2b",
                    "currency": "gbp",
                    "deliveryFrequency": "monthly",
                },
                "subscription": "sub_b2b",
                "customer": "cus_b2b",
            }
        ),
    )
    monkeypatch.setattr(
        payments_router.StripeService,
        "construct_webhook_event",
        lambda self, payload, sig_header: fake_event,
    )

    response = client.post("/api/webhooks/stripe", data=b"{}", headers={"stripe-signature": "ok"})
    assert response.status_code == 200

    db = _db()
    try:
        user = db.table("users").select("plan,user_type").eq("id", "user-1").single().execute().data
        b2b_rows = db.table("b2b_subscriptions").select("*").eq("user_id", "user-1").execute().data
        normal_rows = db.table("subscriptions").select("*").eq("user_id", "user-1").execute().data
    finally:
        db.close()

    assert user["plan"] == "free"
    assert user["user_type"] == "free"
    assert len(b2b_rows) == 1
    assert b2b_rows[0]["stripe_subscription_id"] == "sub_b2b"
    assert normal_rows == []


def test_admin_update_persists_and_notifies_user(client, monkeypatch):
    _clear_tables()
    sent: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        EmailService,
        "send",
        lambda self, to_email, template_name, context=None, attachments=None: sent.append(
            (to_email, template_name, context or {})
        ),
    )
    db = _db()
    try:
        _seed_user(db, email="buyer@example.com")
        subscription_id = _seed_b2b_subscription(db)
    finally:
        db.close()

    admin = AdminUser(id="admin-1", email="admin@example.com", name="Admin", role="master_admin")
    client.app.dependency_overrides[get_current_admin] = lambda: admin

    response = client.patch(
        f"/api/admin/b2b/subscriptions/{subscription_id}",
        headers=HEADERS,
        json={
            "delivery_frequency": "quarterly",
            "extra_recipient_email": "ops@example.com",
        },
    )

    assert response.status_code == 200
    assert response.json()["delivery_frequency"] == "quarterly"
    assert response.json()["extra_recipient_email"] == "ops@example.com"
    assert sent
    assert sent[0][0] == "buyer@example.com"
    assert sent[0][1] == "b2b_subscription_updated"


def test_b2b_process_request_task_completes_on_a_fresh_session(monkeypatch, tmp_path):
    """The background task must open its own DB session and run the full pipeline.

    Regression for the bug where the route enqueued ``service.process_request``,
    which captured the request-scoped session FastAPI had already closed. Here we
    deliberately close the session that created the request before invoking the
    task, proving the task no longer depends on it.
    """
    _clear_tables()
    settings = get_settings()
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    # Avoid the WeasyPrint/SMTP dependencies — exercise the DB + storage path.
    monkeypatch.setattr(PDFService, "generate_pdf_bytes", lambda self, html: b"%PDF-1.4 b2b-test")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        EmailService,
        "send",
        lambda self, to_email, template_name, context=None, attachments=None: sent.append(
            (to_email, template_name)
        ),
    )

    db = _db()
    try:
        _seed_user(db, email="buyer@example.com")
        _seed_b2b_subscription(db, user_id="user-1")
        _seed_signals(db, 12, territory="United Kingdom")
        request = B2BService(db, settings).create_intelligence_request(
            user_id="user-1",
            user_email="buyer@example.com",
            product_type="camera_equipment",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            extra_recipient_email="finance@example.com",
        )
        request_id = request["id"]
    finally:
        db.close()  # the request-scoped session is now gone, as after a route returns

    process_request_task(request_id)

    db = _db()
    try:
        row = (
            db.table("b2b_intelligence_requests")
            .select("*")
            .eq("id", request_id)
            .single()
            .execute()
            .data
        )
    finally:
        db.close()

    assert row["status"] == "completed"
    assert row["pdf_url"]
    assert row["delivered_at"] is not None
    assert row["metrics"]["source_signal_count"] == 12

    delivered = {to_email for to_email, template in sent if template == "b2b_intelligence_ready"}
    assert delivered == {"buyer@example.com", "finance@example.com"}
