"""Tests for agent/prompts.py — system prompt building."""

from unittest.mock import AsyncMock, patch

from home_ops_agent.agent.prompts import (
    DEFAULT_ALERT_RESPONSE,
    DEFAULT_CHAT,
    DEFAULT_CLUSTER_CONTEXT,
    DEFAULT_PR_REVIEW,
    DEFAULTS,
    get_prompt,
)


def test_defaults_dict_has_all_keys():
    assert "cluster_context" in DEFAULTS
    assert "pr_review" in DEFAULTS
    assert "alert_response" in DEFAULTS
    assert "chat" in DEFAULTS


def test_default_cluster_context_not_empty():
    assert len(DEFAULT_CLUSTER_CONTEXT) > 50
    assert "home-ops-agent" in DEFAULT_CLUSTER_CONTEXT


def test_default_pr_review_contains_verdict_keywords():
    assert "SAFE_TO_MERGE" in DEFAULT_PR_REVIEW
    assert "NEEDS_REVIEW" in DEFAULT_PR_REVIEW
    assert "NEEDS_FIX" in DEFAULT_PR_REVIEW


def test_default_alert_response_not_empty():
    assert len(DEFAULT_ALERT_RESPONSE) > 50
    assert "Alert Investigation" in DEFAULT_ALERT_RESPONSE


def test_default_chat_not_empty():
    assert len(DEFAULT_CHAT) > 30
    assert "Interactive Chat" in DEFAULT_CHAT


async def test_get_prompt_default_no_custom(db_session):
    with patch(
        "home_ops_agent.agent.memory.load_memories",
        new_callable=AsyncMock,
        return_value="",
    ):
        result = await get_prompt("pr_review")
    assert DEFAULT_CLUSTER_CONTEXT in result
    assert DEFAULT_PR_REVIEW in result


async def test_get_prompt_includes_memory(db_session):
    with patch(
        "home_ops_agent.agent.memory.load_memories",
        new_callable=AsyncMock,
        return_value="## Agent Memory\n- [knowledge] test fact",
    ):
        result = await get_prompt("chat", include_memory=True)
    assert "Agent Memory" in result
    assert "test fact" in result


async def test_get_prompt_no_memory_flag(db_session):
    with patch(
        "home_ops_agent.agent.memory.load_memories",
        new_callable=AsyncMock,
        return_value="## Agent Memory\n- some memory",
    ) as mock_load:
        result = await get_prompt("chat", include_memory=False)
    mock_load.assert_not_called()
    assert "Agent Memory" not in result


async def test_get_prompt_custom_override(db_session):
    from home_ops_agent.database import Setting

    db_session.add(Setting(key="prompt_cluster_context", value="Custom context!"))
    await db_session.flush()

    with patch(
        "home_ops_agent.agent.memory.load_memories",
        new_callable=AsyncMock,
        return_value="",
    ):
        result = await get_prompt("chat")
    assert "Custom context!" in result
    assert DEFAULT_CLUSTER_CONTEXT not in result


async def test_get_prompt_unknown_agent(db_session):
    with patch(
        "home_ops_agent.agent.memory.load_memories",
        new_callable=AsyncMock,
        return_value="",
    ):
        result = await get_prompt("nonexistent_agent")
    # Should still have cluster context, just no agent prompt
    assert DEFAULT_CLUSTER_CONTEXT in result
