"""Claude Code backend — runs a task on a Claude Pro/Max subscription.

Instead of calling the Anthropic API with an API key, this backend drives the
local **Claude Code CLI** through the Claude Agent SDK, so the request bills to
a Claude subscription. Selected per task with a ``claude-code/`` model prefix
(see :mod:`home_ops_agent.agent.providers`).

The agent's own tools are *not* replaced by Claude Code's built-ins. They are
handed to the CLI as an **in-process MCP server** built from the registered
:class:`~home_ops_agent.agent.core.ToolDefinition` list, so every handler — and
every code-level guardrail inside it (``PROTECTED_NAMESPACES``,
``ALLOWED_COMMIT_PATHS``, ``PROTECTED_BRANCHES``) — runs exactly as it does on
the API backends. Claude Code's own filesystem/shell tools are removed from the
model's context entirely (``tools=[]``), so the only things it can call are
ours.

Two behavioural differences from the API backends are worth knowing:

- **History is flattened.** ``query()`` takes a single prompt, not a message
  list, so a multi-turn history is rendered into a transcript (see
  :func:`flatten_messages`). Single-message runs — every worker — are passed
  through unchanged.
- **Streaming needs partial messages.** Token-level deltas arrive as
  ``StreamEvent``s (enabled by ``include_partial_messages``); the completed
  ``AssistantMessage`` that follows is used only for bookkeeping so text is not
  emitted twice.
"""

import json
import logging
import os
import tempfile
from collections.abc import AsyncGenerator, Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

from home_ops_agent.agent import providers

if TYPE_CHECKING:
    from home_ops_agent.agent.core import AgentResult, ToolDefinition
    from home_ops_agent.agent.workspace import Workspace

logger = logging.getLogger(__name__)

# Built-in Claude Code tools made available to the model. Empty means "none":
# the agent may only call the MCP tools we register, which is what keeps the
# guardrails in the tool handlers authoritative.
BUILTIN_TOOLS: list[str] = []

# When a workspace (a git worktree) is attached, the agent gets a real checkout
# to work in, so the built-in file and shell tools are what make it useful:
# grep the repo, read whole files, edit several, validate the result. The way
# out is still guarded — `workspace_commit` is the only thing that can push,
# and it re-checks every changed path.
WORKSPACE_BUILTIN_TOOLS: list[str] = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]

# Credential sources the CLI ranks ABOVE `CLAUDE_CODE_OAUTH_TOKEN` in its
# documented authentication precedence. Any of these present in the pod's
# environment silently takes over the run — most consequentially
# `ANTHROPIC_API_KEY`, which bills metered API credit instead of the
# subscription this backend exists to use. All carry values, so blanking them
# is enough to take them out of the running. Applied on every run.
_PRECEDENCE_ENV = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
)

# Cloud-provider selectors that outrank everything above. These are read as
# flags, not values, so blanking one could plausibly read as "set" and select
# the provider — the opposite of what we want. Nothing in this app sets them;
# if an operator has, that is a deliberate choice we surface rather than fight.
_PROVIDER_FLAGS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

# Control-plane secrets that must never be readable by a shell the agent
# spawns. Only relevant once a workspace enables `Bash`; the credential sources
# above are blanked whether or not there is a shell.
_MASKED_ENV = (
    "DATABASE_URL",
    "SESSION_SECRET",
    "NTFY_TOKEN",
    "GITHUB_TOKEN",
    "KIMI_API_KEY",
    "OPENAI_API_KEY",
)


def _auth_env(oauth_token: str, workspace_attached: bool) -> dict[str, str]:
    """Environment that guarantees the subscription token is the credential used."""
    for flag in _PROVIDER_FLAGS:
        if os.environ.get(flag):
            logger.warning(
                "%s is set; the Claude Code CLI will use that cloud provider "
                "instead of the Claude subscription for this run.",
                flag,
            )

    env = {key: "" for key in _PRECEDENCE_ENV}
    if workspace_attached:
        env.update({key: "" for key in _MASKED_ENV})
    # Set last: nothing above may clobber the credential we intend to use.
    env[providers.CLAUDE_CODE_TOKEN_ENV] = oauth_token
    return env


def _sdk():
    """Import the Claude Agent SDK lazily, with an actionable error message."""
    try:
        import claude_agent_sdk
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError(
            "The 'claude-agent-sdk' package is required for claude-code/* models "
            "(it bundles the Claude Code CLI it spawns)."
        ) from exc
    return claude_agent_sdk


def flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Render an Anthropic-style message list as a single prompt string.

    A lone user message (the shape every worker sends) is passed through
    verbatim. Longer histories become a labelled transcript with the final user
    message left as the live instruction, so the model can tell prior context
    from the thing it is being asked to do now.
    """
    texts: list[tuple[str, str]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # Tool-use / tool-result blocks have no place in a flattened
            # transcript — the CLI runs its own tool loop.
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "\n".join(p for p in parts if p)
        else:
            text = ""
        if text.strip():
            texts.append((message.get("role", "user"), text.strip()))

    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0][1]

    *history, (last_role, last_text) = texts
    lines = ["<conversation_history>"]
    for role, text in history:
        lines.append(f"<{role}>\n{text}\n</{role}>")
    lines.append("</conversation_history>\n")
    if last_role == "user":
        lines.append(last_text)
    else:
        lines.append(f"<{last_role}>\n{last_text}\n</{last_role}>")
    return "\n".join(lines)


class _ToolContext:
    """Per-run bookkeeping shared by the MCP tool wrappers."""

    def __init__(
        self,
        on_tool_start: Callable[..., Coroutine] | None,
        on_tool_end: Callable[..., Coroutine] | None,
    ):
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end
        self.tool_calls: list[dict[str, Any]] = []
        self._index = 0

    def next_index(self) -> int:
        index = self._index
        self._index += 1
        return index


def _wrap_tool(tool_def: "ToolDefinition", ctx: _ToolContext):
    """Adapt a ``ToolDefinition`` into an SDK MCP tool.

    The wrapper — not the message stream — is where tool progress is reported,
    because it brackets the handler exactly and does not depend on how the SDK
    happens to surface tool_use/tool_result blocks.
    """
    sdk = _sdk()

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        index = ctx.next_index()
        ctx.tool_calls.append({"tool": tool_def.name, "input": args})
        logger.info("Executing tool: %s", tool_def.name)
        if ctx.on_tool_start:
            await ctx.on_tool_start(tool_def.name, index)
        try:
            result = await tool_def.handler(args)
            if not isinstance(result, str):
                result = json.dumps(result, default=str)
            return {"content": [{"type": "text", "text": result}]}
        except Exception as exc:
            logger.exception("Tool %s failed", tool_def.name)
            return {
                "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                "is_error": True,
            }
        finally:
            if ctx.on_tool_end:
                await ctx.on_tool_end(tool_def.name, index)

    handler.__name__ = tool_def.name
    return sdk.tool(tool_def.name, tool_def.description, tool_def.input_schema)(handler)


def _workdir() -> str:
    """A scratch cwd for the CLI.

    The agent operates on the cluster through its own tools and never on a
    checkout, so the CLI gets an empty directory rather than the application
    source tree. Stable (not per-run) so session state isn't scattered.
    """
    path = Path(tempfile.gettempdir()) / "home-ops-agent-claude-code"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def build_options(
    tools: list["ToolDefinition"],
    system_prompt: str,
    model: str,
    max_turns: int,
    oauth_token: str,
    ctx: _ToolContext,
    workspace: "Workspace | None" = None,
):
    """Assemble ``ClaudeAgentOptions`` for one run."""
    sdk = _sdk()
    server_name = providers.CLAUDE_CODE_MCP_SERVER

    mcp_servers = {}
    allowed_tools: list[str] = []
    if tools:
        mcp_servers[server_name] = sdk.create_sdk_mcp_server(
            name=server_name,
            version="1.0.0",
            tools=[_wrap_tool(t, ctx) for t in tools],
        )
        allowed_tools = [f"mcp__{server_name}__*"]

    cli_model = providers.claude_code_model(model)

    # `options.env` merges over os.environ, so an empty value is how a variable
    # is taken away from the CLI and everything it spawns.
    env = _auth_env(oauth_token, workspace is not None)

    return sdk.ClaudeAgentOptions(
        model=cli_model or None,
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        tools=WORKSPACE_BUILTIN_TOOLS if workspace is not None else BUILTIN_TOOLS,
        allowed_tools=allowed_tools,
        # Headless: never block waiting for an interactive approval. Scope is
        # already enforced by `tools=[]` plus the allowed-tools list above.
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        # Token-level deltas for the chat UI; without this the SDK only emits
        # whole assistant turns.
        include_partial_messages=True,
        cwd=str(workspace.path) if workspace is not None else _workdir(),
        # Ignore ~/.claude and any project settings — this process's behaviour
        # must come from the DB-backed config, not from files in the image.
        setting_sources=[],
        env=env,
    )


def _usage_tokens(usage: Any) -> tuple[int, int]:
    """Extract ``(input_tokens, output_tokens)`` from a result's usage payload."""
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    # Cache reads/writes are billed input; count them so totals are comparable
    # with the API backends.
    input_tokens += int(usage.get("cache_creation_input_tokens") or 0)
    input_tokens += int(usage.get("cache_read_input_tokens") or 0)
    return input_tokens, output_tokens


