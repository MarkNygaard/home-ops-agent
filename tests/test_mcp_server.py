"""Tests for the read-only MCP server.

This exposes the agent's whole task/memory/cost record over one endpoint, so
the two things that matter most are that it stays read-only and that it cannot
be reached without a token.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from home_ops_agent.database import AgentTask, ApiUsage, Conversation, Memory, Message
from home_ops_agent.mcp import server as mcp_server


def _aged(days=0, hours=0):
    return datetime.now(UTC) - timedelta(days=days, hours=hours)


def _mounted_app(monkeypatch):
    monkeypatch.setattr(mcp_server.settings, "mcp_api_token", "secret")
    monkeypatch.setattr(mcp_server.settings, "base_url", "http://testserver")
    monkeypatch.setattr(mcp_server, "_server", None)
    app = FastAPI()
    mcp_server.mount(app)
    return app


HANDSHAKE_HEADERS = {
    "Authorization": "Bearer secret",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}


# --- exposure is opt-in ---


def test_endpoint_is_absent_without_a_token(monkeypatch):
    """No token must mean no endpoint, not an open one."""
    monkeypatch.setattr(mcp_server.settings, "mcp_api_token", "")
    app = FastAPI()

    assert mcp_server.mount(app) is False
    assert not [r for r in app.routes if getattr(r, "path", "") == "/mcp"]


def test_endpoint_is_mounted_with_a_token(monkeypatch):
    app = _mounted_app(monkeypatch)
    assert [r for r in app.routes if getattr(r, "path", "") == "/mcp"]


# --- auth (short-circuits before the MCP app, so no session manager needed) ---


@pytest.fixture
async def mcp_client(monkeypatch):
    app = _mounted_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac


async def test_request_without_a_token_is_rejected(mcp_client):
    resp = await mcp_client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401


async def test_request_with_a_wrong_token_is_rejected(mcp_client):
    resp = await mcp_client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


async def test_a_bare_token_without_the_bearer_scheme_is_rejected(mcp_client):
    resp = await mcp_client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": "secret"},
    )
    assert resp.status_code == 401


# --- the wire ---


async def test_a_real_mcp_handshake_succeeds(monkeypatch):
    """End to end through the mounted endpoint, session manager running.

    Starlette does not run a mounted sub-app's lifespan, so main.py starts the
    session manager itself. Without that every request fails with "Task group
    is not initialized" -- which is what this proves does not happen.
    """
    app = _mounted_app(monkeypatch)
    async with mcp_server.lifespan():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.post("/mcp/", json=INITIALIZE, headers=HANDSHAKE_HEADERS)
            assert resp.status_code == 200
            assert resp.json()["result"]["serverInfo"]["name"] == "home-ops-agent"


async def test_tools_are_listed_over_the_wire(monkeypatch):
    app = _mounted_app(monkeypatch)
    async with mcp_server.lifespan():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            await client.post("/mcp/", json=INITIALIZE, headers=HANDSHAKE_HEADERS)
            resp = await client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                headers=HANDSHAKE_HEADERS,
            )
            assert resp.status_code == 200
            names = sorted(t["name"] for t in resp.json()["result"]["tools"])
            assert names == [
                "agent_status",
                "agent_tasks",
                "costs",
                "memories",
                "task_detail",
            ]


# --- host allowlist ---


def test_ingress_host_is_allowed(monkeypatch):
    """The SDK ships DNS-rebinding protection on with an empty allowlist.

    Left alone that rejects every request, including ones arriving through the
    ingress, so the public host is derived from base_url.
    """
    monkeypatch.setattr(mcp_server.settings, "base_url", "https://agent.mnygaard.io")
    monkeypatch.setattr(mcp_server.settings, "mcp_allowed_hosts", "")

    hosts = mcp_server.allowed_hosts()

    assert "agent.mnygaard.io" in hosts
    assert "localhost" in hosts


def test_extra_hosts_are_configurable(monkeypatch):
    monkeypatch.setattr(mcp_server.settings, "base_url", "https://agent.mnygaard.io")
    monkeypatch.setattr(
        mcp_server.settings,
        "mcp_allowed_hosts",
        "home-ops-agent.automation.svc.cluster.local, other.example ",
    )

    hosts = mcp_server.allowed_hosts()

    assert "home-ops-agent.automation.svc.cluster.local" in hosts
    assert "other.example" in hosts
    assert "" not in hosts


# --- the tool surface stays read-only ---


def test_only_read_tools_are_exposed():
    server = mcp_server.build_server()
    names = sorted(t.name for t in server._tool_manager.list_tools())

    assert names == [
        "agent_status",
        "agent_tasks",
        "costs",
        "memories",
        "task_detail",
    ]


def test_no_tool_can_mutate_anything():
    """A read tool is safe to hand any session; a write tool is a separate call."""
    server = mcp_server.build_server()
    names = [t.name for t in server._tool_manager.list_tools()]

    forbidden = ("create", "delete", "update", "trigger", "run", "set", "write", "merge")
    for name in names:
        assert not any(verb in name for verb in forbidden), f"{name} looks mutating"


def test_every_tool_is_documented():
    """Descriptions are the only thing a caller has to pick the right tool."""
    for tool in mcp_server.build_server()._tool_manager.list_tools():
        assert tool.description and len(tool.description) > 40, tool.name


# --- queries ---


async def test_agent_tasks_lists_recent_work(db_session):
    db_session.add(
        AgentTask(
            task_type="pr_review",
            trigger="PR #1",
            status="completed",
            summary="LGTM",
            actions_taken={"tool_calls": [{"tool": "github_get_pr"}], "tokens": 1200},
            created_at=_aged(hours=1),
        )
    )
    await db_session.flush()

    result = await mcp_server._agent_tasks(None, None, None, 20)

    assert result["count"] == 1
    task = result["tasks"][0]
    assert task["type"] == "pr_review"
    assert task["tool_calls"] == 1
    assert task["tokens"] == 1200


async def test_agent_tasks_filters(db_session):
    db_session.add(
        AgentTask(task_type="pr_review", trigger="a", status="completed", created_at=_aged(hours=1))
    )
    db_session.add(
        AgentTask(task_type="alert_fix", trigger="b", status="failed", created_at=_aged(days=10))
    )
    await db_session.flush()

    assert (await mcp_server._agent_tasks("pr_review", None, None, 20))["count"] == 1
    assert (await mcp_server._agent_tasks(None, "failed", None, 20))["count"] == 1
    # since_hours must exclude the ten-day-old row.
    assert (await mcp_server._agent_tasks(None, None, 24, 20))["count"] == 1


async def test_task_detail_returns_the_tool_trace(db_session):
    """The point of this tool: see what the agent actually called."""
    conversation = Conversation(title="Review PR #1", source="pr_review", status="completed")
    db_session.add(conversation)
    await db_session.flush()

    db_session.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content={
                "text": "Looks safe.",
                "tool_calls": [
                    {"tool": "github_get_pr", "input": {"pr_number": 1}, "result": "ok"}
                ],
            },
        )
    )
    task = AgentTask(
        task_type="pr_review",
        trigger="PR #1",
        status="completed",
        conversation_id=conversation.id,
        summary="LGTM",
    )
    db_session.add(task)
    await db_session.flush()

    result = await mcp_server._task_detail(task.id)

    assert result["type"] == "pr_review"
    assert result["conversation"]["title"] == "Review PR #1"
    call = result["messages"][0]["tool_calls"][0]
    assert call["tool"] == "github_get_pr"
    assert call["input"] == {"pr_number": 1}


async def test_task_detail_truncates_huge_tool_output(db_session):
    """An alert investigation can carry enormous results; don't swamp the caller."""
    conversation = Conversation(title="c", source="alert_fix", status="completed")
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content={"tool_calls": [{"tool": "loki_query", "input": {}, "result": "x" * 50000}]},
        )
    )
    task = AgentTask(
        task_type="alert_fix", trigger="t", status="completed", conversation_id=conversation.id
    )
    db_session.add(task)
    await db_session.flush()

    result = await mcp_server._task_detail(task.id)
    rendered = result["messages"][0]["tool_calls"][0]["result"]

    assert len(rendered) < 50000
    assert "truncated" in rendered


