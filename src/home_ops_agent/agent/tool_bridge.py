"""The agent's tool registry, reachable from pi.

`extensions/README.md` used to argue against this, and the argument was sound
when the alternative was a thirty-line SearXNG tool written natively. It stopped
being sound once the surface was forty tools, because of two things that only
became clear by building the other way first:

**Roughly a third of the tools cannot be written natively at all.** The GitHub
tools and ntfy carry credentials, and pi has a `bash` tool -- so handing them
over puts the tokens where the model can read them. That is not hypothetical:
pi was launched with the whole environment until 0.18.2, which made
`workspace_commit`'s guardrails decorative until it was fixed.

**The rest would exist twice, forever.** Claude Code still needs the Python
implementations, so a native port doubles every tool, every guardrail --
`PROTECTED_NAMESPACES`, `ALLOWED_COMMIT_PATHS` -- and every test. They drift:
`cluster.ts` and `kubernetes.py` already disagreed about how a Flux `Ready`
condition is reported, within a day of both existing.

So the registry stays the single implementation and pi reaches it. The hop is a
Unix socket in the same process's pod, which costs microseconds; the duplication
would have cost forever.

**`workspace_commit` is no longer a special case.** It is a `ToolDefinition`
like any other, so it arrives through this like any other. The bridge this
replaces existed only to carry it.

**What the bridge does not decide.** It serves exactly the tools it is handed.
Which tools an agent gets -- enabled skills, `WITHHELD_FROM_PR_AGENT`, the
workspace tools -- is the caller's decision, unchanged, and made in the same
place for both backends.

The socket, its 0600 permissions, the per-run token and the reasoning behind
them are as they were: the token authorises exactly the tools the model already
has, so a model reading it out of its own environment gains nothing.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from home_ops_agent.agent.core import ToolDefinition

logger = logging.getLogger(__name__)

SOCKET_ENV = "HOMEOPS_TOOLS_SOCKET"
TOKEN_ENV = "HOMEOPS_TOOLS_TOKEN"

# Tool arguments cross this, and a code fix can pass a whole file. Generous, but
# not unbounded: an unframed read would let one malformed request consume the
# process.
MAX_REQUEST_BYTES = 4 * 1024 * 1024

READ_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class BridgeHandle:
    """What pi needs in its environment to reach the bridge."""

    socket_path: Path
    token: str

    def env(self) -> dict[str, str]:
        return {SOCKET_ENV: str(self.socket_path), TOKEN_ENV: self.token}


async def _reply(writer: asyncio.StreamWriter, payload: dict) -> None:
    writer.write(json.dumps(payload, default=str).encode("utf-8") + b"\n")
    await writer.drain()


def _manifest(tools: dict[str, ToolDefinition]) -> list[dict[str, Any]]:
    """The tool list pi registers from.

    `input_schema` is passed through untouched. It is JSON Schema, and TypeBox
    schemas are JSON Schema, so pi can use it directly -- which means the schema
    a model sees is the one the handler validates against, rather than a
    hand-written copy that can disagree with it.
    """
    return [
        {"name": t.name, "description": t.description, "parameters": t.input_schema}
        for t in tools.values()
    ]


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    tools: dict[str, ToolDefinition],
    token: str,
) -> None:
    try:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=READ_TIMEOUT_SECONDS)
        except TimeoutError:
            await _reply(writer, {"error": "timed out reading the request"})
            return
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await _reply(writer, {"error": "malformed request"})
            return

        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await _reply(writer, {"error": "request was not JSON"})
            return

        if not hmac.compare_digest(str(request.get("token") or ""), token):
            logger.warning("tool bridge: rejected a request with a bad token")
            await _reply(writer, {"error": "unauthorised"})
            return

        action = request.get("action")
        if action == "list":
            await _reply(writer, {"tools": _manifest(tools)})
            return

        if action != "call":
            await _reply(writer, {"error": f"unsupported action {action!r}"})
            return

        name = str(request.get("tool") or "")
        tool = tools.get(name)
        if tool is None:
            # Named rather than generic: pi registered from this same manifest,
            # so a miss means the two have drifted and that is worth seeing.
            await _reply(writer, {"error": f"unknown tool {name!r}"})
            return

        args = request.get("args")
        if not isinstance(args, dict):
            args = {}

        try:
            result = await tool.handler(args)
        except Exception as exc:
            # Returned, not raised. A failing tool is information the model can
            # act on -- a wrong namespace, a missing PR -- and killing the run
            # over it would turn a recoverable mistake into a dead turn. This
            # mirrors what `core._execute_tool` does for the other backends.
            logger.exception("tool bridge: %s failed", name)
            await _reply(writer, {"result": json.dumps({"error": str(exc)})})
            return

        if not isinstance(result, str):
            result = json.dumps(result, default=str)
        # Writes are recorded by the handlers themselves, which is what makes
        # this path and the two others agree without any of them knowing.
        await _reply(writer, {"result": result})
    except Exception as exc:  # noqa: BLE001 - a bridge fault must not kill the run
        logger.exception("tool bridge: request failed")
        try:
            await _reply(writer, {"error": str(exc)})
        except Exception:
            pass
    finally:
        writer.close()


@asynccontextmanager
async def serve(tools: list[ToolDefinition]) -> AsyncIterator[BridgeHandle]:
    """Serve ``tools`` for the lifetime of the block.

    The socket exists only while the run does, so a token read out of the
    environment is worthless once the run has ended.
    """
    token = secrets.token_urlsafe(32)
    by_name = {t.name: t for t in tools}

    # Its own directory, 0700, so nothing that merely shares /tmp can reach the
    # socket. Short name: the whole path must fit in sockaddr_un.
    directory = Path(tempfile.mkdtemp(prefix="hoa-"))
    os.chmod(directory, 0o700)
    socket_path = directory / "tools.sock"

    server = await asyncio.start_unix_server(
        lambda r, w: _handle(r, w, by_name, token),
        path=str(socket_path),
        limit=MAX_REQUEST_BYTES,
    )
    os.chmod(socket_path, 0o600)
    logger.info("tool bridge serving %d tool(s) on %s", len(by_name), socket_path)

    try:
        yield BridgeHandle(socket_path=socket_path, token=token)
    finally:
        server.close()
        try:
            await server.wait_closed()
        except Exception:
            logger.debug("tool bridge: error closing the server", exc_info=True)
        shutil.rmtree(directory, ignore_errors=True)
