"""Tests for api/settings.py — settings management and API key masking."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from home_ops_agent.api.settings import ALLOWED_SETTING_KEYS, _mask_key

# --- _mask_key() pure function tests ---


def test_mask_key_empty():
    assert _mask_key("") == ""


def test_mask_key_short():
    assert _mask_key("abcdefgh") == "****"


def test_mask_key_very_short():
    assert _mask_key("abc") == "****"


def test_mask_key_long():
    key = "sk-ant-api03-abcdefghijklmnop"
    result = _mask_key(key)
    assert result.startswith("sk-ant-")
    assert result.endswith("mnop")
    assert "..." in result


def test_mask_key_exact_boundary():
    # Exactly 8 chars — should be masked as "****"
    assert _mask_key("12345678") == "****"


def test_mask_key_nine_chars():
    # 9 chars — key[:7] + "..." + key[-4:]
    result = _mask_key("123456789")
    assert result == "1234567...6789"


# --- Settings whitelist test ---


def test_allowed_keys_contains_critical_keys():
    """Verify critical keys are in the actual whitelist."""
    assert "agent_enabled" in ALLOWED_SETTING_KEYS
    assert "pr_mode" in ALLOWED_SETTING_KEYS
    assert "anthropic_api_key" in ALLOWED_SETTING_KEYS


def test_allowed_keys_excludes_dangerous_keys():
    """Verify dangerous keys are NOT in the actual whitelist."""
    assert "database_url" not in ALLOWED_SETTING_KEYS
    assert "github_token" not in ALLOWED_SETTING_KEYS
    assert "session_secret" not in ALLOWED_SETTING_KEYS


def test_allowed_keys_includes_all_model_keys():
    """Verify all model keys are settable via the API."""
    model_keys = {
        "model_pr_review",
        "model_alert_triage",
        "model_alert_fix",
        "model_code_fix",
        "model_deep_review",
        "model_chat",
    }
    assert model_keys.issubset(ALLOWED_SETTING_KEYS)


# --- Prompt defaults test ---


def test_prompt_defaults_available():
    from home_ops_agent.agent.prompts import DEFAULTS as PROMPT_DEFAULTS

    assert "cluster_context" in PROMPT_DEFAULTS
    assert "pr_review" in PROMPT_DEFAULTS
    assert "alert_response" in PROMPT_DEFAULTS
    assert "chat" in PROMPT_DEFAULTS


# --- API endpoint integration tests ---


@pytest.fixture
def client(db_session, mock_settings):
    """Create a test client for the settings router with get_valid_token mocked."""
    from fastapi import FastAPI

    from home_ops_agent.api.settings import router

    app = FastAPI()
    app.include_router(router)

    with patch(
        "home_ops_agent.api.settings.get_valid_token",
        new_callable=AsyncMock,
        return_value=None,
    ):
        yield TestClient(app)


def test_get_settings_endpoint(client):
    """GET /api/settings returns full settings response."""
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "agent_enabled" in data
    assert "pr_mode" in data
    assert "models" in data
    assert data["pr_mode"] == "comment_only"
    assert data["agent_enabled"] is True
    assert "pr_review" in data["models"]
    assert "chat" in data["models"]


def test_update_setting_allowed_key(client):
    """PUT /api/settings/{key} with allowed key succeeds."""
    response = client.put("/api/settings/pr_mode", json={"value": "auto_merge"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["key"] == "pr_mode"

    # Verify the value was persisted
    response = client.get("/api/settings")
    assert response.json()["pr_mode"] == "auto_merge"


def test_update_setting_disallowed_key(client):
    """PUT /api/settings/{key} with disallowed key returns error."""
    response = client.put("/api/settings/database_url", json={"value": "bad"})
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "Unknown setting" in data["error"]


def test_update_setting_overwrites_existing(client):
    """Updating the same setting twice overwrites the value."""
    client.put("/api/settings/pr_mode", json={"value": "comment_only"})
    client.put("/api/settings/pr_mode", json={"value": "auto_merge_all"})

    response = client.get("/api/settings")
    assert response.json()["pr_mode"] == "auto_merge_all"


def test_get_prompts_endpoint(client):
    """GET /api/prompts returns all prompt defaults."""
    response = client.get("/api/prompts")
    assert response.status_code == 200
    data = response.json()
    assert "cluster_context" in data
    assert "pr_review" in data
    assert "alert_response" in data
    assert "chat" in data
    # Each prompt has default, custom, and is_customized fields
    for name, prompt in data.items():
        assert "default" in prompt
        assert "custom" in prompt
        assert "is_customized" in prompt
        assert prompt["is_customized"] is False


def test_reset_prompt_unknown(client):
    """DELETE /api/prompts/{name} with unknown name returns error."""
    response = client.delete("/api/prompts/nonexistent")
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "Unknown prompt" in data["error"]


def test_reset_prompt_success(client):
    """DELETE /api/prompts/{name} with known name succeeds."""
    # First set a custom prompt
    client.put("/api/settings/prompt_chat", json={"value": "custom prompt"})

    # Reset it
    response = client.delete("/api/prompts/chat")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    # Verify it's no longer customized
    response = client.get("/api/prompts")
    assert response.json()["chat"]["is_customized"] is False
