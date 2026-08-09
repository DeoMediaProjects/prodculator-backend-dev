"""Password change and password reset, end to end against the real hash column.

Two defects sat behind these flows. The Account page's Change button navigated to
/reset-password, the page that consumes a token from a reset email, so a signed-in
user filled in the form and was told the link had expired; the endpoint built for the
job had no caller at all. And that endpoint changed the password on the strength of
the access token alone, so anyone with a live session could lock the owner out
without knowing the current password.

These tests exercise the service against the real users.password_hash column rather
than a mock, because the failure that matters is "the stored hash did not change".
"""
from __future__ import annotations

import uuid

import pytest

from app.core.database_client import hash_password, verify_password
from app.core.security import create_password_reset_token, create_verification_token

OLD = "OldPassword123!"
NEW = "BrandNewPassword456!"


class FakeTable:
    """Minimal stand-in for the query-builder surface the service uses."""

    def __init__(self, store: dict):
        self.store = store
        self._id = None

    def select(self, *_):
        return self

    def eq(self, _field, value):
        self._id = value
        return self

    def single(self):
        return self

    def execute(self):
        return type("R", (), {"data": self.store.get(self._id)})()


class FakeAdmin:
    def __init__(self, store: dict):
        self.store = store

    def update_user_by_id(self, user_id, payload):
        # Mirrors the real _AdminAuth: a password write becomes a hash write.
        if "password" in payload:
            self.store[user_id]["password_hash"] = hash_password(payload["password"])


class FakeAuth:
    def __init__(self, store: dict, signed_in_as: str | None):
        self.admin = FakeAdmin(store)
        self._user = signed_in_as

    def get_user(self, _token):
        if not self._user:
            return None
        return type("R", (), {"user": type("U", (), {"id": self._user})()})()


class FakeClient:
    def __init__(self, store: dict, signed_in_as: str | None = None):
        self.store = store
        self.auth = FakeAuth(store, signed_in_as)

    def table(self, _name):
        return FakeTable(self.store)


@pytest.fixture
def account():
    uid = str(uuid.uuid4())
    return uid, {uid: {"id": uid, "email": "a@b.com", "password_hash": hash_password(OLD)}}


def service(store, signed_in_as=None):
    from app.modules.auth.service import AuthService

    return AuthService(FakeClient(store, signed_in_as))


# ── Change password, signed in ───────────────────────────────────────────────

class TestChangePassword:
    def test_the_stored_hash_actually_changes(self, account):
        uid, store = account
        service(store, uid).update_password(token="tok", new_password=NEW, current_password=OLD)
        stored = store[uid]["password_hash"]
        assert verify_password(NEW, stored)
        assert not verify_password(OLD, stored)

    def test_a_wrong_current_password_is_refused(self, account):
        """A live session must not be enough on its own: changing the password is
        exactly what locks the real owner out."""
        uid, store = account
        before = store[uid]["password_hash"]
        with pytest.raises(ValueError, match="Current password is incorrect"):
            service(store, uid).update_password(
                token="tok", new_password=NEW, current_password="not-it",
            )
        assert store[uid]["password_hash"] == before

    def test_an_empty_current_password_is_refused(self, account):
        uid, store = account
        with pytest.raises(ValueError):
            service(store, uid).update_password(token="tok", new_password=NEW, current_password="")

    def test_reusing_the_current_password_is_refused(self, account):
        uid, store = account
        with pytest.raises(ValueError, match="different from the current one"):
            service(store, uid).update_password(token="tok", new_password=OLD, current_password=OLD)

    def test_a_google_account_is_told_why_rather_than_given_a_password(self, account):
        """Setting one here would let whoever holds the session create a password
        login that did not exist before."""
        uid, store = account
        store[uid]["password_hash"] = None
        with pytest.raises(ValueError, match="signs in with Google"):
            service(store, uid).update_password(token="tok", new_password=NEW, current_password="x")
        assert store[uid]["password_hash"] is None

    def test_an_invalid_session_is_refused(self, account):
        _uid, store = account
        with pytest.raises(ValueError, match="Invalid or expired token"):
            service(store, None).update_password(
                token="bad", new_password=NEW, current_password=OLD,
            )

    def test_a_missing_user_row_is_refused(self, account):
        _uid, store = account
        with pytest.raises(ValueError, match="User not found"):
            service(store, str(uuid.uuid4())).update_password(
                token="tok", new_password=NEW, current_password=OLD,
            )


