"""The pi harness — the backend for every model that is not ``claude-code/*``.

Why this exists rather than the hand-rolled loop in :mod:`core`. That loop talks
to ``chatgpt.com/backend-api/codex`` and drives tool use itself, which works but
leaves ChatGPT models unable to edit a file: ``core.run`` refuses a workspace on
any provider but Claude Code, because Claude Code was the only backend whose CLI
already had file and shell tools. pi has ``read``, ``bash``, ``edit`` and
``write`` built in, so pointing it at a worktree gives every model the same
capability and removes the asymmetry rather than working around it.

Tools are the agent's own, reached over a socket. pi executes tools in its own
process, so the Python ``ToolDefinition`` registry is not directly reachable from
here -- but reimplementing it in TypeScript was tried and abandoned. About a
third of the tools carry credentials that must not enter a process with a
``bash`` tool, and the rest would have existed twice forever, because the Claude
Code backend still needs the Python ones. Two copies drift.

:mod:`home_ops_agent.agent.tool_bridge` therefore serves whatever tool list this
module is handed, for the lifetime of one run. ``workspace_commit`` arrives the
same way as everything else; the earlier single-purpose bridge existed only to
carry it.

Discovery is disabled with ``-ne``. Without it pi loads whatever sits in
``~/.pi`` or the working directory — and the working directory here is a
checkout of home-ops, so a file in the repository being edited could otherwise
introduce a tool the image never shipped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

from home_ops_agent.auth.credentials import Credentials, ensure_openai_token

if TYPE_CHECKING:
    from home_ops_agent.agent.core import AgentResult, ToolDefinition
    from home_ops_agent.agent.workspace import Workspace

logger = logging.getLogger(__name__)

# pi resolves its own directory from HOME, as ~/.pi — there is no variable that
# overrides it. The container's HOME is /home/agent on a read-only filesystem,
# so pi cannot create its session directory there and dies before it reaches the
# model:
#
#   Error: ENOENT: no such file or directory,
#     mkdir '/home/agent/.pi/agent/sessions/--app--'
#
# PI_HOME is therefore not passed through to pi; it names a writable directory
# that is handed to the subprocess *as* HOME.
PI_HOME = Path(os.environ.get("PI_HOME", "/tmp/pi"))
AUTH_FILE = PI_HOME / ".pi" / "agent" / "auth.json"

# Shipped by the image, not discovered.
EXTENSIONS_DIR = Path(os.environ.get("PI_EXTENSIONS_DIR", "/app/extensions"))

# pi's name for the ChatGPT-subscription provider. The field names written below
# are pi's, and they line up one-for-one with what Credentials already stores.
CODEX_PROVIDER = "openai-codex"


def _extension_args() -> list[str]:
    """Every ``.ts`` in the extensions directory, passed explicitly."""
    if not EXTENSIONS_DIR.is_dir():
        logger.warning("pi extensions directory %s is missing", EXTENSIONS_DIR)
        return []
    args: list[str] = []
    for path in sorted(EXTENSIONS_DIR.glob("*.ts")):
        args += ["-e", str(path)]
    return args


def write_auth(credentials: Credentials) -> bool:
    """Materialise ``auth.json`` from stored credentials.

    Returns ``False`` when there is nothing to write, so the caller can fail with
    a credentials error rather than letting pi start and report an opaque 401.

    Written 0600: it holds a live access token, and pi requires those
    permissions on the file.
    """
    if not credentials.openai_access_token:
        return False

    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    # The refresh token is deliberately withheld. OpenAI rotates it on use, so
    # two parties refreshing the same credential invalidate each other -- pi did
    # exactly that here and the next call came back
    #
    #   401 refresh_token_reused: "Your refresh token has already been used to
    #   generate a new access token. Please try signing in again."
    #
    # which killed the stored credential outright rather than just failing the
    # run. ensure_openai_token already serialises refreshes across the agent's
    # own callers; pi is simply not one of them. It receives a token that is
    # already fresh, and if it ever does expire mid-run pi fails loudly instead
    # of silently spending the token the agent depends on.
    entry: dict[str, Any] = {
        "type": "oauth",
        "access": credentials.openai_access_token,
        "accountId": credentials.openai_account_id or "",
    }
    if credentials.openai_expires_at:
        # pi stores this as epoch milliseconds.
        entry["expires"] = int(credentials.openai_expires_at.timestamp() * 1000)

    AUTH_FILE.write_text(json.dumps({CODEX_PROVIDER: entry}), encoding="utf-8")
    AUTH_FILE.chmod(0o600)
    return True


# What pi is allowed to see of this process's environment.
#
# pi has a `bash` tool, so every variable handed to the subprocess is readable
# by the model. The first version of this passed `{**os.environ}`, which meant
# pi received GITHUB_TOKEN, DATABASE_URL, SESSION_SECRET, MCP_API_TOKEN and
# NTFY_TOKEN. A model holding GITHUB_TOKEN can simply `git push`, which is the
# precise thing :mod:`home_ops_agent.agent.workspace_bridge` exists to prevent
# -- the bridge withheld the token and the environment handed it straight back.
#
# An allowlist rather than a denylist, so a secret added to the deployment
# tomorrow is excluded because nobody listed it, not included because nobody
# remembered to block it.
#
# The ServiceAccount token stays reachable on disk at /var/run/secrets, and that
# is intended: `cluster.ts` reads it, and it grants exactly what the agent's
# ClusterRole already allows through those tools. It is not a capability the
# model gains by reading the file.
PI_ENV_ALLOWLIST = frozenset(
    {
        "PATH",  # finding node at all
        "LANG",
        "TZ",
        "SEARXNG_URL",  # searxng.ts
        "KUBERNETES_SERVICE_HOST",  # cluster.ts
        "KUBERNETES_SERVICE_PORT",
        "KUBERNETES_SERVICE_PORT_HTTPS",
    }
)


def build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment pi is launched with: the allowlist, plus HOME and ``extra``.

    ``extra`` is for values minted for one run -- the workspace bridge's socket
    and token -- which are deliberately *not* secrets of this process.
    """
    env = {key: value for key, value in os.environ.items() if key in PI_ENV_ALLOWLIST}
    # Not from the allowlist: pi resolves ~/.pi from HOME and the container's
    # real HOME is read-only.
    env["HOME"] = str(PI_HOME)
    if extra:
        env.update(extra)
    return env


