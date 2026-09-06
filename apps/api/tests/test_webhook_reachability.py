"""Tests for deciding whether to send callback URLs at all.

A callback URL pointing at localhost is worse than sending none. The
provider accepts it, tries to deliver, retries for eight minutes, and the
operator sees nothing that explains the silence. Detecting an
unreachable URL and omitting the callbacks turns a confusing failure into
a logged decision, and reconciliation returns the results either way.
"""

from __future__ import annotations

import pytest

from app.core.config import HunarMode, Settings


def settings_with(public_url: str) -> Settings:
    return Settings(
        hunar_mode=HunarMode.MOCK,
        hunar_api_key="test-key",
        openai_api_key="",
        database_url="sqlite+aiosqlite:///:memory:",
        public_api_base_url=public_url,
    )


class TestWebhookReachability:
    @pytest.mark.parametrize(
        "url",
        [
            "https://hunar-api.onrender.com",
            "https://my-tunnel.ngrok-free.app",
            "https://api.example.co.in",
            "https://1.2.3.4",
        ],
    )
    def test_public_https_urls_are_deliverable(self, url: str) -> None:
        assert settings_with(url).webhooks_deliverable is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://localhost",
            "https://localhost:8000",
            "https://127.0.0.1",
            "https://127.0.0.1:8000",
            "https://0.0.0.0",
            "https://macbook.local",
            "https://api.internal",
        ],
    )
    def test_loopback_and_private_names_are_not(self, url: str) -> None:
        """These are the values a developer ends up with by default."""
        assert settings_with(url).webhooks_deliverable is False

    @pytest.mark.parametrize(
        "url", ["http://example.com", "http://localhost:8000", "ftp://example.com"]
    )
    def test_non_https_is_not_deliverable(self, url: str) -> None:
        """Hunar refuses non-HTTPS callbacks outright."""
        assert settings_with(url).webhooks_deliverable is False

    def test_a_path_suffix_does_not_confuse_the_host_check(self) -> None:
        assert settings_with("https://api.example.com/v1/base").webhooks_deliverable is True
        assert settings_with("https://localhost:8000/v1").webhooks_deliverable is False