def _stream_event_text(event: dict[str, Any]) -> str:
    """Pull the text out of a raw ``content_block_delta`` stream event."""
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return ""
    return delta.get("text") or ""


async def stream(
    tools: list["ToolDefinition"],
    system_prompt: str,
    messages: list[dict[str, Any]],
    model: str,
    max_turns: int,
    oauth_token: str,
    on_tool_start: Callable[..., Coroutine] | None = None,
    on_tool_end: Callable[..., Coroutine] | None = None,
    workspace: "Workspace | None" = None,
) -> AsyncGenerator["str | AgentResult", None]:
    """Run one task through the Claude Code CLI.

    Yields assistant text as each turn arrives, then a final ``AgentResult``.

    With a ``workspace`` the CLI runs inside a git worktree with its file and
    shell tools enabled, and gains ``workspace_commit`` as the single guarded
    way to push what it changed.
    """
    from home_ops_agent.agent.core import AgentResult

    sdk = _sdk()
    ctx = _ToolContext(on_tool_start, on_tool_end)
    if workspace is not None:
        from home_ops_agent.agent.workspace import build_workspace_tools

        tools = [*tools, *build_workspace_tools(workspace)]
    options = build_options(tools, system_prompt, model, max_turns, oauth_token, ctx, workspace)

    prompt = flatten_messages(messages)
    all_text: list[str] = []
    last_turn_text = ""
    final_text = ""
    total_input = total_output = 0

    streamed = ""

    async for message in sdk.query(prompt=prompt, options=options):
        if isinstance(message, sdk.StreamEvent):
            # Subagent output would carry a parent tool id; the main thread's
            # deltas are the only ones that belong in the answer.
            if message.parent_tool_use_id is not None:
                continue
            delta = _stream_event_text(message.event)
            if delta:
                streamed += delta
                yield delta
        elif isinstance(message, sdk.AssistantMessage):
            turn_text = "".join(
                block.text
                for block in message.content
                if isinstance(block, sdk.TextBlock) and block.text
            )
            # Normally this turn was already emitted delta by delta. Emit it
            # here only if nothing streamed, so a build without partial-message
            # support still produces output instead of silence.
            if turn_text and not streamed:
                yield turn_text
            streamed = ""
            if turn_text.strip():
                all_text.append(turn_text)
                last_turn_text = turn_text
        elif isinstance(message, sdk.ResultMessage):
            total_input, total_output = _usage_tokens(message.usage)
            final_text = message.result or ""

    # The API backends return only the final turn's text; intermediate
    # narration between tool calls is not part of the answer. Prefer the CLI's
    # own result, then the last assistant turn, then everything we saw.
    response = final_text or last_turn_text or "\n".join(all_text)

    yield AgentResult(
        response=response,
        tool_calls=ctx.tool_calls,
        total_tokens=total_input + total_output,
        input_tokens=total_input,
        output_tokens=total_output,
        model=model,
    )


async def run(
    tools: list["ToolDefinition"],
    system_prompt: str,
    messages: list[dict[str, Any]],
    model: str,
    max_turns: int,
    oauth_token: str,
    workspace: "Workspace | None" = None,
) -> "AgentResult":
    """Non-streaming variant — drains :func:`stream` and returns the result."""
    from home_ops_agent.agent.core import AgentResult

    result: AgentResult | None = None
    async for item in stream(
        tools, system_prompt, messages, model, max_turns, oauth_token, workspace=workspace
    ):
        if isinstance(item, AgentResult):
            result = item
    if result is None:  # pragma: no cover - stream always yields a result last
        return AgentResult(response="[No response from Claude Code]", model=model)
    return result
