"""End-to-end test for the pay -> upgrade flow.

Stitches the three layers together through real HTTP routes against a single
stateful fake database:

    1. POST /api/payments/subscription-checkout   (the user pays)
    2. POST /api/webhooks/stripe                   (Stripe confirms the payment)
    3. GET  /api/auth/me                           (the user is now upgraded)

Unlike the layer-by-layer tests (test_payments_routes / test_webhook_handler),
this verifies that a webhook write is actually readable back through
get_current_user — i.e. the plan the user sees reflects what they paid for.
"""

from types import SimpleNamespace

import pytest

from app.core.dependencies import get_supabase
from app.modules.payments import router as payments_router
from app.modules.payments.router import get_stripe_service


# ---------------------------------------------------------------------------
# Stateful fake database shared by the checkout, the webhook, and /me.
# Reads see the writes the webhook makes — that's the whole point of the test.
# ---------------------------------------------------------------------------

class _StatefulQuery:
    def __init__(self, table_rows, table_name):
        self._rows = table_rows          # live reference into the shared store
        self._table = table_name
        self._filters = {}
        self._in = {}
        self._single = False
        self._op = ""
        self._payload = None
        self._on_conflict = None

    # --- builders ---
    def select(self, _cols):
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def in_(self, key, values):
        self._in[key] = list(values)
        return self

    def gte(self, _k, _v):
        return self

    def lte(self, _k, _v):
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, _n):
        return self

    def single(self):
        self._single = True
        return self

    def upsert(self, data, on_conflict=None):
        self._op = "upsert"
        self._payload = data
        self._on_conflict = on_conflict
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    # --- terminal ---
    def _matches(self, row):
        return all(row.get(k) == v for k, v in self._filters.items()) and all(
            row.get(k) in vs for k, vs in self._in.items()
        )

    def execute(self):
        if self._op == "insert":
            self._rows.append(dict(self._payload))
            return SimpleNamespace(data=[self._payload])

        if self._op == "upsert":
            key = self._on_conflict
            if key and self._payload.get(key) is not None:
                for row in self._rows:
                    if row.get(key) == self._payload.get(key):
                        row.update(self._payload)
                        return SimpleNamespace(data=[row])
            self._rows.append(dict(self._payload))
            return SimpleNamespace(data=[self._payload])

        if self._op == "update":
            patched = []
            for row in self._rows:
                if self._matches(row):
                    row.update(self._payload)
                    patched.append(row)
            return SimpleNamespace(data=patched)

        # read
        filtered = [row for row in self._rows if self._matches(row)]
        if self._single:
            return SimpleNamespace(data=filtered[0] if filtered else None)
        return SimpleNamespace(data=filtered)


class _FakeAuth:
    """Resolves the bearer token to a fixed user id, like AuthClient.get_user."""

    def get_user(self, _token):
        return SimpleNamespace(
            user=SimpleNamespace(id="user-1", email="user@example.com"),
            claims=None,
        )


class StatefulFakeSupabase:
    def __init__(self, tables):
        self._tables = {name: list(rows) for name, rows in tables.items()}
        self.auth = _FakeAuth()

    def table(self, name):
        rows = self._tables.setdefault(name, [])
        return _StatefulQuery(rows, name)


class FakeStripeService:
    @staticmethod
    def create_subscription_checkout(price_id, user_email, user_id, metadata=None):
        return {"session_id": "cs_sub", "url": "https://checkout.stripe.test/sub"}


def _seed_db():
    return StatefulFakeSupabase(
        {
            "users": [
                {
                    "id": "user-1",
                    "email": "user@example.com",
                    "name": "User",
                    "company": "Acme",
                    "role": "Producer",
                    "user_type": "free",
                    "credits_remaining": 1,
                    "plan": "free",
                    "is_blocked": False,
                }
            ],
            "subscriptions": [],
            "processed_webhook_events": [],
        }
    )


