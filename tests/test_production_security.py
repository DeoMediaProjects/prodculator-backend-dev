import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.storage import _LocalStorageBucket


def _safe_production_settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "production",
        "DEBUG": False,
        "JWT_SECRET_KEY": "x" * 64,
        "FRONTEND_URL": "https://app.prodculator.example",
        "BACKEND_URL": "https://api.prodculator.example",
        "CORS_ORIGINS": ["https://app.prodculator.example"],
        "DB_URL": "postgresql+psycopg2://app:secret@db/prodculator",
        "AUTO_CREATE_DB_SCHEMA": False,
        "AUTH_COOKIE_ENABLED": True,
        "AUTH_COOKIE_SECURE": True,
        "RATE_LIMIT_ENABLED": True,
        "RATE_LIMIT_STORAGE_URI": "redis://redis:6379/0",
        "AWS_S3_BUCKET_NAME": "prodculator-reports",
        "AWS_ACCESS_KEY_ID": "test-access-key",
        "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_safe_production_configuration_is_accepted():
    settings = _safe_production_settings()
    assert settings.is_production is True
    assert settings.trusted_hosts == ["api.prodculator.example"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"DEBUG": True}, "DEBUG must be false"),
        ({"JWT_SECRET_KEY": "short-secret-that-is-at-least-32!!"}, "at least 48"),
        ({"AUTH_COOKIE_SECURE": False}, "secure cookie authentication"),
        ({"AUTO_CREATE_DB_SCHEMA": True}, "Alembic migrations"),
        ({"DB_URL": "sqlite:///prod.db"}, "not SQLite"),
        ({"RATE_LIMIT_ENABLED": False}, "rate limiting"),
        ({"FRONTEND_URL": "http://app.example"}, "FRONTEND_URL"),
        ({"CORS_ORIGINS": ["*"]}, "unsafe CORS origin"),
        ({"ALLOWED_HOSTS": ["*"]}, "ALLOWED_HOSTS"),
        ({"AWS_S3_BUCKET_NAME": ""}, "durable S3 storage"),
    ],
)
def test_unsafe_production_configuration_fails_closed(override, message):
    with pytest.raises(ValidationError, match=message):
        _safe_production_settings(**override)


def test_host_header_allowlist_rejects_untrusted_host(client):
    response = client.get("/api/health", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_untrusted_request_id_is_replaced(client):
    supplied = "x" * 129
    response = client.get("/api/health", headers={"X-Request-ID": supplied})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != supplied
    assert len(response.headers["X-Request-ID"]) == 32


def test_oversized_request_is_rejected_before_body_parsing(client):
    response = client.post(
        "/api/contact",
        headers={"Content-Length": str(55 * 1024 * 1024 + 1)},
        content=b"{}",
    )
    assert response.status_code == 413


def test_local_storage_rejects_sibling_prefix_traversal(tmp_path):
    settings = Settings(
        _env_file=None,
        JWT_SECRET_KEY="x" * 64,
        STORAGE_ROOT=str(tmp_path / "storage"),
    )
    bucket = _LocalStorageBucket("reports", settings)

    with pytest.raises(ValueError, match="Invalid storage path"):
        bucket._safe_path("../reports-evil/stolen.pdf")
