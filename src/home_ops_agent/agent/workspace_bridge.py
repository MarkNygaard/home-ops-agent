"""The one callback pi is given: commit and push a workspace.

`extensions/README.md` argues against bridging tools back into the Python
process, and that argument still holds for every tool there is a way to write
natively. This is the exception, and the reason is the credential.

`workspace_commit` needs the GitHub push token. pi has a `bash` tool, so
anything in its environment or on its filesystem is readable by the model --
which means a native TypeScript `workspace_commit` would have to be handed the
token, and the model could then simply run `git push` itself and walk straight
past `ALLOWED_COMMIT_PATHS` and `PROTECTED_BRANCHES`. The guardrail would still
be there; it would just no longer be the only way out.

`workspace.py` already anticipated this on the Claude Code side: the token is
passed per git invocation and scrubbed from `.git/config` after clone, so a
shell inside the worktree cannot read it back. Keeping the push in Python keeps
that true for pi as well.

So pi is given a channel, not a credential:

- a Unix socket, created per run, mode 0600, in a private directory
- a random token, valid only for that run
- exactly one action, bound to exactly one already-open `Workspace`

The token *is* readable by the model via `bash`, and that is fine: the only
thing it can be used for is invoking the guarded commit, which is the tool the
model already has. It grants no capability the model did not already possess,
which is the property that makes handing it over acceptable where handing over
the push token would not be.

A Unix socket rather than a loopback port: it cannot be reached from outside
the pod even by accident, it carries filesystem permissions, and it needs no
HTTP parsing -- one JSON line in, one JSON line out.
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

from home_ops_agent.agent.workspace import Workspace, commit_and_push

logger = logging.getLogger(__name__)

SOCKET_ENV = "HOMEOPS_WORKSPACE_SOCKET"
TOKEN_ENV = "HOMEOPS_WORKSPACE_TOKEN"

# A commit message, and nothing else, ever crosses this. Unix socket paths are
# themselves limited to ~108 bytes, which is why the directory name is short.
MAX_REQUEST_BYTES = 64 * 1024

# A model that opens the socket and then says nothing must not hold the run
# open. Generous, because the caller is a local process and the only thing it
# has to do is write one line.
READ_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class BridgeHandle:
    """What pi needs in its environment to reach the bridge."""

    socket_path: Path
    token: str

    def env(self) -> dict[str, str]:
        return {SOCKET_ENV: str(self.socket_path), TOKEN_ENV: self.token}


async def _reply(writer: asyncio.StreamWriter, payload: dict) -> None:
    writer.write(json.dumps(payload).encode("utf-8") + b"\n")
    await writer.drain()


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ws: Workspace,
    token: str,
) -> None:
    try:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=READ_TIMEOUT_SECONDS)
        except TimeoutError:
            await _reply(writer, {"status": "failed", "error": "timed out reading the request"})
            return
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await _reply(writer, {"status": "failed", "error": "malformed request"})
            return

        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await _reply(writer, {"status": "failed", "error": "request was not JSON"})
            return

        # compare_digest rather than ==: the token is short-lived and local, but
        # a timing-safe compare costs nothing and stops this being the one place
        # that teaches the wrong habit.
        if not hmac.compare_digest(str(request.get("token") or ""), token):
            logger.warning("workspace bridge: rejected a request with a bad token")
            await _reply(writer, {"status": "failed", "error": "unauthorised"})
            return

        if request.get("action") != "commit":
            await _reply(
                writer,
                {"status": "failed", "error": f"unsupported action {request.get('action')!r}"},
            )
            return

        message = str(request.get("message") or "").strip()
        if not message:
            await _reply(writer, {"status": "failed", "error": "A commit message is required."})
            return

        # The guardrails live here, unchanged and shared with the Claude Code
        # path: protected branches, and every staged path under
        # ALLOWED_COMMIT_PATHS.
        result = await commit_and_push(ws, message)
        await _reply(writer, result)
    except Exception as exc:  # noqa: BLE001 - a bridge fault must not kill the run
        logger.exception("workspace bridge: request failed")
        try:
            await _reply(writer, {"status": "failed", "error": str(exc)})
        except Exception:
            pass
    finally:
        writer.close()


@asynccontextmanager
async def serve(ws: Workspace) -> AsyncIterator[BridgeHandle]:
    """Run the bridge for the lifetime of the block.

    The socket exists only while the run does, so a leaked token buys nothing
    once the run has ended.
    """
    token = secrets.token_urlsafe(32)
    # Its own directory, 0700, so the socket cannot be reached by anything that
    # merely shares /tmp. Short name: the whole path must fit in sockaddr_un.
    directory = Path(tempfile.mkdtemp(prefix="hoa-"))
    os.chmod(directory, 0o700)
    socket_path = directory / "ws.sock"

    server = await asyncio.start_unix_server(
        lambda r, w: _handle(r, w, ws, token),
        path=str(socket_path),
        limit=MAX_REQUEST_BYTES,
    )
    os.chmod(socket_path, 0o600)
    logger.info("workspace bridge listening on %s for branch '%s'", socket_path, ws.branch)

    try:
        yield BridgeHandle(socket_path=socket_path, token=token)
    finally:
        server.close()
        try:
            await server.wait_closed()
        except Exception:
            logger.debug("workspace bridge: error closing the server", exc_info=True)
        shutil.rmtree(directory, ignore_errors=True)
