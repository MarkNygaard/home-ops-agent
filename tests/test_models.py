"""Tests for agent/models.py — per-task model resolution."""

from home_ops_agent.agent.models import _DEFAULTS, get_model_for_task


def test_defaults_has_all_keys():
    expected_keys = {"pr_review", "alert_triage", "alert_fix", "code_fix", "deep_review", "chat"}
    assert set(_DEFAULTS.keys()) == expected_keys


def test_defaults_values():
    assert "haiku" in _DEFAULTS["pr_review"]
    assert "haiku" in _DEFAULTS["alert_triage"]
    assert "sonnet" in _DEFAULTS["alert_fix"]
    assert "sonnet" in _DEFAULTS["code_fix"]
    assert "opus" in _DEFAULTS["deep_review"]
    assert "sonnet" in _DEFAULTS["chat"]


async def test_get_model_for_task_db_override(db_session):
    from home_ops_agent.database import Setting

    db_session.add(Setting(key="model_chat", value="claude-opus-4-6"))
    await db_session.flush()

    result = await get_model_for_task("chat")
    assert result == "claude-opus-4-6"


async def test_get_model_for_task_fallback_to_default(db_session):
    # No DB setting exists, should fall back to _DEFAULTS
    result = await get_model_for_task("chat")
    assert result == _DEFAULTS["chat"]


async def test_get_model_for_task_unknown_falls_to_chat(db_session):
    from home_ops_agent.config import settings

    result = await get_model_for_task("nonexistent_task")
    # Falls back to settings.model_chat via _DEFAULTS.get(task, settings.model_chat)
    assert result == settings.model_chat
