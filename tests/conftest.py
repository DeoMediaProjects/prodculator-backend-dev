from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.auth.schemas import AuthUser


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
