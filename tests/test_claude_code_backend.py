"""Tests for the Claude Code backend (agent/claude_code.py)."""

import json

import pytest

from home_ops_agent.agent import claude_code, costs, providers
from home_ops_agent.agent.core import Agent, ToolDefinition
from home_ops_agent.auth.credentials import Credentials

# --- provider routing ---


def test_resolve_provider_claude_code():
    assert providers.resolve_provider("claude-code/sonnet") == providers.CLAUDE_CODE
    assert providers.resolve_provider("claude-code/claude-opus-4-8") == providers.CLAUDE_CODE


def test_unrecognised_models_fall_back_to_the_subscription():
    """A dated ID left over from the metered provider must still resolve.

    The Anthropic API provider was removed; anything unrecognised now goes to
    the subscription rather than to a provider that cannot be configured.
    """
    assert providers.resolve_provider("claude-sonnet-4-6") == providers.CLAUDE_CODE
    assert providers.resolve_provider("something-weird") == providers.CLAUDE_CODE


def test_claude_code_model_strips_prefix():
    assert providers.claude_code_model("claude-code/sonnet") == "sonnet"
    # A dated ID collapses onto its alias, which tracks the current model.
    assert providers.claude_code_model("claude-code/claude-opus-4-8") == "opus"
    # Bare prefix means "let the CLI decide".
    assert providers.claude_code_model("claude-code/") == ""
    # Legacy settings written before the aliases existed keep working.
    assert providers.claude_code_model("claude-sonnet-4-6") == "sonnet"
    assert providers.claude_code_model("claude-haiku-4-5-20251001") == "haiku"
    # Anything else is passed to the CLI as given.
    assert providers.claude_code_model("kimi-for-coding") == "kimi-for-coding"


def test_credentials_expose_claude_code():
    creds = Credentials(claude_code_oauth_token="sk-ant-oat01-x")
    assert creds.available_providers() == {providers.CLAUDE_CODE}
    assert creds.has_provider(providers.CLAUDE_CODE) is True


def test_agent_rejects_claude_code_without_token():
    agent = Agent(Credentials(kimi_api_key="k"))
    with pytest.raises(ValueError, match="claude_code"):
        agent._provider_for("claude-code/sonnet")


# --- cost accounting ---


def test_claude_code_runs_are_not_billed():
    """Subscription runs must not inherit the unknown-model Sonnet fallback."""
    assert costs.calculate_cost("claude-code/sonnet", 1_000_000, 1_000_000) == 0.0
    # Every configurable provider is plan-billed, so nothing is priced per token.
    assert costs.calculate_cost("kimi-for-coding", 1_000_000, 0) == 0.0
    assert costs.calculate_cost("some-unknown-model", 1_000_000, 0) == 0.0


# --- message flattening ---


def test_flatten_single_message_is_verbatim():
    prompt = claude_code.flatten_messages([{"role": "user", "content": "Review PR #7"}])
    assert prompt == "Review PR #7"


def test_flatten_multi_turn_history():
    prompt = claude_code.flatten_messages(
        [
            {"role": "user", "content": "which pods are down?"},
            {"role": "assistant", "content": "none"},
            {"role": "user", "content": "check again"},
        ]
    )
    assert "<conversation_history>" in prompt
    assert "<assistant>\nnone\n</assistant>" in prompt
    # The live instruction sits outside the history block.
    assert prompt.endswith("check again")
    assert prompt.count("check again") == 1


def test_flatten_drops_tool_blocks_keeps_text():
    prompt = claude_code.flatten_messages(
        [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "id": "t1", "name": "list_pods", "input": {}},
                ],
            },
            {"role": "user", "content": "and now?"},
        ]
    )
    assert "checking" in prompt
    assert "list_pods" not in prompt


def test_flatten_empty():
    assert claude_code.flatten_messages([]) == ""
    assert claude_code.flatten_messages([{"role": "user", "content": "   "}]) == ""


# --- tool wrapping ---


def _tool(handler, name="list_pods"):
    return ToolDefinition(
        name=name,
        description="List pods",
        input_schema={"type": "object", "properties": {"ns": {"type": "string"}}},
        handler=handler,
    )


async def test_wrapped_tool_returns_mcp_content():
    async def handler(params):
        return f"pods in {params['ns']}"

    ctx = claude_code._ToolContext(None, None)
    wrapped = claude_code._wrap_tool(_tool(handler), ctx)

    result = await wrapped.handler({"ns": "default"})

    assert result == {"content": [{"type": "text", "text": "pods in default"}]}
    assert ctx.tool_calls == [{"tool": "list_pods", "input": {"ns": "default"}}]


async def test_wrapped_tool_serializes_non_string_results():
    async def handler(params):
        return {"pods": 3}

    ctx = claude_code._ToolContext(None, None)
    wrapped = claude_code._wrap_tool(_tool(handler), ctx)

    result = await wrapped.handler({})

    assert json.loads(result["content"][0]["text"]) == {"pods": 3}


