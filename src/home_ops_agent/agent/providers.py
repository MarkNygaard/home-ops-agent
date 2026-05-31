"""Provider registry — maps model IDs to their API provider.

The agent supports three providers that can all be authenticated at once:

- ``anthropic`` — Claude models via the Anthropic API (API key).
- ``kimi``      — Moonshot "Kimi for Coding" via its Anthropic-compatible
                  endpoint (API key + base URL). Uses the same wire protocol
                  as Anthropic, so it reuses the Anthropic backend.
- ``openai``    — GPT / Codex models billed to a ChatGPT subscription, via the
                  ChatGPT backend Responses API (OAuth access token).

The provider for a model is resolved from its ID prefix so new model names can
be added (in the UI / DB) without touching code.
"""

ANTHROPIC = "anthropic"
KIMI = "kimi"
OPENAI = "openai"

PROVIDERS = (ANTHROPIC, KIMI, OPENAI)

# Providers that speak the Anthropic wire protocol (handled by the same backend).
ANTHROPIC_PROTOCOL = (ANTHROPIC, KIMI)

# --- Kimi for Coding (Anthropic-compatible) ---
KIMI_BASE_URL = "https://api.kimi.com/coding/"

# --- OpenAI / ChatGPT subscription (Codex public client) ---
# Requests authenticated with a ChatGPT subscription token are sent to the
# ChatGPT backend, which exposes the Responses API.
OPENAI_BASE_URL = "https://chatgpt.com/backend-api/codex"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
# Public OAuth client used by the Codex CLI; refresh works without a redirect.
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

_OPENAI_PREFIXES = ("gpt", "codex", "o1", "o3", "o4", "chatgpt")


def resolve_provider(model: str) -> str:
    """Resolve the provider that serves a given model ID.

    Resolution is prefix-based so model IDs can be configured without code
    changes:

    - ``claude-*``                          -> anthropic
    - ``kimi-*`` / ``kimi-for-coding``      -> kimi
    - ``gpt-*`` / ``codex-*`` / ``o3*`` ... -> openai

    Anything unrecognized falls back to Anthropic (the historical default).
    """
    m = model.lower().strip()
    if m.startswith("kimi"):
        return KIMI
    if m.startswith(_OPENAI_PREFIXES):
        return OPENAI
    return ANTHROPIC
