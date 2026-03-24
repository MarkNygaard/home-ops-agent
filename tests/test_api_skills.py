"""Tests for api/skills.py — skill management API."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from home_ops_agent.agent.skills import SkillDefinition, SkillRegistry


def test_skill_api_builtin_cannot_disable():
    """Verify the builtin check logic used in the skills API endpoint."""
    skill = SkillDefinition(id="k8s", name="K8s", description="", builtin=True)
    # The API checks: if skill.builtin and body.enabled is False
    assert skill.builtin is True
    # This would return an error in the API


def test_skill_api_unknown_skill():
    """Verify the unknown skill check logic."""
    reg = SkillRegistry()
    assert reg.get("nonexistent") is None
    # The API checks: if skill is None: return {"error": ...}


def test_skill_api_update_model():
    """Verify the UpdateSkill model accepts correct values."""
    from home_ops_agent.api.skills import UpdateSkill

    body = UpdateSkill(enabled=True, config={"url": "http://prom:9090"})
    assert body.enabled is True
    assert body.config == {"url": "http://prom:9090"}


def test_skill_api_update_model_optional_fields():
    from home_ops_agent.api.skills import UpdateSkill

    body_none = UpdateSkill()
    assert body_none.enabled is None
    assert body_none.config is None

    body_partial = UpdateSkill(enabled=False)
    assert body_partial.enabled is False
    assert body_partial.config is None


# --- API endpoint integration tests ---


def _make_test_registry():
    """Create a registry with test skills for endpoint testing."""
    reg = SkillRegistry()
    reg.register(
        SkillDefinition(
            id="test_builtin",
            name="Test Builtin",
            description="A built-in test skill",
            builtin=True,
            get_tools=lambda config: [],
        )
    )
    reg.register(
        SkillDefinition(
            id="test_optional",
            name="Test Optional",
            description="An optional test skill",
            builtin=False,
            config_fields=[{"key": "url", "label": "URL", "default": "http://localhost"}],
            get_tools=lambda config: [],
        )
    )
    return reg


@pytest.fixture
def skills_client(db_session):
    """Create a test client with a patched skill registry."""
    from fastapi import FastAPI

    from home_ops_agent.api.skills import router

    app = FastAPI()
    app.include_router(router)

    test_reg = _make_test_registry()
    with patch("home_ops_agent.api.skills.registry", test_reg):
        yield TestClient(app), test_reg


def test_list_skills_endpoint(skills_client):
    """GET /api/skills returns all registered skills."""
    client, _ = skills_client
    response = client.get("/api/skills")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    ids = {s["id"] for s in data}
    assert "test_builtin" in ids
    assert "test_optional" in ids

    # Builtin is always enabled
    builtin = next(s for s in data if s["id"] == "test_builtin")
    assert builtin["enabled"] is True
    assert builtin["builtin"] is True

    # Optional defaults to disabled
    optional = next(s for s in data if s["id"] == "test_optional")
    assert optional["enabled"] is False
    assert optional["builtin"] is False


def test_update_skill_enable_optional(skills_client):
    """PUT /api/skills/{id} enables an optional skill."""
    client, _ = skills_client
    response = client.put("/api/skills/test_optional", json={"enabled": True})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["skill"]["enabled"] is True


def test_update_skill_with_config(skills_client):
    """PUT /api/skills/{id} updates config."""
    client, _ = skills_client
    response = client.put(
        "/api/skills/test_optional",
        json={"enabled": True, "config": {"url": "http://prom:9090"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["skill"]["config"]["url"] == "http://prom:9090"


def test_update_skill_unknown(skills_client):
    """PUT /api/skills/{id} with unknown ID returns error."""
    client, _ = skills_client
    response = client.put("/api/skills/nonexistent", json={"enabled": True})
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "Unknown skill" in data["error"]


def test_update_skill_disable_builtin(skills_client):
    """PUT /api/skills/{id} cannot disable a built-in skill."""
    client, _ = skills_client
    response = client.put("/api/skills/test_builtin", json={"enabled": False})
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "Cannot disable built-in" in data["error"]
