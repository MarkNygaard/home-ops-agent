"""The pi harness — the backend for every model that is not ``claude-code/*``.

Why this exists rather than the hand-rolled loop in :mod:`core`. That loop talks
to ``chatgpt.com/backend-api/codex`` and drives tool use itself, which works but
leaves ChatGPT models unable to edit a file: ``core.run`` refuses a workspace on
any provider but Claude Code, because Claude Code was the only backend whose CLI
already had file and shell tools. pi has ``read``, ``bash``, ``edit`` and
``write`` built in, so pointing it at a worktree gives every model the same
capability and removes the asymmetry rather than working around it.

Tools do **not** cross the process boundary. pi executes tools in-process, and
RPC mode drives pi rather than serving tools back to the host, so the Python
``ToolDefinition`` registry is not reachable from here. Tools pi should have are
written as extensions under ``extensions/`` and loaded with ``-e``. The Python
tools stay where they are and remain available to the Claude Code backend; the
two sets converge only if everything moves to pi.

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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from home_ops_agent.auth.credentials import Credentials

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
    entry: dict[str, Any] = {
        "type": "oauth",
        "access": credentials.openai_access_token,
        "refresh": credentials.openai_refresh_token or "",
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


def build_argv(model: str, system_prompt: str, prompt: str) -> list[str]:
    """The command line pi is invoked with.

    Split out so a test can assert the safety-relevant flags are present without
    running pi.
    """
    model_arg = model if "/" in model else f"{CODEX_PROVIDER}/{model}"
    return [
        "pi",
        "-ne",
        *_extension_args(),
        "--model",
        model_arg,
        "--mode",
        "json",
        "--system-prompt",
        system_prompt,
        "-p",
        prompt,
    ]


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
    from home_ops_agent.agent.core import AgentResult

    if not write_auth(credentials):
        raise ValueError("OpenAI credentials unavailable")

    # pi takes a single prompt string, so the conversation is flattened the same
    # way the Claude Code backend does it and a resumed chat keeps its history.
    argv = build_argv(model, system_prompt, flatten_messages(messages))

    PI_HOME.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(PI_HOME)}
    cwd = str(workspace.path) if workspace is not None else None

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
            usage = message.get("usage") or {}
            input_tokens += int(usage.get("input") or 0)
            output_tokens += int(usage.get("output") or 0)
            text = _text_of(message)
            if text:
                last_text = text
                yield text

    stderr_raw = await proc.stderr.read() if proc.stderr else b""
    await proc.wait()

    if proc.returncode != 0 and not last_text:
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
        detail = " | ".join(stderr.splitlines()[:6]) if stderr else "no output"
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