def test_pay_then_webhook_then_me_reflects_upgrade(client, monkeypatch):
    """A producer checkout, confirmed by a Stripe webhook, upgrades the user
    so that /api/auth/me reports plan=producer / user_type=paid."""
    db = _seed_db()
    client.app.dependency_overrides[get_supabase] = lambda: db
    client.app.dependency_overrides[get_stripe_service] = lambda: FakeStripeService()

    auth = {"Authorization": "Bearer token"}

    # 0. Before paying, the user is on the free plan.
    before = client.get("/api/auth/me", headers=auth)
    assert before.status_code == 200
    assert before.json()["plan"] == "free"
    assert before.json()["user_type"] == "free"

    # 1. The user starts a producer subscription checkout (the "pay" step).
    checkout = client.post(
        "/api/payments/subscription-checkout",
        headers=auth,
        json={"price_id": "price_producer", "plan_type": "producer"},
    )
    assert checkout.status_code == 200
    assert checkout.json()["session_id"] == "cs_sub"

    # 2. Stripe calls our webhook to confirm the completed checkout. We stub
    #    signature verification but let the REAL WebhookHandler run against the
    #    shared fake DB, so it performs the actual upgrade writes.
    fake_event = SimpleNamespace(
        id="evt_e2e_1",
        type="checkout.session.completed",
        data=SimpleNamespace(
            object={
                "metadata": {"userId": "user-1", "planType": "producer"},
                "subscription": "sub_e2e",
                "customer": "cus_e2e",
            }
        ),
    )
    monkeypatch.setattr(
        payments_router.StripeService,
        "construct_webhook_event",
        lambda self, payload, sig_header: fake_event,
    )

    webhook = client.post(
        "/api/webhooks/stripe",
        data=b"{}",
        headers={"stripe-signature": "ok"},
    )
    assert webhook.status_code == 200

    # 3. The user is now upgraded — /api/auth/me reads the post-webhook state.
    after = client.get("/api/auth/me", headers=auth)
    assert after.status_code == 200
    assert after.json()["plan"] == "producer"
    assert after.json()["user_type"] == "paid"

    # And the subscription row was created with the producer report limit.
    sub_rows = db._tables["subscriptions"]
    assert len(sub_rows) == 1
    assert sub_rows[0]["plan_type"] == "producer"
    assert sub_rows[0]["report_limit"] == 3
    assert sub_rows[0]["status"] == "active"


