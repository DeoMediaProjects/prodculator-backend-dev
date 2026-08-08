"""Manual-contract invite flow (handoff §4.3/§4.4).

The workflow this replaces: a contracted client had to sign up first, then be
provisioned by hand — ``create_manual_subscription`` refuses outright until a
user row exists. An invite defers subscription creation to the client's claim,
so the admin can act on a signed contract before the client has an account.

The properties worth defending, in order of consequence:

* the raw token is never stored, so a database leak yields no usable links;
* the claim is bound to the invited address, so a forwarded email cannot
  transfer a paid entitlement;
* claiming twice does not create two subscriptions;
* a failure during claim leaves the invite claimable rather than consumed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.core.dependencies import get_current_admin, get_current_user, get_supabase
from app.modules.admin.schemas import AdminUser
from app.modules.auth.schemas import AuthUser
from app.modules.b2b.invite_service import (
    STATUS_ACCEPTED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REVOKED,
    B2BInviteService,
    InviteEmailMismatch,
    InviteError,
    InviteNotClaimable,
    InviteNotFound,
    hash_token,
    mint_token,
)
from app.modules.b2b.service import B2BService
from app.core.config import Settings

PRODUCT = "camera_equipment"
CLIENT_EMAIL = "buyer@greyconsortium.example"


def _settings() -> Settings:
    return Settings(
        FRONTEND_URL="https://app.prodculator.com",
        JWT_SECRET_KEY="test-secret-key-with-at-least-32-chars",
    )


class FakeInviteTable:
    """Minimal stand-in for the invites table, with real filter semantics."""

    def __init__(self, store: list[dict]):
        self.store = store
        self._filters: dict = {}
        self._action = "select"
        self._payload: dict | None = None

    def select(self, *_a, **_k):
        self._action = "select"
        return self

    def insert(self, payload):
        row = dict(payload)
        self.store.append(row)
        self._action = "insert"
        self._payload = row
        return self

    def update(self, payload):
        self._action = "update"
        self._payload = dict(payload)
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def limit(self, _n):
        return self

    def order(self, *_a, **_k):
        return self

    def _matching(self):
        return [
            row for row in self.store
            if all(row.get(k) == v for k, v in self._filters.items())
        ]

    def execute(self):
        if self._action == "insert":
            return MagicMock(data=[self._payload])
        if self._action == "update":
            rows = self._matching()
            for row in rows:
                row.update(self._payload or {})
            return MagicMock(data=rows)
        return MagicMock(data=self._matching())


class FakeDB:
    def __init__(self):
        self.invites: list[dict] = []
        self.subscriptions: list[dict] = []

    def table(self, name):
        if name == "b2b_contract_invites":
            return FakeInviteTable(self.invites)
        if name == "b2b_subscriptions":
            return FakeInviteTable(self.subscriptions)
        return FakeInviteTable([])


def _service(db=None, email=None) -> B2BInviteService:
    return B2BInviteService(db or FakeDB(), _settings(), email or MagicMock())


def _b2b(db) -> B2BService:
    service = B2BService.__new__(B2BService)
    service.db = db
    service.settings = _settings()
    return service


def _issue(svc: B2BInviteService, **overrides):
    kwargs = {
        "email": CLIENT_EMAIL,
        "product_type": PRODUCT,
        "company_name": "Grey Consortium UK",
        "created_by": "admin-1",
    }
    kwargs.update(overrides)
    return svc.issue(**kwargs)


# ── Tokens ──────────────────────────────────────────────────────────────────


class TestTokens:
    def test_tokens_are_unique_and_url_safe(self):
        tokens = {mint_token()[0] for _ in range(200)}
        assert len(tokens) == 200
        for token in tokens:
            assert token == token.strip()
            assert "/" not in token and "+" not in token and "=" not in token

    def test_only_the_hash_is_stored(self):
        db = FakeDB()
        svc = _service(db)
        _invite, url = _issue(svc)
        raw = url.rsplit("/", 1)[-1]

        stored = db.invites[0]
        assert "token" not in stored
        assert stored["token_hash"] == hash_token(raw)
        assert raw not in str(stored), "the raw token must not be recoverable from the row"

    def test_the_admin_view_never_exposes_the_hash(self):
        db = FakeDB()
        invite, _url = _issue(_service(db))
        assert "token_hash" not in invite
        # A short prefix is kept so two outstanding invites are distinguishable.
        assert invite["token_prefix"] and len(invite["token_prefix"]) <= 8

    def test_the_accept_url_points_at_the_frontend(self):
        _invite, url = _issue(_service())
        assert url.startswith("https://app.prodculator.com/b2b/invite/")


# ── Issue ───────────────────────────────────────────────────────────────────


class TestIssue:
    def test_issuing_records_the_contract_terms(self):
        db = FakeDB()
        invite, _url = _issue(
            _service(db), delivery_frequency="quarterly", admin_notes="Signed 2026-08-01",
        )
        assert invite["email"] == CLIENT_EMAIL
        assert invite["product_type"] == PRODUCT
        assert invite["status"] == STATUS_PENDING
        assert invite["delivery_frequency"] == "quarterly"
        assert invite["company_name"] == "Grey Consortium UK"
        assert invite["admin_notes"] == "Signed 2026-08-01"
        assert invite["created_by"] == "admin-1"

    def test_the_invited_email_is_normalised(self):
        db = FakeDB()
        invite, _url = _issue(_service(db), email="  Buyer@Example.COM ")
        assert invite["email"] == "buyer@example.com"

    def test_issuing_emails_the_client_with_the_accept_link(self):
        email = MagicMock()
        _invite, url = _issue(_service(FakeDB(), email))
        assert email.send.call_count == 1
        recipient, template, context = email.send.call_args[0]
        assert recipient == CLIENT_EMAIL
        assert template == "b2b_contract_invite"
        assert context["accept_url"] == url

    def test_email_can_be_suppressed_when_the_link_is_passed_on_by_hand(self):
        email = MagicMock()
        _invite, url = _issue(_service(FakeDB(), email), send_email=False)
        assert email.send.call_count == 0
        assert url

    def test_a_mail_failure_does_not_lose_the_invite(self):
        """The admin holds the accept URL, so a bounced send must not discard the
        invite that a signed contract depends on."""
        email = MagicMock()
        email.send.side_effect = RuntimeError("smtp down")
        db = FakeDB()
        invite, url = _issue(_service(db, email))
        assert invite["status"] == STATUS_PENDING
        assert len(db.invites) == 1
        assert url

    def test_a_second_outstanding_invite_is_refused(self):
        svc = _service(FakeDB())
        _issue(svc)
        with pytest.raises(InviteError, match="outstanding invite"):
            _issue(svc)

    def test_a_new_invite_is_allowed_once_the_first_is_revoked(self):
        db = FakeDB()
        svc = _service(db)
        invite, _url = _issue(svc)
        svc.revoke(invite["id"])
        second, _url2 = _issue(svc)
        assert second["status"] == STATUS_PENDING

    @pytest.mark.parametrize("days", [0, -1, 366])
    def test_an_unreasonable_expiry_is_refused(self, days):
        with pytest.raises(InviteError, match="expires_in_days"):
            _issue(_service(), expires_in_days=days)

    def test_a_blank_email_is_refused(self):
        with pytest.raises(InviteError, match="email address is required"):
            _issue(_service(), email="   ")


# ── Derived status ──────────────────────────────────────────────────────────


class TestStatus:
    def test_expiry_is_derived_not_stored(self):
        """An invite past its expiry must read as expired without a job running."""
        svc = _service()
        row = {
            "status": STATUS_PENDING,
            "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        assert svc.effective_status(row) == STATUS_EXPIRED

    def test_a_live_invite_reads_as_pending(self):
        svc = _service()
        row = {
            "status": STATUS_PENDING,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        }
        assert svc.effective_status(row) == STATUS_PENDING

    def test_a_terminal_status_is_not_overridden_by_the_clock(self):
        svc = _service()
        past = datetime.now(timezone.utc) - timedelta(days=100)
        assert svc.effective_status({"status": STATUS_ACCEPTED, "expires_at": past}) == STATUS_ACCEPTED
        assert svc.effective_status({"status": STATUS_REVOKED, "expires_at": past}) == STATUS_REVOKED

    def test_a_naive_stored_timestamp_is_read_as_utc(self):
        """SQLite-backed rows come back naive; treating them as local time would
        misjudge expiry by the host's offset."""
        svc = _service()
        naive_utc = (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None)
        assert svc.effective_status(
            {"status": STATUS_PENDING, "expires_at": naive_utc}
        ) == STATUS_PENDING


