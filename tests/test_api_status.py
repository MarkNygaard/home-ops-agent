"""Tests for api/status.py — REST endpoints for health and status."""

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
    with (
        patch(
            "home_ops_agent.api.status.get_auth_method",
            new_callable=AsyncMock,
            return_value="api_key",
        ),
        patch(
            "home_ops_agent.api.status.get_valid_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("home_ops_agent.api.status.settings", mock_settings),
        patch("home_ops_agent.workers.pr_monitor.last_pr_check_at", None),
    ):
        response = await client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["auth_method"] == "api_key"
        assert data["has_credentials"] is True
        assert data["task_counts"] == {}
        assert data["latest_task"] is None
        assert data["github_repo"] == "test-owner/test-repo"
