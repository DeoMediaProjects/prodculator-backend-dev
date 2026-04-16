"""Tests for the RequirePlan FastAPI dependency."""

import pytest
from fastapi import HTTPException

from app.core.dependencies import RequirePlan
from app.modules.auth.schemas import AuthUser


def _make_user(plan: str = "free", **kwargs) -> AuthUser:
    defaults = dict(
        id="user-1",
        email="user@example.com",
        user_type="paid" if plan != "free" else "free",
        credits_remaining=0,
        plan=plan,
    )
    defaults.update(kwargs)
    return AuthUser(**defaults)


@pytest.mark.asyncio
async def test_free_user_blocked_from_professional():
    dep = RequirePlan("professional")
    user = _make_user(plan="free")
    with pytest.raises(HTTPException) as exc_info:
        await dep(user)
    assert exc_info.value.status_code == 403
    assert "professional" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_professional_user_passes_professional_gate():
    dep = RequirePlan("professional")
    user = _make_user(plan="professional")
    result = await dep(user)
    assert result.plan == "professional"


@pytest.mark.asyncio
async def test_studio_user_passes_professional_gate():
    dep = RequirePlan("professional")
    user = _make_user(plan="studio")
    result = await dep(user)
    assert result.plan == "studio"


@pytest.mark.asyncio
async def test_legacy_single_passes_professional_gate():
    """Legacy 'single' plan should be treated as professional."""
    dep = RequirePlan("professional")
    user = _make_user(plan="single")
    result = await dep(user)
    assert result.plan == "single"


@pytest.mark.asyncio
async def test_free_user_passes_free_gate():
    dep = RequirePlan("free")
    user = _make_user(plan="free")
    result = await dep(user)
    assert result.plan == "free"


@pytest.mark.asyncio
async def test_professional_user_blocked_from_studio():
    dep = RequirePlan("studio")
    user = _make_user(plan="professional")
    with pytest.raises(HTTPException) as exc_info:
        await dep(user)
    assert exc_info.value.status_code == 403
    assert "studio" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_studio_user_passes_studio_gate():
    dep = RequirePlan("studio")
    user = _make_user(plan="studio")
    result = await dep(user)
    assert result.plan == "studio"


@pytest.mark.asyncio
async def test_error_message_includes_user_plan():
    dep = RequirePlan("professional")
    user = _make_user(plan="free")
    with pytest.raises(HTTPException) as exc_info:
        await dep(user)
    assert "free" in exc_info.value.detail
    assert "professional" in exc_info.value.detail
