"""Tests for the MCP server.

This exposes the agent's whole task/memory/cost record over one endpoint, so
the things that matter most are that it cannot be reached without a token and
that its write surface stays exactly the two memory tools it is meant to have.
"""

import json
import pathlib
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
                "conversation_detail",
                "conversations",
                "costs",
                "create_memory",
                "delete_memory",
                "memories",
                "task_detail",
            ]


# --- host allowlist ---


def test_ingress_host_is_allowed(monkeypatch):
    """The SDK ships DNS-rebinding protection on with an empty allowlist.

    Left alone that rejects every request, including ones arriving through the
    ingress, so the public host is derived from base_url.
    """
    monkeypatch.setattr(mcp_server.settings, "base_url", "https://agent.example.com")
    monkeypatch.setattr(mcp_server.settings, "mcp_allowed_hosts", "")

    hosts = mcp_server.allowed_hosts()

    assert "agent.example.com" in hosts
    assert "localhost" in hosts


def test_extra_hosts_are_configurable(monkeypatch):
    monkeypatch.setattr(mcp_server.settings, "base_url", "https://agent.example.com")
    monkeypatch.setattr(
        mcp_server.settings,
        "mcp_allowed_hosts",
        "home-ops-agent.automation.svc.cluster.local, other.example ",
    )

    hosts = mcp_server.allowed_hosts()

    assert "home-ops-agent.automation.svc.cluster.local" in hosts
    assert "other.example" in hosts
    assert "" not in hosts


# --- the tool surface ---


def test_exposed_tools():
    server = mcp_server.build_server()
    names = sorted(t.name for t in server._tool_manager.list_tools())

    assert names == [
        "agent_status",
        "agent_tasks",
        "conversation_detail",
        "conversations",
        "costs",
        "create_memory",
        "delete_memory",
        "memories",
        "task_detail",
    ]


def test_write_surface_is_exactly_two_tools():
    """The write surface is pinned, so widening it has to be deliberate.

    Memories reach every future system prompt, including agents that can commit
    to the repo and restart pods, and they are instruction-shaped. Two narrow,
    reversible memory tools are the considered exception; a third write tool
    should break this test rather than slip in.
    """
    server = mcp_server.build_server()
    names = {t.name for t in server._tool_manager.list_tools()}

    assert names & {"create_memory", "delete_memory"} == {"create_memory", "delete_memory"}

    # Nothing may change settings, models, prompts, or start work.
    forbidden = ("setting", "model", "prompt", "trigger", "check", "merge", "restart", "commit")
    for name in names:
        assert not any(verb in name for verb in forbidden), f"{name} exceeds the write surface"


def test_no_bulk_deletion_is_possible():
    """Deleting is one memory at a time, by id, so a mistake is small and visible."""
    server = mcp_server.build_server()
    delete = next(t for t in server._tool_manager.list_tools() if t.name == "delete_memory")

    params = delete.parameters["properties"]
    assert set(params) == {"memory_id"}


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


# --- conversations (chats produce no agent_tasks row, so only these see them) ---


async def test_conversations_include_chats(db_session):
    """A chat creates a Conversation but no AgentTask.

    agent_tasks alone therefore misses every chat; this is the tool that does
    not.
    """
    db_session.add(Conversation(title="What is broken?", source="chat", status="completed"))
    db_session.add(Conversation(title="Review PR #4", source="pr_review", status="completed"))
    await db_session.flush()

    result = await mcp_server._conversations(None, 20)

    assert result["count"] == 2
    assert {c["source"] for c in result["conversations"]} == {"chat", "pr_review"}


async def test_conversations_filter_by_source_and_count_messages(db_session):
    conversation = Conversation(title="chat", source="chat", status="completed")
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(Message(conversation_id=conversation.id, role="user", content={"text": "hi"}))
    db_session.add(
        Message(conversation_id=conversation.id, role="assistant", content={"text": "hello"})
    )
    db_session.add(Conversation(title="pr", source="pr_review", status="completed"))
    await db_session.flush()

    result = await mcp_server._conversations("chat", 20)

    assert result["count"] == 1
    assert result["conversations"][0]["messages"] == 2


async def test_conversation_detail_returns_messages(db_session):
    conversation = Conversation(title="chat", source="chat", status="completed")
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content={
                "text": "Checked the pods.",
                "tool_calls": [{"tool": "list_pods", "input": {}, "result": "3 running"}],
            },
        )
    )
    await db_session.flush()

    result = await mcp_server._conversation_detail(conversation.id)

    assert result["source"] == "chat"
    assert result["messages"][0]["text"] == "Checked the pods."
    assert result["messages"][0]["tool_calls"][0]["tool"] == "list_pods"


