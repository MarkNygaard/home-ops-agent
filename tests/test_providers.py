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


async def test_ensure_openai_token_adopts_peer_refreshed_token(db_session):
    """When a near-expiry token is already refreshed in the DB by a peer,
    adopt the persisted token instead of issuing a redundant refresh."""
    from home_ops_agent.auth import credentials as creds_mod
    from home_ops_agent.auth.credentials import ensure_openai_token

    fresh_expiry = datetime.now(UTC) + timedelta(hours=1)
    await creds_mod.store_settings(
        {
            creds_mod.OPENAI_ACCESS_TOKEN_KEY: "fresh-token",
            creds_mod.OPENAI_REFRESH_TOKEN_KEY: "fresh-refresh",
            creds_mod.OPENAI_EXPIRES_AT_KEY: fresh_expiry.isoformat(),
        }
    )

    # This caller still holds a near-expiry token (a peer just refreshed).
    creds = Credentials(
        openai_access_token="stale-token",
        openai_refresh_token="stale-refresh",
        openai_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    # No httpx mock: if this tried to actually refresh it would hit the network,
    # so a clean return proves it adopted the DB token instead.
    token = await ensure_openai_token(creds)
    assert token == "fresh-token"
    assert creds.openai_access_token == "fresh-token"
    assert creds.openai_refresh_token == "fresh-refresh"