async def test_task_detail_handles_a_missing_task(db_session):
    assert "error" in await mcp_server._task_detail(999999)


async def test_memories_flags_stale_and_hand_written(db_session):
    db_session.add(Memory(content="old incident", category="issue", created_at=_aged(days=30)))
    db_session.add(
        Memory(content="architectural fact", category="knowledge", created_at=_aged(days=200))
    )
    await db_session.flush()

    result = await mcp_server._memories(None, 50)
    by_content = {m["content"]: m for m in result["memories"]}

    # A stale entry is withheld from agent prompts; say so rather than implying
    # the agent can still see it.
    assert by_content["old incident"]["in_prompt"] is False
    assert by_content["architectural fact"]["in_prompt"] is True
    assert by_content["architectural fact"]["hand_written"] is True


async def test_costs_aggregates_by_model(db_session):
    db_session.add(
        ApiUsage(
            model="claude-sonnet-4-6",
            task_type="pr_review",
            input_tokens=1000,
            output_tokens=200,
            cost_usd=0.006,
            created_at=_aged(days=1),
        )
    )
    db_session.add(
        ApiUsage(
            model="claude-code/sonnet",
            task_type="code_fix",
            input_tokens=5000,
            output_tokens=900,
            cost_usd=0.0,
            created_at=_aged(days=1),
        )
    )
    await db_session.flush()

    result = await mcp_server._costs(30)

    assert result["total_requests"] == 2
    models = {m["model"]: m for m in result["by_model"]}
    # Subscription-billed runs record $0 — requests without cost is expected.
    assert models["claude-code/sonnet"]["cost_usd"] == 0.0
    assert models["claude-sonnet-4-6"]["cost_usd"] == 0.006


async def test_costs_excludes_rows_outside_the_window(db_session):
    db_session.add(
        ApiUsage(
            model="m",
            task_type="chat",
            input_tokens=1,
            output_tokens=1,
            cost_usd=1.0,
            created_at=_aged(days=90),
        )
    )
    await db_session.flush()

    assert (await mcp_server._costs(7))["total_requests"] == 0


async def test_results_are_json_serialisable(db_session):
    """Tools return strings to the model, so every payload must encode."""
    db_session.add(AgentTask(task_type="user_chat", trigger="t", status="completed"))
    await db_session.flush()

    json.dumps(await mcp_server._agent_tasks(None, None, None, 5), default=str)
    json.dumps(await mcp_server._memories(None, 5), default=str)
    json.dumps(await mcp_server._costs(7), default=str)
