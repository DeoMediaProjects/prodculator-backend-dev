"""The admin fixes made before the portal went to production.

Each test names a way the portal could be abused before the fix: a support
admin reading password hashes, a revoked refresh token minting new sessions,
a low-privilege role blocking paying users or approving rebate figures, a
stale cookie locking an admin out of sign-in, and a block that waited out the
profile cache.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.auth_cookies import ACCESS_COOKIE
from app.core.config import get_settings
from app.core.dependencies import get_current_admin, get_supabase
from app.core.security import _BLOCKLIST_PREFIX, create_refresh_token, decode_token
from app.modules.admin.auth_router import get_auth_service as get_admin_auth_service
from app.modules.admin.schemas import AdminUser
from app.modules.auth.service import AuthService
from tests.admin_fakes import FakeSupabase

HEADERS = {"Authorization": "Bearer token"}


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    # Subscriber actions drop the cached profile in Redis. Keep that off the
    # network; the tests that care about it install their own fake.
    import app.modules.subscribers.service as subscriber_service

    monkeypatch.setattr(subscriber_service.sync_redis, "from_url", lambda *a, **k: _RecordingRedis())


def _as(role: str):
    return lambda: AdminUser(id="admin-1", email="admin@example.com", role=role)


def _users_store() -> FakeSupabase:
    return FakeSupabase(
        {
            "users": [
                {
                    "id": "u1",
                    "email": "alice@example.com",
                    "name": "Alice",
                    "password_hash": "$argon2id$v=19$secret",
                    "google_uid": "google-123",
                    "credits_remaining": 1,
                    "created_at": "2026-01-10T00:00:00Z",
                },
            ],
            "subscriptions": [],
        }
    )


# ── GET /api/admin/users ─────────────────────────────────────────────────────


def test_the_user_list_never_carries_credentials(client):
    client.app.dependency_overrides[get_current_admin] = _as("support_admin")
    client.app.dependency_overrides[get_supabase] = _users_store

    response = client.get("/api/admin/users", headers=HEADERS)

    assert response.status_code == 200
    [row] = response.json()["items"]
    assert row["email"] == "alice@example.com"
    assert "password_hash" not in row
    assert "google_uid" not in row


# ── subscriber actions ───────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["support_admin", "data_admin", ""])
@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/admin/subscribers/u1/block", None),
        ("/api/admin/subscribers/u1/unblock", None),
        ("/api/admin/subscribers/u1/credit", {"adjustment": 5}),
    ],
)
def test_a_role_without_the_permission_cannot_act_on_a_subscriber(client, role, path, body):
    store = _users_store()
    client.app.dependency_overrides[get_current_admin] = _as(role)
    client.app.dependency_overrides[get_supabase] = lambda: store

    response = client.post(path, headers=HEADERS, json=body)

    assert response.status_code == 403
    user = store.store["users"][0]
    assert user["credits_remaining"] == 1
    assert not user.get("is_blocked")


@pytest.mark.parametrize("role", ["master_admin", "senior_admin"])
def test_a_senior_role_can_still_act_on_a_subscriber(client, role):
    store = _users_store()
    client.app.dependency_overrides[get_current_admin] = _as(role)
    client.app.dependency_overrides[get_supabase] = lambda: store

    assert client.post("/api/admin/subscribers/u1/block", headers=HEADERS).status_code == 200
    credit = client.post(
        "/api/admin/subscribers/u1/credit", headers=HEADERS, json={"adjustment": 5}
    )
    assert credit.status_code == 200
    assert credit.json()["credits_remaining"] == 6


def test_support_can_still_read_the_subscriber_list(client):
    client.app.dependency_overrides[get_current_admin] = _as("support_admin")
    client.app.dependency_overrides[get_supabase] = _users_store

    assert client.get("/api/admin/subscribers", headers=HEADERS).status_code == 200


@pytest.mark.parametrize("adjustment", [101, -101, 10_000_000])
def test_one_credit_adjustment_cannot_exceed_the_cap(client, adjustment):
    store = _users_store()
    client.app.dependency_overrides[get_current_admin] = _as("master_admin")
    client.app.dependency_overrides[get_supabase] = lambda: store

    response = client.post(
        "/api/admin/subscribers/u1/credit", headers=HEADERS, json={"adjustment": adjustment}
    )

    assert response.status_code == 422
    assert store.store["users"][0]["credits_remaining"] == 1


# ── calculation approval ─────────────────────────────────────────────────────


def test_a_support_admin_cannot_approve_a_rebate_calculation(client):
    client.app.dependency_overrides[get_current_admin] = _as("support_admin")
    client.app.dependency_overrides[get_supabase] = lambda: MagicMock()

    response = client.post(
        "/api/admin/calculation-approval/GB_AVEC",
        headers=HEADERS,
        json={"status": "ready", "note": "Looks fine.", "force": True},
    )

    assert response.status_code == 403


# ── refresh-token revocation ─────────────────────────────────────────────────


class _Redis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, _ttl, value):
        self.store[key] = value


class _BrokenRedis:
    async def get(self, key):
        raise ConnectionError("redis down")

    async def setex(self, key, _ttl, value):
        raise ConnectionError("redis down")


def _service_refreshing(token: str) -> AuthService:
    """An AuthService whose token layer decodes ``token`` as the real one does."""
    settings = get_settings()
    supabase = MagicMock()
    supabase.settings = settings
    session = SimpleNamespace(access_token="new-access", refresh_token="new-refresh", expires_in=3600)
    response = SimpleNamespace(
        session=session,
        claims=decode_token(token, settings),
        user=SimpleNamespace(id="admin-1", email="admin@example.com"),
    )
    supabase.auth.refresh_admin_session.return_value = response
    supabase.auth.refresh_session.return_value = response
    return AuthService(supabase)


def _revoke(redis: _Redis, token: str) -> None:
    jti = decode_token(token, get_settings())["jti"]
    redis.store[f"{_BLOCKLIST_PREFIX}{jti}"] = "1"


@pytest.mark.parametrize("method", ["admin_refresh_session", "refresh_session"])
def test_a_revoked_refresh_token_cannot_mint_a_new_session(method):
    token = create_refresh_token("admin-1", "admin", get_settings())
    redis = _Redis()
    _revoke(redis, token)
    service = _service_refreshing(token)

    with pytest.raises(ValueError, match="revoked"):
        asyncio.run(getattr(service, method)(token, redis_client=redis))


def test_a_rotated_refresh_token_cannot_be_replayed():
    token = create_refresh_token("admin-1", "admin", get_settings())
    redis = _Redis()
    service = _service_refreshing(token)
    service.supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
        SimpleNamespace(data={"id": "admin-1", "email": "admin@example.com", "role": "master_admin"})
    )

    first = asyncio.run(service.admin_refresh_session(token, redis_client=redis))
    assert first.access_token == "new-access"

    with pytest.raises(ValueError, match="revoked"):
        asyncio.run(service.admin_refresh_session(token, redis_client=redis))


def test_a_redis_outage_does_not_lock_every_admin_out():
    token = create_refresh_token("admin-1", "admin", get_settings())
    service = _service_refreshing(token)
    service.supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = (
        SimpleNamespace(data={"id": "admin-1", "email": "admin@example.com", "role": "master_admin"})
    )

    result = asyncio.run(service.admin_refresh_session(token, redis_client=_BrokenRedis()))

    assert result.access_token == "new-access"


@pytest.mark.parametrize("method, lookup", [("sign_out_admin", "get_admin"), ("sign_out", "get_user")])
def test_signing_out_revokes_the_refresh_token_too(method, lookup):
    settings = get_settings()
    refresh = create_refresh_token("admin-1", "admin", settings)
    access = create_refresh_token("admin-1", "admin", settings)  # any signed jti will do
    supabase = MagicMock()
    supabase.settings = settings
    getattr(supabase.auth, lookup).return_value = SimpleNamespace(user=SimpleNamespace(id="admin-1"))
    redis = _Redis()

    asyncio.run(getattr(AuthService(supabase), method)(access, redis_client=redis, refresh_token=refresh))

    refresh_jti = decode_token(refresh, settings)["jti"]
    assert f"{_BLOCKLIST_PREFIX}{refresh_jti}" in redis.store


def test_signing_out_with_an_unreadable_refresh_cookie_still_succeeds():
    settings = get_settings()
    access = create_refresh_token("admin-1", "admin", settings)
    supabase = MagicMock()
    supabase.settings = settings
    supabase.auth.get_admin.return_value = SimpleNamespace(user=SimpleNamespace(id="admin-1"))

    asyncio.run(
        AuthService(supabase).sign_out_admin(access, redis_client=_Redis(), refresh_token="garbage")
    )


# ── CSRF on admin sign-in ────────────────────────────────────────────────────


class _RefusingAdminAuth:
    def admin_sign_in(self, **_kwargs):
        raise ValueError("Invalid email or password")

    async def admin_refresh_session(self, **_kwargs):
        raise ValueError("Invalid refresh token")


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/admin/auth/signin", {"email": "admin@example.com", "password": "password123"}),
        ("/api/admin/auth/refresh", {"refresh_token": "anything"}),
    ],
)
def test_a_stale_access_cookie_does_not_block_admin_sign_in(client, path, body):
    # Without the exemption this is a 403 from the CSRF guard before the route
    # runs; reaching the route's own 401 proves the guard let it through.
    client.app.dependency_overrides[get_admin_auth_service] = lambda: _RefusingAdminAuth()
    client.cookies.set(ACCESS_COOKIE, "stale")

    response = client.post(path, json=body)

    assert response.status_code == 401


# ── profile cache after a subscriber action ──────────────────────────────────


class _RecordingRedis:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, key):
        self.deleted.append(key)

    def close(self):
        pass


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/admin/subscribers/u1/block", None),
        ("/api/admin/subscribers/u1/unblock", None),
        ("/api/admin/subscribers/u1/credit", {"adjustment": 5}),
    ],
)
def test_a_subscriber_action_drops_the_cached_profile(client, monkeypatch, path, body):
    # get_current_user serves the cached profile before it checks is_blocked,
    # so a block that leaves the cache in place does nothing for five minutes.
    import app.modules.subscribers.service as subscriber_service

    redis = _RecordingRedis()
    monkeypatch.setattr(subscriber_service.sync_redis, "from_url", lambda *a, **k: redis)
    store = _users_store()
    client.app.dependency_overrides[get_current_admin] = _as("senior_admin")
    client.app.dependency_overrides[get_supabase] = lambda: store

    assert client.post(path, headers=HEADERS, json=body).status_code == 200
    assert redis.deleted == ["user_profile:u1"]


def test_a_block_still_lands_when_redis_is_down(client, monkeypatch):
    import app.modules.subscribers.service as subscriber_service

    def unreachable(*_a, **_k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(subscriber_service.sync_redis, "from_url", unreachable)
    store = _users_store()
    client.app.dependency_overrides[get_current_admin] = _as("senior_admin")
    client.app.dependency_overrides[get_supabase] = lambda: store

    assert client.post("/api/admin/subscribers/u1/block", headers=HEADERS).status_code == 200
    assert store.store["users"][0]["is_blocked"] is True