def _text_of(message: dict[str, Any]) -> str:
    """Concatenate the text blocks of one assistant message."""
    return "".join(
        block.get("text", "")
        for block in message.get("content") or []
        if block.get("type") == "text"
    )


# Appended, not merged into the agent's prompt, because it is true only of this
# backend: the Claude Code path gets `workspace_commit` as an ordinary Python
# tool and needs none of this said. It is deliberately short -- the agent's own
# prompt is the one that should be steering.
WORKSPACE_NOTE = (
    "You are working inside a git worktree and have file and shell tools. "
    "To land your changes you must call the workspace_commit tool. Committing or "
    "pushing with git yourself will not work: the credential is deliberately not "
    "available to your shell, and workspace_commit is the only path out. It "
    "rejects files outside the allowed paths, names them, and unstages the "
    "change, so a rejection is recoverable within this run."
)


def build_argv(
    model: str,
    system_prompt: str,
    prompt: str,
    append_prompt: str = "",
) -> list[str]:
    """The command line pi is invoked with.

    Split out so a test can assert the safety-relevant flags are present without
    running pi.

    ``--system-prompt`` *replaces* pi's built-in prompt rather than adding to it,
    which is the intent. That prompt opens with "You are an expert coding
    assistant operating inside pi" and then spends most of its length telling the
    model where pi's own README, docs and examples live and when to read them --
    guidance for someone working *on* pi, and a standing invitation to go reading
    documentation that has nothing to do with this cluster.

    What replacing it costs is pi's "Available tools" section, which is the only
    consumer of the ``promptSnippet`` each extension registers. Tool *selection*
    is unaffected -- that runs off the schemas sent with the request, which is how
    a model picked `web_search` and the cluster tools correctly before this was
    noticed -- so the snippets are currently inert rather than missed.

    ``--append-system-prompt`` composes with ``--system-prompt`` (pi appends it in
    both branches), which is how the workspace note reaches the model without
    either prompt having to know about the other.
    """
    model_arg = model if "/" in model else f"{CODEX_PROVIDER}/{model}"
    argv = [
        "pi",
        "-ne",
        *_extension_args(),
        "--model",
        model_arg,
        "--mode",
        "json",
        "--system-prompt",
        system_prompt,
    ]
    if append_prompt:
        argv += ["--append-system-prompt", append_prompt]
    return [*argv, "-p", prompt]


