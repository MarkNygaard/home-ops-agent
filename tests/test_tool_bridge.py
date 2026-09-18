"""Tests for the tool bridge — how pi reaches the agent's registry.

Every tool call on that backend crosses this, so the cases that matter are the
refusals and the ones where a failure must not take the run with it.
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

from home_ops_agent.agent import tool_bridge  # noqa: E402
from home_ops_agent.agent.core import ToolDefinition  # noqa: E402


def _tool(name="k8s_get_pods", handler=None, schema=None):
    async def _default(params):
        return json.dumps({"called": name, "params": params})

    return ToolDefinition(
        name=name,
        description=f"{name} does a thing. And more detail after the first sentence.",
        input_schema=schema or {"type": "object", "properties": {"ns": {"type": "string"}}},
        handler=handler or _default,
    )


async def _ask(handle, payload) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(handle.socket_path))
    writer.write(json.dumps(payload).encode() + b"\n")
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=5)
    writer.close()
    return json.loads(line)


@pytest.mark.asyncio
async def test_the_manifest_carries_the_schema_untouched():
    """pi registers from this, so the schema a model is shown is the one the
    handler is written against — not a copy that can disagree with it."""
    schema = {
        "type": "object",
        "properties": {"pr_number": {"type": "integer"}},
        "required": ["pr_number"],
    }
    async with tool_bridge.serve([_tool("github_get_pr", schema=schema)]) as handle:
        reply = await _ask(handle, {"token": handle.token, "action": "list"})

    assert [t["name"] for t in reply["tools"]] == ["github_get_pr"]
    assert reply["tools"][0]["parameters"] == schema


@pytest.mark.asyncio
async def test_a_call_reaches_the_handler_with_its_arguments():
    async with tool_bridge.serve([_tool()]) as handle:
        reply = await _ask(
            handle,
            {
                "token": handle.token,
                "action": "call",
                "tool": "k8s_get_pods",
                "args": {"ns": "media"},
            },
        )

    assert json.loads(reply["result"]) == {"called": "k8s_get_pods", "params": {"ns": "media"}}


@pytest.mark.asyncio
async def test_a_wrong_token_reaches_nothing():
    called = False

    async def _handler(_params):
        nonlocal called
        called = True
        return "x"

    async with tool_bridge.serve([_tool(handler=_handler)]) as handle:
        listed = await _ask(handle, {"token": "guessed", "action": "list"})
        called_reply = await _ask(
            handle, {"token": "guessed", "action": "call", "tool": "k8s_get_pods", "args": {}}
        )

    assert listed["error"] == "unauthorised"
    assert called_reply["error"] == "unauthorised"
    assert called is False


@pytest.mark.asyncio
async def test_only_the_tools_it_was_handed_exist():
    """The bridge decides nothing about which tools an agent gets.

    Enabled skills, the tools withheld from the PR agent, the workspace tools —
    all of that is the caller's, made in the same place for both backends. A
    bridge that could reach the whole registry regardless would quietly undo it.
    """
    async with tool_bridge.serve([_tool("k8s_get_pods")]) as handle:
        reply = await _ask(
            handle, {"token": handle.token, "action": "call", "tool": "github_merge_pr", "args": {}}
        )

    assert "unknown tool" in reply["error"]


@pytest.mark.asyncio
async def test_a_failing_tool_is_reported_not_raised():
    """A failing tool is information the model can act on — a wrong namespace, a
    missing PR. Killing the run over it turns a recoverable mistake into a dead
    turn, and the other backends do not."""

    async def _boom(_params):
        raise RuntimeError("namespace 'nope' not found")

    async with tool_bridge.serve([_tool(handler=_boom)]) as handle:
        reply = await _ask(
            handle, {"token": handle.token, "action": "call", "tool": "k8s_get_pods", "args": {}}
        )

    assert "nope" in json.loads(reply["result"])["error"]


@pytest.mark.asyncio
async def test_a_non_string_result_is_serialised():
    """Handlers return dicts as well as strings; pi only takes text."""

    async def _dict(_params):
        return {"pods": 3}

    async with tool_bridge.serve([_tool(handler=_dict)]) as handle:
        reply = await _ask(
            handle, {"token": handle.token, "action": "call", "tool": "k8s_get_pods", "args": {}}
        )

    assert json.loads(reply["result"]) == {"pods": 3}


@pytest.mark.asyncio
async def test_missing_or_malformed_arguments_do_not_crash_the_call():
    captured = {}

    async def _handler(params):
        captured["params"] = params
        return "ok"

    async with tool_bridge.serve([_tool(handler=_handler)]) as handle:
        await _ask(handle, {"token": handle.token, "action": "call", "tool": "k8s_get_pods"})

    assert captured["params"] == {}


@pytest.mark.asyncio
async def test_garbage_is_answered_rather_than_crashing():
    async with tool_bridge.serve([_tool()]) as handle:
        reader, writer = await asyncio.open_unix_connection(str(handle.socket_path))
        writer.write(b"not json at all\n")
        await writer.drain()
        reply = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
        writer.close()

    assert "not JSON" in reply["error"]


@pytest.mark.asyncio
async def test_an_unsupported_action_is_refused():
    async with tool_bridge.serve([_tool()]) as handle:
        reply = await _ask(handle, {"token": handle.token, "action": "exec"})
    assert "unsupported action" in reply["error"]


@pytest.mark.asyncio
async def test_the_socket_is_private_and_does_not_outlive_the_run():
    async with tool_bridge.serve([_tool()]) as handle:
        assert os.stat(handle.socket_path).st_mode & 0o777 == 0o600
        assert os.stat(handle.socket_path.parent).st_mode & 0o777 == 0o700
        path = handle.socket_path

    assert not path.exists()
    with pytest.raises((ConnectionRefusedError, FileNotFoundError, OSError)):
        await asyncio.open_unix_connection(str(path))


@pytest.mark.asyncio
async def test_each_run_gets_a_different_token():
    async with tool_bridge.serve([_tool()]) as a, tool_bridge.serve([_tool()]) as b:
        assert a.token != b.token
        assert a.socket_path != b.socket_path


def test_the_env_carries_only_the_socket_and_its_token():
    """Whatever goes into pi's environment is readable by the model via `bash`.

    The token authorises exactly the tools the model already has, so reading it
    gains nothing. A credential would not have that property, which is the whole
    reason the registry is not reimplemented on the other side.
    """
    from pathlib import Path

    handle = tool_bridge.BridgeHandle(socket_path=Path("/tmp/x"), token="t")
    assert set(handle.env()) == {tool_bridge.SOCKET_ENV, tool_bridge.TOKEN_ENV}
