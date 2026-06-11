"""Unit tests for the Brevo-backed EmailService.send() request building."""
import base64

import httpx
import pytest

from app.core.config import Settings
from app.modules.email import service as email_module
from app.modules.email.service import BREVO_SEND_URL, EmailService


class _FakeResponse:
    def __init__(self, status_code: int = 201):
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        JWT_SECRET_KEY="x" * 64,
        BREVO_API_KEY="xkeysib-test",
        BREVO_FROM_EMAIL="noreply@prodculator.com",
        BREVO_FROM_NAME="Prodculator",
    )
    base.update(overrides)
    return Settings(**base)


def _patch_post(monkeypatch) -> dict:
    captured: dict = {}

    def fake_post(url, *, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _FakeResponse(201)

    monkeypatch.setattr(email_module.httpx, "post", fake_post)
    return captured


def test_send_posts_to_brevo_with_expected_payload(monkeypatch):
    captured = _patch_post(monkeypatch)
    EmailService(_settings()).send("user@example.com", "welcome", {"name": "Ada"})

    assert captured["url"] == BREVO_SEND_URL
    assert captured["headers"]["api-key"] == "xkeysib-test"
    body = captured["json"]
    assert body["sender"] == {"email": "noreply@prodculator.com", "name": "Prodculator"}
    assert body["to"] == [{"email": "user@example.com"}]
    assert body["subject"]  # subject resolved from EMAIL_SUBJECTS
    assert "<" in body["htmlContent"]  # rendered HTML
    assert "attachment" not in body


def test_send_maps_attachments_to_brevo_shape(monkeypatch):
    captured = _patch_post(monkeypatch)
    content = base64.b64encode(b"hello").decode()
    EmailService(_settings()).send(
        "user@example.com",
        "report_ready",
        {"report_id": "r1"},
        attachments=[{"content": content, "filename": "report.pdf", "type": "application/pdf"}],
    )

    assert captured["json"]["attachment"] == [{"content": content, "name": "report.pdf"}]


def test_send_skips_when_api_key_missing(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("httpx.post should not be called without an API key")

    monkeypatch.setattr(email_module.httpx, "post", boom)
    # Must not raise — send is a no-op when Brevo is not configured.
    EmailService(_settings(BREVO_API_KEY="")).send("user@example.com", "welcome", {})


def test_send_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        email_module.httpx, "post", lambda *a, **k: _FakeResponse(400)
    )
    with pytest.raises(httpx.HTTPStatusError):
        EmailService(_settings()).send("user@example.com", "welcome", {})
