"""Tests for agent/providers.py and auth/credentials.py credential resolution."""

from datetime import UTC, datetime, timedelta

from home_ops_agent.agent import providers
from home_ops_agent.auth.credentials import Credentials


def test_resolve_provider_claude_code():
    """Claude models route to the subscription; there is no metered provider."""
    assert providers.resolve_provider("claude-code/sonnet") == providers.CLAUDE_CODE
    # Dated IDs from before the metered provider was removed still resolve.
    assert providers.resolve_provider("claude-sonnet-4-6") == providers.CLAUDE_CODE
    assert providers.resolve_provider("claude-opus-4-8") == providers.CLAUDE_CODE


def test_resolve_provider_kimi():
    assert providers.resolve_provider("kimi-for-coding") == providers.KIMI
    assert providers.resolve_provider("kimi-k2.6") == providers.KIMI


def test_resolve_provider_openai():
    assert providers.resolve_provider("gpt-5.5") == providers.OPENAI
    assert providers.resolve_provider("codex-5.3") == providers.OPENAI
    assert providers.resolve_provider("o3-mini") == providers.OPENAI


def test_resolve_provider_unknown_defaults_to_the_subscription():
    assert providers.resolve_provider("something-weird") == providers.CLAUDE_CODE


def test_credentials_available_providers():
    creds = Credentials(claude_code_oauth_token="oat", kimi_api_key="k", openai_access_token="t")
    assert creds.available_providers() == {"claude_code", "kimi", "openai"}
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


# --- removing the metered Anthropic provider must not break old settings ---


def test_no_anthropic_provider_remains():
    """It was removed because it required pinning dated model IDs by hand."""
    assert not hasattr(providers, "ANTHROPIC")
    assert providers.PROVIDERS == (
        providers.CLAUDE_CODE,
        providers.KIMI,
        providers.OPENAI,
    )
    # Kimi still speaks the protocol, so the backend grouping survives.
    assert providers.ANTHROPIC_PROTOCOL == (providers.KIMI,)


def test_credentials_cannot_hold_an_anthropic_key():
    import dataclasses

    fields = {f.name for f in dataclasses.fields(Credentials)}
    assert "anthropic_api_key" not in fields
    assert "claude_code_oauth_token" in fields


def test_dated_claude_ids_collapse_onto_aliases():
    """The point of the change: aliases track the current model on their own.

    A setting written as `claude-sonnet-4-6` needed editing every release; the
    CLI resolves `sonnet` itself. Old settings map onto the alias rather than
    failing.
    """
    cases = {
        "claude-haiku-4-5": "haiku",
        "claude-sonnet-4-6": "sonnet",
        "claude-opus-4-8": "opus",
        "claude-haiku-4-5-20251001": "haiku",
        "claude-code/claude-sonnet-4-6": "sonnet",
        "claude-code/sonnet": "sonnet",
    }
    for given, expected in cases.items():
        assert providers.claude_code_model(given) == expected, given


def test_aliases_are_passed_through_unchanged():
    for alias in ("haiku", "sonnet", "opus"):
        assert providers.claude_code_model(f"claude-code/{alias}") == alias


def test_a_non_claude_model_is_not_rewritten():
    assert providers.claude_code_model("kimi-for-coding") == "kimi-for-coding"


def test_every_default_model_resolves_to_a_real_provider():
    """A fresh deploy must not point at a provider that cannot be configured."""
    from home_ops_agent.agent.models import _DEFAULTS

    for task, model in _DEFAULTS.items():
        assert providers.resolve_provider(model) in providers.PROVIDERS, task
        assert model.startswith(providers.CLAUDE_CODE_PREFIX), f"{task} -> {model}"


# --- OpenAI model IDs ---


def test_gpt_56_tiers_route_to_openai():
    """GPT-5.6 ships as three tiers; all must resolve to the OpenAI provider."""
    for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        assert providers.resolve_provider(model) == providers.OPENAI, model


def test_retired_openai_ids_still_route_rather_than_falling_through():
    """A stale setting should reach OpenAI and fail there, not silently become
    a Claude subscription run on someone's plan."""
    for model in ("codex-5.3", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"):
        assert providers.resolve_provider(model) == providers.OPENAI, model