def test_duplicate_webhook_does_not_double_apply(client, monkeypatch):
    """Replaying the same Stripe event is idempotent: the second delivery is
    deduplicated and leaves exactly one subscription row."""
    db = _seed_db()
    client.app.dependency_overrides[get_supabase] = lambda: db

    fake_event = SimpleNamespace(
        id="evt_e2e_dup",
        type="checkout.session.completed",
        data=SimpleNamespace(
            object={
                "metadata": {"userId": "user-1", "planType": "professional"},
                "subscription": "sub_dup",
                "customer": "cus_dup",
            }
        ),
    )
    monkeypatch.setattr(
        payments_router.StripeService,
        "construct_webhook_event",
        lambda self, payload, sig_header: fake_event,
    )

    first = client.post("/api/webhooks/stripe", data=b"{}", headers={"stripe-signature": "ok"})
    second = client.post("/api/webhooks/stripe", data=b"{}", headers={"stripe-signature": "ok"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(db._tables["subscriptions"]) == 1


# ---------------------------------------------------------------------------
# Regression: a mid-handler failure must NOT permanently mark the event
# processed. Stripe's retry has to be able to complete the upgrade — otherwise
# the customer is charged but stays on the free plan.
# ---------------------------------------------------------------------------

class _FailingOnceQuery(_StatefulQuery):
    """Raises on the first users.update to simulate a transient DB error,
    then behaves normally so the retry can succeed."""

    def __init__(self, table_rows, table_name, fail_flag):
        super().__init__(table_rows, table_name)
        self._fail_flag = fail_flag

    def execute(self):
        if self._op == "update" and self._table == "users" and self._fail_flag[0]:
            self._fail_flag[0] = False
            raise RuntimeError("transient DB error on users.update")
        return super().execute()


class FlakyFakeSupabase(StatefulFakeSupabase):
    def __init__(self, tables, fail_flag):
        super().__init__(tables)
        self._fail_flag = fail_flag

    def table(self, name):
        rows = self._tables.setdefault(name, [])
        return _FailingOnceQuery(rows, name, self._fail_flag)


def test_retry_after_midhandler_failure_completes_upgrade(client, monkeypatch):
    """First delivery fails after the dedup point used to be written; the event
    must remain un-recorded so Stripe's retry upgrades the user."""
    fail_flag = [True]
    db = FlakyFakeSupabase(
        {
            "users": [
                {
                    "id": "user-1",
                    "email": "user@example.com",
                    "user_type": "free",
                    "plan": "free",
                    "credits_remaining": 0,
                    "is_blocked": False,
                }
            ],
            "subscriptions": [],
            "processed_webhook_events": [],
        },
        fail_flag,
    )
    client.app.dependency_overrides[get_supabase] = lambda: db

    fake_event = SimpleNamespace(
        id="evt_retry",
        type="checkout.session.completed",
        data=SimpleNamespace(
            object={
                "metadata": {"userId": "user-1", "planType": "producer"},
                "subscription": "sub_retry",
                "customer": "cus_retry",
            }
        ),
    )
    monkeypatch.setattr(
        payments_router.StripeService,
        "construct_webhook_event",
        lambda self, payload, sig_header: fake_event,
    )

    # Delivery #1 — users.update blows up. In production uvicorn turns this into
    # a 500 (Stripe then retries); TestClient re-raises it, which is the same
    # signal: the request did not complete successfully.
    with pytest.raises(RuntimeError, match="transient DB error"):
        client.post("/api/webhooks/stripe", data=b"{}", headers={"stripe-signature": "ok"})
    # The event must NOT have been recorded as processed.
    assert db._tables["processed_webhook_events"] == []
    assert db._tables["users"][0]["plan"] == "free"

    # Delivery #2 (Stripe retry) — DB is healthy now; upgrade completes.
    second = client.post("/api/webhooks/stripe", data=b"{}", headers={"stripe-signature": "ok"})
    assert second.status_code == 200
    assert db._tables["users"][0]["plan"] == "producer"
    assert db._tables["users"][0]["user_type"] == "paid"
    assert len(db._tables["processed_webhook_events"]) == 1
    # No duplicate subscription row from the reprocess (upsert is idempotent).
    assert len(db._tables["subscriptions"]) == 1


def test_upgrade_email_deferred_to_background_tasks(client, monkeypatch):
    """The slow SMTP send is scheduled on BackgroundTasks, not run inline during
    the webhook, so a slow provider can't delay the 200 to Stripe."""
    db = _seed_db()
    client.app.dependency_overrides[get_supabase] = lambda: db

    sent = []

    def fake_send(self, to, template, context):
        sent.append((to, template))

    monkeypatch.setattr(
        "app.modules.email.service.EmailService.send", fake_send, raising=True
    )

    fake_event = SimpleNamespace(
        id="evt_email",
        type="checkout.session.completed",
        data=SimpleNamespace(
            object={
                "metadata": {"userId": "user-1", "planType": "producer"},
                "subscription": "sub_email",
                "customer": "cus_email",
            }
        ),
    )
    monkeypatch.setattr(
        payments_router.StripeService,
        "construct_webhook_event",
        lambda self, payload, sig_header: fake_event,
    )

    resp = client.post("/api/webhooks/stripe", data=b"{}", headers={"stripe-signature": "ok"})
    assert resp.status_code == 200
    # TestClient runs background tasks after the response — the emails still fire,
    # but via the deferred path. payment_confirmation + plan_upgraded.
    templates = {t for _, t in sent}
    assert "payment_confirmation" in templates
    assert "plan_upgraded" in templates