async def test_conversation_detail_handles_a_missing_id(db_session):
    assert "error" in await mcp_server._conversation_detail(999999)


# --- memory writes ---


async def test_create_memory_reaches_the_prompt(db_session):
    """The point of the tool: a fact written here must actually be used."""
    from home_ops_agent.agent import memory as mem_mod

    result = await mcp_server._create_memory(
        "ntfy runs behind a local-path PVC pinned to k8s-1", "knowledge"
    )

    assert result["status"] == "created"
    assert "local-path PVC pinned to k8s-1" in await mem_mod.load_memories()


async def test_create_memory_defaults_to_knowledge(db_session):
    result = await mcp_server._create_memory("a durable fact", "knowledge")
    assert result["category"] == "knowledge"


async def test_create_memory_rejects_empty_and_unknown_category(db_session):
    assert "must not be empty" in (await mcp_server._create_memory("   ", "knowledge"))["error"]
    assert "category must be one of" in (await mcp_server._create_memory("x", "nope"))["error"]


async def test_create_memory_rejects_duplicates(db_session):
    assert (await mcp_server._create_memory("the same fact", "knowledge"))["status"] == "created"
    assert (
        "already exists" in (await mcp_server._create_memory("the same fact", "knowledge"))["error"]
    )


async def test_delete_memory_echoes_what_it_removed(db_session):
    """Removing something that shapes every prompt should leave a record."""
    created = await mcp_server._create_memory("a fact to remove", "knowledge")

    result = await mcp_server._delete_memory(created["id"])

    assert result["status"] == "deleted"
    assert result["removed"]["content"] == "a fact to remove"
    assert (await mcp_server._memories(None, 50))["count"] == 0


async def test_delete_memory_handles_a_missing_id(db_session):
    assert "error" in await mcp_server._delete_memory(999999)


# --- the crash that took the operator offline ---


def test_mcp_major_version_is_pinned():
    """An unpinned `mcp` let the image ship 2.x while tests ran 1.26.

    mcp 2.x removed `mcp.server.fastmcp` and dropped the MCPServer kwargs this
    module depends on (stateless_http, json_response, streamable_http_path,
    transport_security), so a bump is a migration, not a version change.
    """
    import tomllib

    pyproject = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
    mcp_dep = next(d for d in pyproject["project"]["dependencies"] if d.startswith("mcp"))

    assert "<2" in mcp_dep, f"mcp must stay below 2.x until migrated (found {mcp_dep!r})"


def test_image_builds_from_the_lock_file():
    """The image resolved dependencies fresh, so it could ship what tests never ran.

    Copying uv.lock and installing the exported set is what makes the image and
    the test environment the same.
    """
    dockerfile = pathlib.Path("Dockerfile").read_text(encoding="utf-8")

    assert "uv.lock" in dockerfile
    assert "--frozen" in dockerfile


def test_a_broken_mcp_sdk_does_not_take_the_app_down(monkeypatch):
    """The operator must survive a broken optional endpoint.

    When mcp 2.x landed in the image, build_server() raised at startup and the
    whole pod entered CrashLoopBackOff -- PR reviews and alert handling went
    down with an inspection endpoint.
    """
    monkeypatch.setattr(mcp_server.settings, "mcp_api_token", "secret")
    monkeypatch.setattr(mcp_server, "_server", None)

    def _boom():
        raise ModuleNotFoundError("No module named 'mcp.server.fastmcp'")

    monkeypatch.setattr(mcp_server, "build_server", _boom)
    app = FastAPI()

    # Must not raise, and must report that it did not mount.
    assert mcp_server.mount(app) is False
    assert not [r for r in app.routes if getattr(r, "path", "") == "/mcp"]


async def test_lifespan_is_a_noop_when_mounting_failed(monkeypatch):
    """A failed mount leaves no server, so the lifespan must still start cleanly."""
    monkeypatch.setattr(mcp_server, "_server", None)
    async with mcp_server.lifespan():
        pass


async def test_bare_mcp_path_redirects_instead_of_405(monkeypatch):
    """A client configured without the trailing slash must still work.

    The static catch-all matches `/mcp`, so Starlette's usual redirect-to-slash
    never fires and a POST landed on a GET-only route as a 405. That cost a
    full debugging round-trip.
    """
    monkeypatch.setattr(mcp_server.settings, "mcp_api_token", "secret")
    monkeypatch.setattr(mcp_server.settings, "base_url", "http://testserver")
    monkeypatch.setattr(mcp_server, "_server", None)

    from home_ops_agent.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            follow_redirects=False,
        )

    assert resp.status_code == 307, f"expected a redirect, got {resp.status_code}"
    assert resp.headers["location"].endswith("/mcp/")
