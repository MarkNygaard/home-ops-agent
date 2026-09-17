"""The pi harness — the backend for every model that is not ``claude-code/*``.

Why this exists rather than the hand-rolled loop in :mod:`core`. That loop talks
to ``chatgpt.com/backend-api/codex`` and drives tool use itself, which works but
leaves ChatGPT models unable to edit a file: ``core.run`` refuses a workspace on
any provider but Claude Code, because Claude Code was the only backend whose CLI
already had file and shell tools. pi has ``read``, ``bash``, ``edit`` and
``write`` built in, so pointing it at a worktree gives every model the same
capability and removes the asymmetry rather than working around it.

Tools are written as extensions, not bridged. pi executes tools in-process, and
RPC mode drives pi rather than serving tools back to the host, so the Python
``ToolDefinition`` registry is not reachable from here. Tools pi should have are
written under ``extensions/`` and loaded with ``-e``. The Python tools stay where
they are and remain available to the Claude Code backend; the two sets converge
only if everything moves to pi.

There is exactly one exception, and it is about a credential rather than a tool.
``workspace_commit`` needs the GitHub push token, and pi has a ``bash`` tool --
so a token placed in this subprocess's environment is a token the model can
``git push`` with, walking past ``ALLOWED_COMMIT_PATHS`` instead of through it.
When a workspace is attached, :mod:`home_ops_agent.agent.workspace_bridge` hands
pi a single-run Unix socket whose only action is the guarded commit, and keeps
the token on this side. See that module for why the trade is sound.

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
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

from home_ops_agent.auth.credentials import Credentials, ensure_openai_token

if TYPE_CHECKING:
    from home_ops_agent.agent.core import AgentResult
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
    env = {**os.environ, "HOME": str(PI_HOME)}
    cwd = str(workspace.path) if workspace is not None else None

    async with AsyncExitStack() as stack:
        if workspace is not None:
            # pi can edit files in the checkout but must not be able to push
            # from it: it has a `bash` tool, so a GitHub token in this
            # environment is a token the model can `git push` with, straight
            # past the path and branch guardrails. The bridge hands it a
            # single-run socket instead, whose only action is the guarded
            # commit. Imported here rather than at module scope because
            # workspace_bridge reaches `core`, which imports this module.
            from home_ops_agent.agent import workspace_bridge

            handle = await stack.enter_async_context(workspace_bridge.serve(workspace))
            env.update(handle.env())

        async for item in _drive(argv, env, cwd, model):
            yield item


async def _drive(
    argv: list[str],
    env: dict[str, str],
    cwd: str | None,
    model: str,
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
    )

    tool_calls: list[dict[str, Any]] = []
    input_tokens = output_tokens = 0
    last_text = ""
    stop_reason = ""

    assert proc.stdout is not None
    # Records are split on newline only. pi's protocol note is explicit that
    # also splitting on U+2028/U+2029 — which some readers do — breaks framing.
    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("pi: non-JSON line %r", line[:200])
            continue

        kind = event.get("type")

        if kind == "tool_execution_start":
            tool_calls.append(
                {
                    "id": event.get("toolCallId"),
                    "name": event.get("toolName"),
                    "input": event.get("args"),
                }
            )
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
) -> AgentResult:
    """Non-streaming variant — drains :func:`stream` and returns the result."""
    from home_ops_agent.agent.core import AgentResult

    result: AgentResult | None = None
    async for item in stream(system_prompt, messages, model, credentials, workspace):
        if isinstance(item, AgentResult):
            result = item
    if result is None:
        return AgentResult(response="[No response from pi]", model=model)
    return result