# ── Forgot password ──────────────────────────────────────────────────────────

class TestResetPassword:
    def test_a_valid_token_changes_the_stored_hash(self, account, monkeypatch):
        uid, store = account
        from app.core.config import get_settings

        token = create_password_reset_token(uid, "a@b.com", get_settings())
        service(store).confirm_password_reset(token=token, new_password=NEW)
        assert verify_password(NEW, store[uid]["password_hash"])
        assert not verify_password(OLD, store[uid]["password_hash"])

    @pytest.mark.parametrize("bad", ["", "garbage", "a.b.c"])
    def test_an_unreadable_token_is_refused(self, account, bad):
        _uid, store = account
        with pytest.raises(ValueError, match="invalid or has expired"):
            service(store).confirm_password_reset(token=bad, new_password=NEW)

    def test_a_token_of_the_wrong_type_is_refused(self, account):
        """A verification token is signed by the same key. Without the type check it
        would be accepted as a password reset."""
        uid, store = account
        from app.core.config import get_settings

        token = create_verification_token(uid, "a@b.com", get_settings())
        with pytest.raises(ValueError, match="Invalid reset token"):
            service(store).confirm_password_reset(token=token, new_password=NEW)
        assert verify_password(OLD, store[uid]["password_hash"])

    def test_a_token_for_an_unknown_user_is_refused(self, account):
        _uid, store = account
        from app.core.config import get_settings

        token = create_password_reset_token(str(uuid.uuid4()), "a@b.com", get_settings())
        with pytest.raises(ValueError, match="User not found"):
            service(store).confirm_password_reset(token=token, new_password=NEW)

    def test_the_token_carries_a_jti_so_it_can_be_spent(self):
        """Single use depends on this claim existing. It did, and nothing consumed it,
        so a reset link stayed replayable for its full hour."""
        from app.core.config import get_settings
        from app.core.security import decode_token

        token = create_password_reset_token(str(uuid.uuid4()), "a@b.com", get_settings())
        assert decode_token(token, get_settings()).get("jti")


class TestResetEmail:
    def test_no_email_is_sent_for_an_unregistered_address(self, monkeypatch):
        """Silence is the point: the endpoint must not reveal who has an account."""
        sent = []
        svc = service({})
        monkeypatch.setattr(svc.email_service, "send", lambda **kw: sent.append(kw))
        svc.reset_password(email="nobody@example.com", redirect_url="https://x.test")
        assert sent == []

    def test_no_email_is_sent_to_a_google_account(self, account, monkeypatch):
        uid, store = account
        store[uid]["password_hash"] = None
        store["a@b.com"] = store[uid]  # the lookup is by email
        sent = []
        svc = service(store)
        monkeypatch.setattr(svc.email_service, "send", lambda **kw: sent.append(kw))
        svc.reset_password(email="a@b.com", redirect_url="https://x.test")
        assert sent == []

    def test_the_link_points_at_the_reset_page_and_carries_a_token(self, account, monkeypatch):
        uid, store = account
        store["a@b.com"] = store[uid]
        sent = []
        svc = service(store)
        monkeypatch.setattr(svc.email_service, "send", lambda **kw: sent.append(kw))
        svc.reset_password(email="a@b.com", redirect_url="https://app.test")

        assert len(sent) == 1
        assert sent[0]["template_name"] == "reset_password"
        url = sent[0]["context"]["reset_url"]
        assert url.startswith("https://app.test/reset-password?token=")
        # The page reads ?token=; a link without one strands the user on a form that
        # can only fail.
        assert len(url.split("token=")[1]) > 20
