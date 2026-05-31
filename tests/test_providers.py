"""Tests for agent/providers.py and auth/credentials.py credential resolution."""

from datetime import UTC, datetime, timedelta

from home_ops_agent.agent import providers
from home_ops_agent.auth.credentials import Credentials


def test_resolve_provider_anthropic():
    assert providers.resolve_provider("claude-sonnet-4-6") == providers.ANTHROPIC
    assert providers.resolve_provider("claude-opus-4-8") == providers.ANTHROPIC


def test_resolve_provider_kimi():
    assert providers.resolve_provider("kimi-for-coding") == providers.KIMI
    assert providers.resolve_provider("kimi-k2.6") == providers.KIMI


def test_resolve_provider_openai():
    assert providers.resolve_provider("gpt-5.5") == providers.OPENAI
    assert providers.resolve_provider("codex-5.3") == providers.OPENAI
    assert providers.resolve_provider("o3-mini") == providers.OPENAI


def test_resolve_provider_unknown_defaults_to_anthropic():
    assert providers.resolve_provider("something-weird") == providers.ANTHROPIC


def test_credentials_available_providers():
    creds = Credentials(anthropic_api_key="a", kimi_api_key="k", openai_access_token="t")
    assert creds.available_providers() == {"anthropic", "kimi", "openai"}
    assert creds.has_any() is True


def test_credentials_partial():
    creds = Credentials(kimi_api_key="k")
    assert creds.available_providers() == {"kimi"}
    assert creds.has_provider("kimi") is True
    assert creds.has_provider("anthropic") is False


def test_credentials_empty():
    creds = Credentials()
    assert creds.available_providers() == set()
    assert creds.has_any() is False


async def test_ensure_openai_token_no_token():
    from home_ops_agent.auth.credentials import ensure_openai_token

    assert await ensure_openai_token(Credentials()) is None


async def test_ensure_openai_token_still_valid():
    from home_ops_agent.auth.credentials import ensure_openai_token

    creds = Credentials(
        openai_access_token="tok",
        openai_refresh_token="ref",
        openai_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    # Far from expiry — returns existing token without refreshing.
    assert await ensure_openai_token(creds) == "tok"