async def stream(
    system_prompt: str,
    messages: list[dict[str, Any]],
    model: str,
    credentials: Credentials,
    workspace: Workspace | None = None,
    tools: list[ToolDefinition] | None = None,
    on_tool_start: Callable[..., Coroutine] | None = None,
    on_tool_end: Callable[..., Coroutine] | None = None,
) -> AsyncGenerator[str | AgentResult, None]:
    """Run a prompt through pi, yielding assistant text then an ``AgentResult``.

    Mirrors :func:`home_ops_agent.agent.claude_code.stream` so ``core`` can treat
    the two backends alike.
    """
    from home_ops_agent.agent.claude_code import flatten_messages

    # Refresh before writing, so the token pi receives is valid for the whole
    # run and pi never reaches for the refresh it is not given.
    await ensure_openai_token(credentials)
    if not write_auth(credentials):
        raise ValueError("OpenAI credentials unavailable")

    # pi takes a single prompt string, so the conversation is flattened the same
    # way the Claude Code backend does it and a resumed chat keeps its history.
    argv = build_argv(
        model,
        system_prompt,
        flatten_messages(messages),
        append_prompt=WORKSPACE_NOTE if workspace is not None else "",
    )

    PI_HOME.mkdir(parents=True, exist_ok=True)
    env = build_env()
    cwd = str(workspace.path) if workspace is not None else None

    # The agent's own tools, served over a socket rather than reimplemented here.
    # A third of them carry credentials that must not enter this environment --
    # pi has a `bash` tool -- and the rest would otherwise exist twice, since the
    # Claude Code backend still needs the Python implementations. Imported here
    # rather than at module scope because the bridge reaches `core`, which
    # imports this module.
    from home_ops_agent.agent import tool_bridge

    served = list(tools or [])
    if workspace is not None:
        # workspace_commit is a ToolDefinition like any other, so it arrives the
        # same way. Composed here rather than by the caller, exactly as
        # claude_code.stream does it.
        from home_ops_agent.agent.workspace import build_workspace_tools

        served = [*served, *build_workspace_tools(workspace)]

    async with AsyncExitStack() as stack:
        if served:
            handle = await stack.enter_async_context(tool_bridge.serve(served))
            env = build_env(handle.env())

        async for item in _drive(argv, env, cwd, model, on_tool_start, on_tool_end):
            yield item


# pi embeds whole tool results in its event stream, so one line carries a pod
# list, a PR diff or a page of logs. asyncio's default stream limit is 64 KiB
# per line, and exceeding it raises out of the read loop -- which ended the run
# with "Separator is found, but chunk is longer than limit", a message that
# says nothing about tool output being large. Asking the cluster for its health
# was enough to trigger it.
STREAM_LIMIT = 16 * 1024 * 1024


async def _events(stdout: asyncio.StreamReader) -> AsyncGenerator[dict[str, Any], None]:
    """pi's newline-delimited JSON events, skipping what cannot be read.

    Records are split on newline only. pi's protocol note is explicit that also
    splitting on U+2028/U+2029 -- which some readers do -- breaks framing.

    A line past the limit is dropped rather than fatal: `readline` discards it
    and keeps framing when the newline was found, so the run continues with one
    event missing instead of failing outright. The event most likely to be that
    large is a tool result the model has already received.
    """
    while True:
        try:
            raw = await stdout.readline()
        except ValueError:
            # asyncio raises this for a line over the limit, having already
            # dropped it.
            logger.warning("pi: dropped an event line larger than %d bytes", STREAM_LIMIT)
            continue

        if not raw:
            return

        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            logger.debug("pi: non-JSON line %r", line[:200])


