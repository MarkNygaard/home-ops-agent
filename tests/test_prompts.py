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


def test_code_fix_has_its_own_prompt_not_the_chat_one():
    """Both code-fix paths ran on DEFAULT_CHAT until this existed.

    That prompt opens "The user is asking you about the cluster" and advises
    "if you're unsure, say so and suggest what the user could check" — for an
    unattended run whose whole purpose is to make a change, that is close to an
    instruction to give up and write a reply. It worked only because the real
    task arrived in the user message.
    """
    from home_ops_agent.agent.prompts import DEFAULTS

    assert "code_fix" in DEFAULTS
    assert DEFAULTS["code_fix"] != DEFAULTS["chat"]
    assert "The user is asking you" not in DEFAULTS["code_fix"]


def test_both_code_fix_paths_use_it():
    """The worker and the chat tool must not drift onto different prompts."""
    import inspect

    from home_ops_agent.agent.tools import code_fix as tool
    from home_ops_agent.workers import pr_fix

    for module in (pr_fix, tool):
        source = inspect.getsource(module)
        assert 'get_prompt("code_fix")' in source, module.__name__
        assert 'get_prompt("chat")' not in source, module.__name__


def test_the_code_fix_prompt_tells_it_when_not_to_commit():
    """The failure that costs the most is a confident wrong commit on a branch
    that auto-merges, not a run that declines."""
    from home_ops_agent.agent.prompts import DEFAULTS

    text = DEFAULTS["code_fix"].lower()
    assert "commit nothing" in text


def test_the_code_fix_prompt_names_no_tools():
    """It is read on two backends whose tool names differ — Claude Code has Grep
    and Glob, pi has grep and find — so naming any sends one of them looking for
    a tool it does not have. The schemas are the source of truth."""
    from home_ops_agent.agent.prompts import DEFAULTS

    text = DEFAULTS["code_fix"]
    for name in ("Grep", "Glob", "workspace_commit", "github_", "k8s_", "kubeconform"):
        assert name not in text, name


def test_the_ui_offers_the_code_fix_prompt():
    """A prompt with no button is one the operator cannot tune — which is how
    Code Fix ended up silently running on the chat prompt."""
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "constants.ts"
    ).read_text(encoding="utf-8")

    block = re.search(r"AGENTS = \[(.*?)\] as const", source, re.S).group(1)
    row = re.search(r'\{[^{}]*modelKey:\s*"code_fix".*?\}', block, re.S).group(0)
    assert 'promptKey: "code_fix"' in row

    # Every promptKey the UI offers must be a prompt that actually exists.
    from home_ops_agent.agent.prompts import DEFAULTS

    for key in re.findall(r'promptKey:\s*"([^"]+)"', block):
        assert key in DEFAULTS, key
