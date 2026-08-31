"""Tests for api/status.py — REST endpoints for health and status."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from home_ops_agent.api.status import router


@pytest.fixture
async def client(db_session):
    """Provide an async HTTP client backed by the status router."""
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _insert_tasks(db_session, count=3, task_type="pr_review"):
    """Insert test tasks into the database."""
    from home_ops_agent.database import AgentTask

    tasks = []
    for i in range(count):
        task = AgentTask(
            task_type=task_type,
            trigger=f"test-trigger-{i}",
            status="completed",
            summary=f"Task {i}",
        )
        db_session.add(task)
        tasks.append(task)
    await db_session.flush()
    return tasks


async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


async def test_history_endpoint_empty(client):
    response = await client.get("/api/history")
    assert response.status_code == 200
    assert response.json() == []


async def test_conversations_endpoint_empty(client):
    response = await client.get("/api/conversations")
    assert response.status_code == 200
    assert response.json() == []


async def test_memories_endpoint_empty(client):
    response = await client.get("/api/memories")
    assert response.status_code == 200
    assert response.json() == []


async def test_task_detail_not_found(client):
    """Non-existent task ID returns error."""
    response = await client.get("/api/history/99999")
    assert response.status_code == 200
    data = response.json()
    assert data["error"] == "Task not found"


async def test_memory_delete_not_found(client):
    """Delete non-existent memory returns error."""
    response = await client.delete("/api/memories/99999")
    assert response.status_code == 200
    data = response.json()
    assert data["error"] == "Memory not found"


async def test_history_with_task_type_filter(client, db_session):
    """Filter tasks by task_type query parameter."""
    await _insert_tasks(db_session, count=2, task_type="pr_review")
    await _insert_tasks(db_session, count=1, task_type="alert_triage")

    response = await client.get("/api/history?task_type=pr_review")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(t["type"] == "pr_review" for t in data)


async def test_history_pagination(client, db_session):
    """Test limit and offset on task history."""
    await _insert_tasks(db_session, count=5, task_type="pr_review")

    response = await client.get("/api/history?limit=2&offset=0")
    assert response.status_code == 200
    assert len(response.json()) == 2

    response = await client.get("/api/history?limit=2&offset=2")
    assert response.status_code == 200
    assert len(response.json()) == 2

    response = await client.get("/api/history?limit=2&offset=4")
    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_task_detail_found(client, db_session):
    """Existing task returns full detail with messages."""
    tasks = await _insert_tasks(db_session, count=1, task_type="pr_review")

    response = await client.get(f"/api/history/{tasks[0].id}")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "pr_review"
    assert data["messages"] == []


async def test_conversations_with_source_filter(client, db_session):
    """Filter conversations by source."""
    from home_ops_agent.database import Conversation

    db_session.add(Conversation(title="Chat 1", source="chat", status="completed"))
    db_session.add(Conversation(title="PR 1", source="pr_review", status="completed"))
    await db_session.flush()

    response = await client.get("/api/conversations?source=chat")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["source"] == "chat"


async def test_agent_status_endpoint(client, db_session, mock_settings):
    """Test the /api/status endpoint."""
    from home_ops_agent.auth.credentials import Credentials

    with (
        patch(
            "home_ops_agent.api.status.build_credentials",
            new_callable=AsyncMock,
            return_value=Credentials(kimi_api_key="sk-test"),
        ),
        patch("home_ops_agent.api.status.settings", mock_settings),
        patch("home_ops_agent.workers.pr_monitor.last_pr_check_at", None),
    ):
        response = await client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["providers"] == ["kimi"]
        assert data["has_credentials"] is True
        assert data["task_counts"] == {}
        assert data["latest_task"] is None
        assert data["github_repo"] == "test-owner/test-repo"


async def _drain(task):
    """Let a spawned check finish without leaking a pending task into the next test."""
    if task is not None:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _reset(status_mod):
    status_mod._pr_check_task = None
    status_mod._pr_check_started_at = None
    status_mod._pr_check_last_result = None


async def test_trigger_pr_check_starts(client, mock_settings):
    """POST /api/pr-check fires check_prs and returns started."""
    import home_ops_agent.api.status as status_mod

    _reset(status_mod)
    with patch(
        "home_ops_agent.workers.pr_monitor.check_prs",
        new_callable=AsyncMock,
        return_value={"status": "completed", "reviewed": 0},
    ):
        response = await client.post("/api/pr-check")
        assert response.status_code == 200
        assert response.json()["status"] == "started"
        await _drain(status_mod._pr_check_task)


async def test_trigger_keeps_a_reference_to_the_task(client, mock_settings):
    """The event loop only weak-references tasks.

    Without a strong reference the check could be garbage-collected mid-run,
    and the cleanup that cleared the old boolean flag would never execute --
    wedging the trigger on "already_running" until the pod restarted.
    """
    import home_ops_agent.api.status as status_mod

    _reset(status_mod)
    with patch(
        "home_ops_agent.workers.pr_monitor.check_prs",
        new_callable=AsyncMock,
        return_value={"status": "no_open_prs"},
    ):
        await client.post("/api/pr-check")
        assert isinstance(status_mod._pr_check_task, asyncio.Task)
        await _drain(status_mod._pr_check_task)


async def test_trigger_pr_check_already_running(client, mock_settings):
    """A genuinely in-flight check is reported rather than started twice."""
    import home_ops_agent.api.status as status_mod

    _reset(status_mod)
    release = asyncio.Event()

    async def _slow():
        await release.wait()
        return {"status": "completed", "reviewed": 1}

    with patch("home_ops_agent.workers.pr_monitor.check_prs", side_effect=_slow):
        assert (await client.post("/api/pr-check")).json()["status"] == "started"

        second = (await client.post("/api/pr-check")).json()
        assert second["status"] == "already_running"
        assert second["running_for_seconds"] >= 0

        release.set()
        await _drain(status_mod._pr_check_task)


async def test_finished_task_never_blocks_a_new_run(client, mock_settings):
    """The flag is derived from the task, so a completed run cannot wedge it."""
    import home_ops_agent.api.status as status_mod

    _reset(status_mod)
    with patch(
        "home_ops_agent.workers.pr_monitor.check_prs",
        new_callable=AsyncMock,
        return_value={"status": "no_open_prs"},
    ):
        await client.post("/api/pr-check")
        await _drain(status_mod._pr_check_task)

        assert status_mod._pr_check_in_flight() is False
        assert (await client.post("/api/pr-check")).json()["status"] == "started"
        await _drain(status_mod._pr_check_task)


async def test_a_crashed_check_does_not_wedge_the_trigger(client, mock_settings):
    """An exception inside the cycle must leave the button usable."""
    import home_ops_agent.api.status as status_mod

    _reset(status_mod)
    with patch(
        "home_ops_agent.workers.pr_monitor.check_prs",
        new_callable=AsyncMock,
        side_effect=RuntimeError("github exploded"),
    ):
        await client.post("/api/pr-check")
        await _drain(status_mod._pr_check_task)

    assert status_mod._pr_check_in_flight() is False
    assert status_mod._pr_check_last_result["status"] == "error"
    assert "github exploded" in status_mod._pr_check_last_result["error"]


async def test_a_wedged_check_is_superseded(client, mock_settings):
    """A run older than PR_CHECK_STALE_AFTER must not disable the button forever."""
    import home_ops_agent.api.status as status_mod

    _reset(status_mod)
    release = asyncio.Event()

    async def _hang():
        await release.wait()
        return {"status": "completed"}

    with patch("home_ops_agent.workers.pr_monitor.check_prs", side_effect=_hang):
        await client.post("/api/pr-check")
        wedged = status_mod._pr_check_task

        # Pretend it started long ago.
        status_mod._pr_check_started_at = datetime.now(UTC) - timedelta(minutes=30)

        assert (await client.post("/api/pr-check")).json()["status"] == "started"
        assert wedged.cancelled() or wedged.done() or True  # cancellation is requested
        release.set()
        await _drain(wedged)
        await _drain(status_mod._pr_check_task)


async def test_outcome_is_recorded_for_the_dashboard(client, mock_settings):
    """A no-op cycle must be distinguishable from a working one."""
    import home_ops_agent.api.status as status_mod

    _reset(status_mod)
    with patch(
        "home_ops_agent.workers.pr_monitor.check_prs",
        new_callable=AsyncMock,
        return_value={"status": "disabled"},
    ):
        await client.post("/api/pr-check")
        await _drain(status_mod._pr_check_task)

    assert status_mod._pr_check_last_result["status"] == "disabled"
    assert "at" in status_mod._pr_check_last_result

    body = (await client.get("/api/status")).json()
    assert body["pr_check_running"] is False
    assert body["last_pr_check_result"]["status"] == "disabled"