async def _drive(
    argv: list[str],
    env: dict[str, str],
    cwd: str | None,
    model: str,
    on_tool_start: Callable[..., Coroutine] | None = None,
    on_tool_end: Callable[..., Coroutine] | None = None,
) -> AsyncGenerator[str | AgentResult, None]:
    """Run pi and turn its event stream into assistant text plus an ``AgentResult``.

    Split out of :func:`stream` so the workspace bridge's lifetime is a plain
    ``async with`` around one call, rather than a try/finally wrapped around
    ninety lines of stream parsing.
    """
    from home_ops_agent.agent.core import AgentResult

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
        limit=STREAM_LIMIT,
    )

    tool_calls: list[dict[str, Any]] = []
    input_tokens = output_tokens = 0
    last_text = ""
    stop_reason = ""
    # Tools the chat has been told about and not yet told about finishing,
    # keyed by pi's call id: {id: (name, index)}.
    running: dict[str, tuple[str, int]] = {}
    tool_index = 0

    assert proc.stdout is not None
    async for event in _events(proc.stdout):
        kind = event.get("type")

        if kind == "tool_execution_start":
            # `tool`, not `name`. Every other backend emits {"tool", "input"} --
            # core.py in four places, claude_code.py -- and both consumers read
            # that key: the MCP server does `c.get("tool")` and the chat UI
            # renders `label={tc.tool}`. This module emitted `name` from the
            # start, so every GPT run since 0.14.0 has shown blank tool chips in
            # the chat and null tool names over MCP, while working perfectly.
            #
            name = event.get("toolName") or "tool"
            tool_calls.append({"tool": event.get("toolName"), "input": event.get("args")})

            # Reported as it happens, not at the end. Every other backend calls
            # these, and pi was the one that did not -- so a GPT chat sat on
            # "thinking" for the whole run and then showed what it had used,
            # while the same question on a Claude model narrated itself.
            #
            # toolCallId earns its keep here: it is what pairs an end event with
            # the start it belongs to, which matters once two tools overlap.
            call_id = str(event.get("toolCallId") or tool_index)
            running[call_id] = (name, tool_index)
            tool_index += 1
            if on_tool_start:
                await on_tool_start(name, running[call_id][1])
        elif kind == "tool_execution_end":
            call_id = str(event.get("toolCallId") or "")
            finished = running.pop(call_id, None)
            if finished and on_tool_end:
                await on_tool_end(*finished)
        elif kind == "message_end":
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            stop_reason = message.get("stopReason") or stop_reason
            usage = message.get("usage") or {}
            input_tokens += int(usage.get("input") or 0)
            output_tokens += int(usage.get("output") or 0)
            text = _text_of(message)
            if text:
                last_text = text
                yield text

    # Anything still open when the stream ends is closed here, so a backend that
    # does not emit an end event -- or a run that dies mid-tool -- cannot leave
    # a spinner turning in the chat forever.
    for name, index in running.values():
        if on_tool_end:
            await on_tool_end(name, index)
    running.clear()

    stderr_raw = await proc.stderr.read() if proc.stderr else b""
    await proc.wait()

    # stopReason is the only place a rejected request shows up. Measured against
    # a live rejection, pi gives exit 0, an empty stderr, and a well-formed event
    # stream whose assistant message carries stopReason=error and no text:
    #
    #   exit=0  stderr=0 bytes  message_end stopReason=error err=None
    #
    # Two earlier versions of this guard keyed on the exit code and then on
    # stderr, and both let that through as a successful empty answer. The exit
    # code and stderr checks are kept because they are what caught pi crashing
    # on a read-only HOME, which stopReason never sees.
    if not last_text and (stop_reason == "error" or proc.returncode != 0 or stderr_raw.strip()):
        # Provider rejections arrive on stderr as plain text; the JSON stream
        # carries only `stopReason: error` with no detail, so without this a
        # rejected model surfaces as a silent empty response. That is exactly
        # how "model not supported when using Codex with a ChatGPT account"
        # presented before it was tracked down.
        stderr = stderr_raw.decode("utf-8", errors="replace").strip()
        # Not the last line. A Node crash ends with the runtime version banner,
        # so reporting the tail turned "ENOENT: cannot mkdir ~/.pi/..." into
        # "Node.js v22.23.2" and hid the actual fault. Keep enough of the tail
        # to carry a stack trace's message line.
        detail = " | ".join(stderr.splitlines()[:6]) if stderr else ""
        if not detail:
            # A rejected request carries no detail anywhere -- error is null on
            # the message too -- so say what is actually known rather than
            # inventing a cause.
            detail = (
                f"stopReason={stop_reason or 'unknown'}, no output. "
                "Usually the provider rejected the credential or the model."
            )
        raise RuntimeError(f"pi exited {proc.returncode}: {detail}")

    yield AgentResult(
        response=last_text,
        tool_calls=tool_calls,
        total_tokens=input_tokens + output_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
    )


async def run(
    system_prompt: str,
    messages: list[dict[str, Any]],
    model: str,
    credentials: Credentials,
    workspace: Workspace | None = None,
    tools: list[ToolDefinition] | None = None,
) -> AgentResult:
    """Non-streaming variant — drains :func:`stream` and returns the result."""
    from home_ops_agent.agent.core import AgentResult

    result: AgentResult | None = None
    async for item in stream(system_prompt, messages, model, credentials, workspace, tools):
        if isinstance(item, AgentResult):
            result = item
    if result is None:
        return AgentResult(response="[No response from pi]", model=model)
    return result