# ── Claim ───────────────────────────────────────────────────────────────────


class TestClaim:
    def _prepare(self):
        db = FakeDB()
        svc = _service(db)
        _invite, url = _issue(svc)
        token = url.rsplit("/", 1)[-1]
        return db, svc, token

    def test_claiming_creates_and_links_the_subscription(self):
        db, svc, token = self._prepare()
        subscription = svc.accept(
            token=token, user_id="user-9", user_email=CLIENT_EMAIL, b2b_service=_b2b(db),
        )
        assert subscription["user_id"] == "user-9"
        assert subscription["product_type"] == PRODUCT
        assert subscription["source"] == "manual_contract"
        assert subscription["company_name"] == "Grey Consortium UK"

        invite = db.invites[0]
        assert invite["status"] == STATUS_ACCEPTED
        assert invite["accepted_by_user_id"] == "user-9"
        assert invite["b2b_subscription_id"] == subscription["id"]
        assert invite["accepted_at"]

    def test_the_contracted_delivery_frequency_carries_through(self):
        db = FakeDB()
        svc = _service(db)
        _invite, url = _issue(svc, delivery_frequency="quarterly")
        subscription = svc.accept(
            token=url.rsplit("/", 1)[-1], user_id="u1",
            user_email=CLIENT_EMAIL, b2b_service=_b2b(db),
        )
        assert subscription["delivery_frequency"] == "quarterly"

    def test_an_unknown_token_is_not_found(self):
        _db, svc, _token = self._prepare()
        with pytest.raises(InviteNotFound):
            svc.accept(
                token="not-a-real-token", user_id="u1",
                user_email=CLIENT_EMAIL, b2b_service=_b2b(FakeDB()),
            )

    def test_a_different_signed_in_user_cannot_claim_it(self):
        """A forwarded invite email must not transfer a paid entitlement."""
        db, svc, token = self._prepare()
        with pytest.raises(InviteEmailMismatch) as exc_info:
            svc.accept(
                token=token, user_id="attacker",
                user_email="someone.else@example.com", b2b_service=_b2b(db),
            )
        assert exc_info.value.invited_email == CLIENT_EMAIL
        assert db.subscriptions == []
        assert db.invites[0]["status"] == STATUS_PENDING

    def test_email_matching_ignores_case_and_whitespace(self):
        db, svc, token = self._prepare()
        subscription = svc.accept(
            token=token, user_id="u1",
            user_email=f"  {CLIENT_EMAIL.upper()} ", b2b_service=_b2b(db),
        )
        assert subscription["id"]

    def test_claiming_twice_returns_the_same_subscription(self):
        db, svc, token = self._prepare()
        b2b = _b2b(db)
        first = svc.accept(
            token=token, user_id="u1", user_email=CLIENT_EMAIL, b2b_service=b2b,
        )
        b2b.get_subscription = lambda sid: next(
            (s for s in db.subscriptions if s["id"] == sid), None
        )
        second = svc.accept(
            token=token, user_id="u1", user_email=CLIENT_EMAIL, b2b_service=b2b,
        )
        assert first["id"] == second["id"]
        assert len(db.subscriptions) == 1

    def test_someone_else_cannot_reuse_an_accepted_invite(self):
        db, svc, token = self._prepare()
        svc.accept(token=token, user_id="u1", user_email=CLIENT_EMAIL, b2b_service=_b2b(db))
        with pytest.raises(InviteNotClaimable) as exc_info:
            svc.accept(
                token=token, user_id="u2", user_email=CLIENT_EMAIL, b2b_service=_b2b(db),
            )
        assert exc_info.value.status == STATUS_ACCEPTED

    def test_a_revoked_invite_cannot_be_claimed(self):
        db, svc, token = self._prepare()
        svc.revoke(db.invites[0]["id"])
        with pytest.raises(InviteNotClaimable) as exc_info:
            svc.accept(token=token, user_id="u1", user_email=CLIENT_EMAIL, b2b_service=_b2b(db))
        assert exc_info.value.status == STATUS_REVOKED
        assert db.subscriptions == []

    def test_an_expired_invite_cannot_be_claimed(self):
        db, svc, token = self._prepare()
        db.invites[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(days=1)
        with pytest.raises(InviteNotClaimable) as exc_info:
            svc.accept(token=token, user_id="u1", user_email=CLIENT_EMAIL, b2b_service=_b2b(db))
        assert exc_info.value.status == STATUS_EXPIRED
        assert db.subscriptions == []

    def test_a_failed_subscription_write_leaves_the_invite_claimable(self):
        """Ordering matters: consuming the invite and then failing would strand a
        contracted client with no subscription and no way back in."""
        db, svc, token = self._prepare()
        b2b = _b2b(db)
        b2b.create_manual_subscription_for_user = MagicMock(
            side_effect=RuntimeError("db down")
        )
        with pytest.raises(RuntimeError):
            svc.accept(token=token, user_id="u1", user_email=CLIENT_EMAIL, b2b_service=b2b)
        assert db.invites[0]["status"] == STATUS_PENDING


# ── Resend and revoke ───────────────────────────────────────────────────────


class TestResendRevoke:
    def test_resending_rotates_the_token_and_kills_the_old_link(self):
        db = FakeDB()
        svc = _service(db)
        _invite, first_url = _issue(svc)
        first_token = first_url.rsplit("/", 1)[-1]

        _resent, second_url = svc.resend(db.invites[0]["id"])
        second_token = second_url.rsplit("/", 1)[-1]

        assert first_token != second_token
        assert svc.get_by_token(first_token) is None
        assert svc.get_by_token(second_token) is not None

    def test_resending_counts_the_send(self):
        db = FakeDB()
        svc = _service(db)
        _issue(svc)
        resent, _url = svc.resend(db.invites[0]["id"])
        assert resent["sent_count"] == 1
        assert resent["last_sent_at"]

    def test_resending_revives_an_expired_invite(self):
        db = FakeDB()
        svc = _service(db)
        _issue(svc)
        db.invites[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(days=1)
        resent, _url = svc.resend(db.invites[0]["id"])
        assert resent["status"] == STATUS_PENDING

    def test_an_accepted_invite_cannot_be_resent(self):
        db = FakeDB()
        svc = _service(db)
        _invite, url = _issue(svc)
        svc.accept(
            token=url.rsplit("/", 1)[-1], user_id="u1",
            user_email=CLIENT_EMAIL, b2b_service=_b2b(db),
        )
        with pytest.raises(InviteNotClaimable):
            svc.resend(db.invites[0]["id"])

    def test_an_accepted_invite_cannot_be_revoked(self):
        """Revoking would not undo the subscription it created, so refusing is
        the honest answer."""
        db = FakeDB()
        svc = _service(db)
        _invite, url = _issue(svc)
        svc.accept(
            token=url.rsplit("/", 1)[-1], user_id="u1",
            user_email=CLIENT_EMAIL, b2b_service=_b2b(db),
        )
        with pytest.raises(InviteNotClaimable, match="already been accepted"):
            svc.revoke(db.invites[0]["id"])

    def test_resending_an_unknown_invite_is_not_found(self):
        with pytest.raises(InviteNotFound):
            _service().resend("nope")

    def test_revoking_an_unknown_invite_is_not_found(self):
        with pytest.raises(InviteNotFound):
            _service().revoke("nope")


# ── Listing and preview ─────────────────────────────────────────────────────


class TestListingAndPreview:
    def test_listing_filters_by_derived_status(self):
        db = FakeDB()
        svc = _service(db)
        _issue(svc, email="live@example.com")
        _issue(svc, email="stale@example.com")
        db.invites[1]["expires_at"] = datetime.now(timezone.utc) - timedelta(days=1)

        pending, total_pending = svc.list_invites(status=STATUS_PENDING)
        expired, total_expired = svc.list_invites(status=STATUS_EXPIRED)
        assert [i["email"] for i in pending] == ["live@example.com"]
        assert [i["email"] for i in expired] == ["stale@example.com"]
        assert (total_pending, total_expired) == (1, 1)

    def test_listing_filters_by_email_substring_and_product(self):
        db = FakeDB()
        svc = _service(db)
        _issue(svc, email="a@grey.example")
        _issue(svc, email="b@other.example")
        found, total = svc.list_invites(email="grey")
        assert total == 1 and found[0]["email"] == "a@grey.example"

        found2, total2 = svc.list_invites(product_type="crew_casting")
        assert total2 == 0 and found2 == []

    def test_preview_shows_what_is_being_claimed(self):
        db = FakeDB()
        svc = _service(db)
        _invite, url = _issue(svc)
        preview = svc.preview(url.rsplit("/", 1)[-1])
        assert preview["email"] == CLIENT_EMAIL
        assert preview["product_type"] == PRODUCT
        assert preview["company_name"] == "Grey Consortium UK"
        assert preview["claimable"] is True

    def test_preview_reveals_nothing_beyond_the_invitation(self):
        db = FakeDB()
        svc = _service(db)
        _invite, url = _issue(svc, admin_notes="Internal: discount agreed verbally")
        preview = svc.preview(url.rsplit("/", 1)[-1])
        assert "admin_notes" not in preview
        assert "token_hash" not in preview
        assert "id" not in preview

    def test_preview_of_an_expired_invite_is_not_claimable(self):
        db = FakeDB()
        svc = _service(db)
        _invite, url = _issue(svc)
        db.invites[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(days=1)
        preview = svc.preview(url.rsplit("/", 1)[-1])
        assert preview["status"] == STATUS_EXPIRED
        assert preview["claimable"] is False

    def test_preview_of_an_unknown_token_is_not_found(self):
        with pytest.raises(InviteNotFound):
            _service().preview("nope")


# ── Routes ──────────────────────────────────────────────────────────────────


def _admin() -> AdminUser:
    return AdminUser(id="admin-1", email="admin@example.com", role="master_admin")


def _wire_admin(client, db):
    from app.modules.b2b.admin_router import get_invite_service as admin_invite_dep
    from app.modules.b2b.admin_router import get_b2b_service as admin_b2b_dep

    svc = _service(db)
    client.app.dependency_overrides[get_current_admin] = _admin
    client.app.dependency_overrides[get_supabase] = lambda: db
    client.app.dependency_overrides[admin_invite_dep] = lambda: svc
    client.app.dependency_overrides[admin_b2b_dep] = lambda: _b2b(db)
    return svc


def _wire_client(client, db, email=CLIENT_EMAIL):
    from app.modules.b2b.router import get_b2b_service as client_b2b_dep
    from app.modules.b2b.router import get_invite_service as client_invite_dep

    svc = _service(db)
    client.app.dependency_overrides[get_supabase] = lambda: db
    client.app.dependency_overrides[client_invite_dep] = lambda: svc
    client.app.dependency_overrides[client_b2b_dep] = lambda: _b2b(db)
    client.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-9", email=email, name="Buyer", company="Grey Consortium UK",
        role="Buyer", user_type="paid", credits_remaining=0, plan="professional",
    )
    return svc


HEADERS = {"Authorization": "Bearer token"}


def test_admin_can_issue_an_invite(client):
    db = FakeDB()
    _wire_admin(client, db)
    response = client.post(
        "/api/admin/b2b/invites",
        json={"email": CLIENT_EMAIL, "product_type": PRODUCT, "company_name": "Grey"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invite"]["email"] == CLIENT_EMAIL
    assert body["accept_url"].startswith("https://app.prodculator.com/b2b/invite/")
    # The token exists once, in this response and the email — never in the row.
    assert "token_hash" not in body["invite"]


def test_admin_issue_refuses_an_unknown_product(client):
    _wire_admin(client, FakeDB())
    response = client.post(
        "/api/admin/b2b/invites",
        json={"email": CLIENT_EMAIL, "product_type": "camera_equipment"},
        headers=HEADERS,
    )
    assert response.status_code == 200  # sanity: a known product is accepted

    response2 = client.post(
        "/api/admin/b2b/invites",
        json={"email": "other@example.com", "product_type": "not_a_product"},
        headers=HEADERS,
    )
    assert response2.status_code == 422  # rejected by the schema's Literal


def test_admin_issue_conflicts_on_a_duplicate(client):
    db = FakeDB()
    _wire_admin(client, db)
    payload = {"email": CLIENT_EMAIL, "product_type": PRODUCT}
    assert client.post("/api/admin/b2b/invites", json=payload, headers=HEADERS).status_code == 200
    second = client.post("/api/admin/b2b/invites", json=payload, headers=HEADERS)
    assert second.status_code == 409


def test_admin_can_list_filter_resend_and_revoke(client):
    db = FakeDB()
    _wire_admin(client, db)
    issued = client.post(
        "/api/admin/b2b/invites",
        json={"email": CLIENT_EMAIL, "product_type": PRODUCT},
        headers=HEADERS,
    ).json()
    invite_id = issued["invite"]["id"]

    listing = client.get("/api/admin/b2b/invites?status=pending", headers=HEADERS)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    resent = client.post(f"/api/admin/b2b/invites/{invite_id}/resend", headers=HEADERS)
    assert resent.status_code == 200
    assert resent.json()["accept_url"] != issued["accept_url"]

    revoked = client.post(f"/api/admin/b2b/invites/{invite_id}/revoke", headers=HEADERS)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"


def test_admin_listing_rejects_an_unknown_status(client):
    _wire_admin(client, FakeDB())
    response = client.get("/api/admin/b2b/invites?status=maybe", headers=HEADERS)
    assert response.status_code == 400


def test_admin_resend_and_revoke_404_on_an_unknown_invite(client):
    _wire_admin(client, FakeDB())
    assert client.post("/api/admin/b2b/invites/nope/resend", headers=HEADERS).status_code == 404
    assert client.post("/api/admin/b2b/invites/nope/revoke", headers=HEADERS).status_code == 404


def test_the_public_accept_page_can_read_the_invite(client):
    db = FakeDB()
    svc = _wire_client(client, db)
    _invite, url = _issue(svc)
    token = url.rsplit("/", 1)[-1]

    # No Authorization header: the client may not have an account yet.
    response = client.get(f"/api/b2b/invites/{token}")
    assert response.status_code == 200
    body = response.json()
    assert body["product_type"] == PRODUCT
    assert body["claimable"] is True


def test_the_public_accept_page_404s_on_a_bad_token(client):
    _wire_client(client, FakeDB())
    assert client.get("/api/b2b/invites/nonsense").status_code == 404


def test_claiming_over_the_api_creates_the_subscription(client):
    db = FakeDB()
    svc = _wire_client(client, db)
    _invite, url = _issue(svc)
    token = url.rsplit("/", 1)[-1]

    response = client.post(f"/api/b2b/invites/{token}/accept", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["product_type"] == PRODUCT
    assert body["source"] == "manual_contract"
    assert db.invites[0]["status"] == "accepted"
    assert db.invites[0]["b2b_subscription_id"] == body["id"]


def test_claiming_with_the_wrong_account_403s_and_names_the_address(client):
    db = FakeDB()
    svc = _wire_client(client, db, email="someone.else@example.com")
    _invite, url = _issue(svc)

    response = client.post(
        f"/api/b2b/invites/{url.rsplit('/', 1)[-1]}/accept", headers=HEADERS,
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["reason"] == "email_mismatch"
    assert detail["invited_email"] == CLIENT_EMAIL
    assert db.subscriptions == []


def test_claiming_a_revoked_invite_409s_with_the_reason(client):
    db = FakeDB()
    svc = _wire_client(client, db)
    _invite, url = _issue(svc)
    svc.revoke(db.invites[0]["id"])

    response = client.post(
        f"/api/b2b/invites/{url.rsplit('/', 1)[-1]}/accept", headers=HEADERS,
    )
    assert response.status_code == 409
    # The accept page needs to distinguish expired / revoked / already used.
    assert response.json()["detail"]["reason"] == "revoked"


def test_claiming_requires_authentication(client):
    db = FakeDB()
    svc = _service(db)
    _invite, url = _issue(svc)
    from app.modules.b2b.router import get_invite_service as client_invite_dep

    client.app.dependency_overrides.clear()
    client.app.dependency_overrides[client_invite_dep] = lambda: svc

    response = client.post(f"/api/b2b/invites/{url.rsplit('/', 1)[-1]}/accept")
    assert response.status_code in (401, 403)
    assert db.subscriptions == []


def test_invite_endpoints_are_audited(client):
    """Admin invite mutations are admin mutations: they must be in the trail."""
    from app.core.audit import AuditedAPIRoute
    from tests.test_admin_audit import _api_routes

    invite_routes = [
        r for r in _api_routes(client.app)
        if r.path.startswith("/api/admin/b2b/invites")
    ]
    assert invite_routes
    assert all(isinstance(r, AuditedAPIRoute) for r in invite_routes)


def test_the_invite_email_template_renders():
    from app.modules.email.service import EmailService

    subject, html = EmailService(_settings()).render(
        "b2b_contract_invite",
        {
            "product_title": "Camera Equipment Intelligence",
            "company_name": "Grey Consortium UK",
            "delivery_frequency": "monthly",
            "accept_url": "https://app.prodculator.com/b2b/invite/abc",
            "expires_at": "2026-09-05",
        },
    )
    assert subject
    assert "https://app.prodculator.com/b2b/invite/abc" in html
    assert "Grey Consortium UK" in html
