from collections.abc import Iterator
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Keep route tests independent from a developer's local .env. Several app
# modules initialize settings and DB engines at import time, so these must be
# set before importing app.main.
TEST_DB = Path(
    os.environ.get(
        "PRODCULATOR_TEST_DB",
        f"/private/tmp/prodculator_backend_tests_{os.getpid()}.db",
    )
)
TEST_DB.unlink(missing_ok=True)

_APP_IMPORT_ENV = {
    "APP_ENV": "test",
    "DEBUG": "false",
    "DB_URL": f"sqlite:///{TEST_DB}",
    "AUTO_CREATE_DB_SCHEMA": "true",
    "SCRAPER_ENABLED": "false",
    "SCHEDULER_ENABLED": "false",
    # Report generation runs in-process (BackgroundTasks) under tests — no RQ
    # worker or Redis required.
    "REPORT_QUEUE_ENABLED": "false",
    "STRIPE_SECRET_KEY": "",
    "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-chars",
}
_PREVIOUS_ENV = {key: os.environ.get(key) for key in _APP_IMPORT_ENV}
os.environ.update(_APP_IMPORT_ENV)

from app.main import app
from app.modules.auth.schemas import AuthUser

for key, value in _PREVIOUS_ENV.items():
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_user() -> AuthUser:
    return AuthUser(
        id="user-1",
        email="user@example.com",
        name="User",
        company="Acme",
        role="Producer",
        user_type="free",
        credits_remaining=1,
        plan="free",
    )
