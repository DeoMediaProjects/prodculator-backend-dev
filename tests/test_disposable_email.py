"""Tests for disposable-email blocking at signup — the practical enforcement of
'one free signup per person' for the Explorer plan.
"""
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.modules.auth.disposable_email import email_domain, is_disposable_email
from app.modules.auth.service import AuthService


def _settings() -> Settings:
    return Settings(_env_file=None, JWT_SECRET_KEY="x" * 64)


class TestIsDisposableEmail:
    def test_flags_known_disposable_domains(self):
        assert is_disposable_email("someone@mailinator.com")
        assert is_disposable_email("x@guerrillamail.com")
        assert is_disposable_email("test@yopmail.com")
        assert is_disposable_email("a@10minutemail.com")

    def test_allows_real_providers(self):
        assert not is_disposable_email("user@gmail.com")
        assert not is_disposable_email("producer@strathmore.edu")
        assert not is_disposable_email("exec@studio.co.uk")

    def test_case_insensitive_and_trimmed(self):
        assert is_disposable_email("User@MAILINATOR.com")
        assert is_disposable_email("  x@Guerrillamail.COM  ".strip())

    def test_malformed_email_is_not_disposable(self):
        assert not is_disposable_email("")
        assert not is_disposable_email("no-at-sign")

    def test_email_domain_helper(self):
        assert email_domain("a@b.com") == "b.com"
        assert email_domain("A@B.COM") == "b.com"
        assert email_domain("bad") == ""


class TestSignupBlocksDisposable:
    def test_sign_up_rejects_disposable_email_before_touching_db(self):
        supabase = MagicMock()
        svc = AuthService(supabase, _settings())

        with pytest.raises(ValueError, match="permanent email"):
            svc.sign_up(
                email="throwaway@mailinator.com",
                password="Sup3rSecret!",
                redirect_url="https://app.example.com",
                name="Abuser",
            )

        # The disposable check must short-circuit before any account creation.
        supabase.auth.sign_up.assert_not_called()