async def test_wrapped_tool_reports_failure_as_error_result():
    """A handler raising must not abort the run — Claude should see the error."""

    async def handler(params):
        raise RuntimeError("cluster unreachable")

    ctx = claude_code._ToolContext(None, None)
    wrapped = claude_code._wrap_tool(_tool(handler), ctx)

    result = await wrapped.handler({})

    assert result["is_error"] is True
    assert json.loads(result["content"][0]["text"]) == {"error": "cluster unreachable"}


async def test_wrapped_tool_fires_progress_callbacks():
    events = []

    async def handler(params):
        events.append("run")
        return "ok"

    async def on_start(name, idx):
        events.append(("start", name, idx))

    async def on_end(name, idx):
        events.append(("end", name, idx))

    ctx = claude_code._ToolContext(on_start, on_end)
    wrapped = claude_code._wrap_tool(_tool(handler), ctx)

    await wrapped.handler({})

    assert events == [("start", "list_pods", 0), "run", ("end", "list_pods", 0)]


async def test_wrapped_tool_fires_end_callback_on_failure():
    events = []

    async def handler(params):
        raise RuntimeError("boom")

    async def on_end(name, idx):
        events.append(name)

    ctx = claude_code._ToolContext(None, on_end)
    wrapped = claude_code._wrap_tool(_tool(handler), ctx)

    await wrapped.handler({})

    assert events == ["list_pods"]


# --- options assembly ---


def test_build_options_scopes_tools_to_our_mcp_server():
    async def handler(params):
        return "ok"

    ctx = claude_code._ToolContext(None, None)
    options = claude_code.build_options(
        [_tool(handler)], "be helpful", "claude-code/sonnet", 10, "tok", ctx
    )

    # No built-in Claude Code tools: the agent can only call our handlers,
    # which is where the safety guardrails live.
    assert options.tools == []
    assert options.allowed_tools == ["mcp__homeops__*"]
    assert "homeops" in options.mcp_servers
    assert options.permission_mode == "bypassPermissions"
    assert options.max_turns == 10
    # The `claude-code/` prefix is ours, not a model the CLI knows.
    assert options.model == "sonnet"
    assert options.system_prompt == "be helpful"


def test_build_options_passes_token_and_blanks_higher_precedence_creds():
    """Everything the CLI ranks above CLAUDE_CODE_OAUTH_TOKEN must be neutralised.

    Any of these left set would quietly become the credential for the run —
    ANTHROPIC_API_KEY most damagingly, since it bills metered API credit rather
    than the subscription this backend exists to use.
    """
    ctx = claude_code._ToolContext(None, None)
    options = claude_code.build_options([], "p", "claude-code/sonnet", 5, "sk-ant-oat01-x", ctx)

    assert options.env[providers.CLAUDE_CODE_TOKEN_ENV] == "sk-ant-oat01-x"
    for key in (
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_PROFILE",
        "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_ORGANIZATION_ID",
    ):
        assert options.env[key] == "", f"{key} outranks the subscription token"
    # Nothing from ~/.claude or the repo should influence a run.
    assert options.setting_sources == []


def test_auth_env_never_lets_masking_clobber_the_token():
    """The token is written last, so no mask entry can blank it."""
    env = claude_code._auth_env("sk-ant-oat01-x", workspace_attached=True)
    assert env[providers.CLAUDE_CODE_TOKEN_ENV] == "sk-ant-oat01-x"
    assert env["DATABASE_URL"] == ""


def test_cloud_provider_flags_are_reported_not_blanked(monkeypatch, caplog):
    """Blanking a flag could read as 'set' and select the provider — warn instead."""
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")

    with caplog.at_level("WARNING"):
        env = claude_code._auth_env("tok", workspace_attached=False)

    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" in caplog.text


def test_build_options_bare_prefix_leaves_model_unset():
    ctx = claude_code._ToolContext(None, None)
    options = claude_code.build_options([], "p", "claude-code/", 5, "tok", ctx)
    assert options.model is None


# --- usage parsing ---


def test_usage_tokens_counts_cache_as_input():
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_creation_input_tokens": 5,
        "cache_read_input_tokens": 7,
    }
    assert claude_code._usage_tokens(usage) == (112, 20)


def test_usage_tokens_tolerates_missing_payload():
    assert claude_code._usage_tokens(None) == (0, 0)
    assert claude_code._usage_tokens({}) == (0, 0)


def test_stream_event_text_extracts_only_text_deltas():
    assert (
        claude_code._stream_event_text(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}
        )
        == "hi"
    )
    assert (
        claude_code._stream_event_text(
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hm"}}
        )
        == ""
    )
    assert claude_code._stream_event_text({"type": "message_start"}) == ""
