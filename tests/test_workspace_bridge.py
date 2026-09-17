"""Tests for the workspace bridge — the one callback pi is given.

This is the only thing standing between a model with a `bash` tool and an
unguarded `git push`, so the cases that matter are the refusals: a wrong token,
an action that is not `commit`, a request that is not JSON. The happy path is
covered too, but it is the least interesting part.

`commit_and_push` itself is stubbed throughout. Its guardrails are tested in
`test_workspace.py`; what is under test here is whether a request reaches it at
all, and what comes back.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the bridge is a Unix socket; the agent runs on Linux"
)

from home_ops_agent.agent import workspace_bridge  # noqa: E402
from home_ops_agent.agent.workspace import Workspace  # noqa: E402


def _workspace(tmp_path) -> Workspace:
    return Workspace(path=tmp_path, branch="renovate/some-chart", token="gh-token")


async def _ask(handle: workspace_bridge.BridgeHandle, payload: dict) -> dict:
    """One request, one reply — the entire protocol."""
    reader, writer = await asyncio.open_unix_connection(str(handle.socket_path))
    writer.write(json.dumps(payload).encode() + b"\n")
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=5)
    writer.close()
    return json.loads(line)


@pytest.mark.asyncio
async def test_commit_reaches_the_guarded_push(monkeypatch, tmp_path):
    seen = {}

    async def _commit(ws, message):
        seen["branch"] = ws.branch
        seen["message"] = message
        return {"status": "ok", "branch": ws.branch, "sha": "abc123", "files": ["a.yaml"]}

    monkeypatch.setattr(workspace_bridge, "commit_and_push", _commit)

    async with workspace_bridge.serve(_workspace(tmp_path)) as handle:
        reply = await _ask(
            handle, {"token": handle.token, "action": "commit", "message": "fix the chart"}
        )

    assert reply["status"] == "ok"
    assert seen["branch"] == "renovate/some-chart"
    assert seen["message"] == "fix the chart"


@pytest.mark.asyncio
async def test_a_wrong_token_never_reaches_commit(monkeypatch, tmp_path):
    """The socket is local, but the token is what makes it single-purpose.

    Anything else in the pod that can see the socket must not be able to push.
    """
    called = False

    async def _commit(_ws, _message):
        nonlocal called
        called = True
        return {"status": "ok"}

    monkeypatch.setattr(workspace_bridge, "commit_and_push", _commit)

    async with workspace_bridge.serve(_workspace(tmp_path)) as handle:
        reply = await _ask(handle, {"token": "guessed", "action": "commit", "message": "x"})

    assert reply["status"] == "failed"
    assert reply["error"] == "unauthorised"
    assert called is False


@pytest.mark.asyncio
async def test_no_action_other_than_commit(monkeypatch, tmp_path):
    """The bridge exists to expose one operation.

    If it ever grows a second, that must be a deliberate edit here rather than
    something a caller can reach by naming it.
    """
    monkeypatch.setattr(
        workspace_bridge, "commit_and_push", lambda *_a: pytest.fail("must not be called")
    )

    async with workspace_bridge.serve(_workspace(tmp_path)) as handle:
        for action in ("push", "checkout", "exec", None):
            reply = await _ask(handle, {"token": handle.token, "action": action, "message": "x"})
            assert reply["status"] == "failed", action
            assert "unsupported action" in reply["error"], action


@pytest.mark.asyncio
async def test_an_empty_message_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workspace_bridge, "commit_and_push", lambda *_a: pytest.fail("must not be called")
    )

    async with workspace_bridge.serve(_workspace(tmp_path)) as handle:
        reply = await _ask(handle, {"token": handle.token, "action": "commit", "message": "   "})

    assert reply["status"] == "failed"
    assert "commit message is required" in reply["error"]


@pytest.mark.asyncio
async def test_garbage_is_answered_not_crashed(monkeypatch, tmp_path):
    """A malformed request must fail the call, not the run."""
    monkeypatch.setattr(
        workspace_bridge, "commit_and_push", lambda *_a: pytest.fail("must not be called")
    )

    async with workspace_bridge.serve(_workspace(tmp_path)) as handle:
        reader, writer = await asyncio.open_unix_connection(str(handle.socket_path))
        writer.write(b"not json at all\n")
        await writer.drain()
        reply = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
        writer.close()

    assert reply["status"] == "failed"
    assert "not JSON" in reply["error"]


@pytest.mark.asyncio
async def test_a_blocked_commit_is_passed_through_verbatim(monkeypatch, tmp_path):
    """The rejected paths have to survive the hop.

    Without them the model is told "blocked" with nothing to act on, and the
    guardrail turns a recoverable mistake into a failed run.
    """

    async def _commit(_ws, _message):
        return {
            "status": "blocked",
            "error": "BLOCKED: 1 path(s) outside the allowed prefixes.",
            "blocked_paths": ["talos/talconfig.yaml"],
        }

    monkeypatch.setattr(workspace_bridge, "commit_and_push", _commit)

    async with workspace_bridge.serve(_workspace(tmp_path)) as handle:
        reply = await _ask(handle, {"token": handle.token, "action": "commit", "message": "x"})

    assert reply["status"] == "blocked"
    assert reply["blocked_paths"] == ["talos/talconfig.yaml"]


@pytest.mark.asyncio
async def test_the_socket_is_private_and_goes_away(tmp_path):
    """A leaked token must be worthless once the run is over."""
    async with workspace_bridge.serve(_workspace(tmp_path)) as handle:
        assert handle.socket_path.exists()
        # 0600 on the socket, 0700 on its directory: nothing that merely shares
        # /tmp can connect.
        assert os.stat(handle.socket_path).st_mode & 0o777 == 0o600
        assert os.stat(handle.socket_path.parent).st_mode & 0o777 == 0o700
        path = handle.socket_path

    assert not path.exists()
    with pytest.raises((ConnectionRefusedError, FileNotFoundError, OSError)):
        await asyncio.open_unix_connection(str(path))


@pytest.mark.asyncio
async def test_each_run_gets_a_different_token(tmp_path):
    async with workspace_bridge.serve(_workspace(tmp_path)) as a:
        async with workspace_bridge.serve(_workspace(tmp_path)) as b:
            assert a.token != b.token
            assert a.socket_path != b.socket_path


def test_the_env_carries_only_the_socket_and_its_token():
    """Whatever goes into pi's environment is readable by the model via `bash`.

    A GitHub token here would let it push directly and bypass the guardrails
    entirely, which is the entire reason this module exists.
    """
    handle = workspace_bridge.BridgeHandle(
        socket_path=__import__("pathlib").Path("/tmp/x"), token="t"
    )
    assert set(handle.env()) == {workspace_bridge.SOCKET_ENV, workspace_bridge.TOKEN_ENV}
