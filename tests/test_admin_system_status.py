from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser
from tests.admin_fakes import FakeSupabase

HEADERS = {"Authorization": "Bearer token"}
PATH = "/api/admin/system-status"


class _Redis:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def ping(self):
        if self.fail:
            raise ConnectionError("no redis")
        return True


class _Storage:
    def __init__(self, result=(True, "Bucket reachable"), exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc

    def preflight(self):
        if self.exc:
            raise self.exc
        return self.result


class _FakeSupabaseWithStorage(FakeSupabase):
    def __init__(self, storage: _Storage) -> None:
        super().__init__({"users": [{"id": "u1"}]})
        self.storage = storage


class _BrokenDb(_FakeSupabaseWithStorage):
    def table(self, name):
        raise RuntimeError("db down")


def _settings(**overrides):
    base = {
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "GEMINI_API_KEY": "",
        "LLM_FALLBACK_PROVIDERS": "",
        "STRIPE_SECRET_KEY": "",
        "BREVO_API_KEY": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _setup(client, monkeypatch, *, role="support_admin", supabase=None, redis=None, settings=None):
    fake = supabase or _FakeSupabaseWithStorage(_Storage())
    client.app.dependency_overrides[get_current_admin] = lambda: AdminUser(
        id="admin-1", email="admin@example.com", name="Admin", role=role
    )
    client.app.dependency_overrides[get_supabase] = lambda: fake
    client.app.dependency_overrides[get_settings] = lambda: settings or _settings()
    monkeypatch.setattr("app.core.cache.get_redis", lambda: redis or _Redis())
    return fake


def _by_name(response):
    return {s["name"]: s for s in response.json()["services"]}


def test_non_admin_is_refused(client, monkeypatch):
    _setup(client, monkeypatch)

    def _not_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    client.app.dependency_overrides[get_current_admin] = _not_admin
    response = client.get(PATH, headers=HEADERS)
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["support_admin", "data_admin", "master_admin"])
def test_any_admin_role_gets_a_well_formed_status(client, monkeypatch, role):
    _setup(client, monkeypatch, role=role)
    response = client.get(PATH, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["checked_at"]
    names = [s["name"] for s in data["services"]]
    assert names == [
        "Primary Database",
        "Redis Cache",
        "Report Storage",
        "Script Analysis (Claude)",
        "Stripe Payments",
        "Brevo Email Delivery",
    ]
    for service in data["services"]:
        assert service["check"] in {"live", "configuration"}
        if service["check"] == "live":
            assert service["last_checked"] == data["checked_at"]
        else:
            assert service["last_checked"] is None


def test_healthy_dependencies_report_operational(client, monkeypatch):
    _setup(client, monkeypatch)
    services = _by_name(client.get(PATH, headers=HEADERS))
    assert services["Primary Database"]["status"] == "operational"
    assert services["Redis Cache"]["status"] == "operational"
    assert services["Report Storage"]["status"] == "operational"
    assert services["Report Storage"]["detail"] == "Bucket reachable"


def test_unreachable_dependencies_are_reported_not_raised(client, monkeypatch):
    _setup(
        client,
        monkeypatch,
        supabase=_BrokenDb(_Storage(exc=RuntimeError("s3 down"))),
        redis=_Redis(fail=True),
    )
    response = client.get(PATH, headers=HEADERS)
    assert response.status_code == 200
    services = _by_name(response)
    assert services["Primary Database"]["status"] == "down"
    assert services["Redis Cache"]["status"] == "degraded"
    assert "ConnectionError" in services["Redis Cache"]["detail"]
    assert services["Report Storage"]["status"] == "down"
    assert "RuntimeError" in services["Report Storage"]["detail"]


def test_failed_storage_preflight_is_down_with_its_detail(client, monkeypatch):
    _setup(client, monkeypatch, supabase=_FakeSupabaseWithStorage(_Storage(result=(False, "Bucket missing"))))
    services = _by_name(client.get(PATH, headers=HEADERS))
    assert services["Report Storage"]["status"] == "down"
    assert services["Report Storage"]["detail"] == "Bucket missing"


def test_missing_credentials_read_as_not_configured(client, monkeypatch):
    _setup(client, monkeypatch)
    services = _by_name(client.get(PATH, headers=HEADERS))
    for name in ("Script Analysis (Claude)", "Stripe Payments", "Brevo Email Delivery"):
        assert services[name]["status"] == "not_configured"
        assert services[name]["check"] == "configuration"


def test_present_credentials_read_as_configured_with_fallback_chain(client, monkeypatch):
    _setup(
        client,
        monkeypatch,
        settings=_settings(
            ANTHROPIC_API_KEY="sk-ant",
            OPENAI_API_KEY="sk-oai",
            LLM_FALLBACK_PROVIDERS="openai, gemini",
            STRIPE_SECRET_KEY="sk_test",
            BREVO_API_KEY="xkeysib",
        ),
    )
    services = _by_name(client.get(PATH, headers=HEADERS))
    ai = services["Script Analysis (Claude)"]
    assert ai["status"] == "configured"
    assert ai["detail"] == "Anthropic key present; fallbacks: openai"
    assert services["Stripe Payments"]["status"] == "configured"
    assert services["Brevo Email Delivery"]["status"] == "configured"


def test_fallback_provider_alone_counts_as_configured(client, monkeypatch):
    _setup(
        client,
        monkeypatch,
        settings=_settings(GEMINI_API_KEY="g-key", LLM_FALLBACK_PROVIDERS="gemini"),
    )
    ai = _by_name(client.get(PATH, headers=HEADERS))["Script Analysis (Claude)"]
    assert ai["status"] == "configured"
    assert ai["detail"].startswith("ANTHROPIC_API_KEY is not set")
    assert "fallbacks: gemini" in ai["detail"]
