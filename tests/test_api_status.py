"""Tests for api/status.py — REST endpoints for health and status."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_session):
    """Create a test client for the status router."""
    from fastapi import FastAPI

    from home_ops_agent.api.status import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


def test_history_endpoint_empty(client):
    response = client.get("/api/history")
    assert response.status_code == 200
    assert response.json() == []


def test_conversations_endpoint_empty(client):
    response = client.get("/api/conversations")
    assert response.status_code == 200
    assert response.json() == []


def test_memories_endpoint_empty(client):
    response = client.get("/api/memories")
    assert response.status_code == 200
    assert response.json() == []
