"""Provider registry — maps model IDs to their API provider.

The agent supports four providers that can all be authenticated at once:

- ``anthropic``   — Claude models via the Anthropic API (API key).
- ``kimi``        — Moonshot "Kimi for Coding" via its Anthropic-compatible
                    endpoint (API key + base URL). Uses the same wire protocol
                    as Anthropic, so it reuses the Anthropic backend.
- ``openai``      — GPT / Codex models billed to a ChatGPT subscription, via the
                    ChatGPT backend Responses API (OAuth access token).
- ``claude_code`` — Claude models billed to a **Claude subscription** (Pro/Max),
                    run through the local Claude Code CLI via the Claude Agent
                    SDK. Selected with a ``claude-code/`` model prefix, e.g.
                    ``claude-code/sonnet``. The agent's own tools are handed to
                    the CLI as an in-process MCP server, so tool handlers (and
                    their guardrails) are unchanged.

The provider for a model is resolved from its ID prefix so new model names can
be added (in the UI / DB) without touching code.
"""

ANTHROPIC = "anthropic"
KIMI = "kimi"
OPENAI = "openai"
CLAUDE_CODE = "claude_code"

PROVIDERS = (ANTHROPIC, KIMI, OPENAI, CLAUDE_CODE)

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

# --- Claude Code CLI (Claude Pro/Max subscription) ---
# A ``claude-code/`` prefix routes an otherwise ordinary Claude model through the
# Claude Code CLI instead of the Anthropic API, so the request bills to the
# subscription rather than to API credit. The suffix is passed to the CLI as
# ``--model`` verbatim, so both aliases (``sonnet``) and full IDs work.
CLAUDE_CODE_PREFIX = "claude-code/"
# In-process MCP server name; tools are exposed to the CLI as
# ``mcp__{CLAUDE_CODE_MCP_SERVER}__{tool_name}``.
CLAUDE_CODE_MCP_SERVER = "homeops"
# Long-lived (1 year) subscription token minted by ``claude setup-token``.
CLAUDE_CODE_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

_OPENAI_PREFIXES = ("gpt", "codex", "o1", "o3", "o4", "chatgpt")


def resolve_provider(model: str) -> str:
    """Resolve the provider that serves a given model ID.

    Resolution is prefix-based so model IDs can be configured without code
    changes:

    - ``claude-code/*``                     -> claude_code
    - ``claude-*``                          -> anthropic
    - ``kimi-*`` / ``kimi-for-coding``      -> kimi
    - ``gpt-*`` / ``codex-*`` / ``o3*`` ... -> openai

    Anything unrecognized falls back to Anthropic (the historical default).
    """
    m = model.lower().strip()
    if m.startswith(CLAUDE_CODE_PREFIX):
        return CLAUDE_CODE
    if m.startswith("kimi"):
        return KIMI
    if m.startswith(_OPENAI_PREFIXES):
        return OPENAI
    return ANTHROPIC


def claude_code_model(model: str) -> str:
    """Strip the ``claude-code/`` prefix, yielding the CLI's ``--model`` value.

    An empty suffix (bare ``claude-code/``) means "let the CLI pick", which the
    backend signals by returning an empty string.
    """
    m = model.strip()
    if m.lower().startswith(CLAUDE_CODE_PREFIX):
        return m[len(CLAUDE_CODE_PREFIX) :].strip()
    return m
