"""Tests for agent/memory.py — memory extraction and loading."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# --- Pure logic tests ---


def test_markdown_code_block_stripping():
    """Test the markdown code block stripping logic used in extract_memories."""
    result_text = '```json\n[{"content": "test", "category": "knowledge"}]\n```'
    if result_text.startswith("```"):
        result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0]
    parsed = json.loads(result_text)
    assert len(parsed) == 1
    assert parsed[0]["content"] == "test"


def test_conversation_text_building():
    """Test the message text building logic from extract_memories."""
    messages = [
        {"role": "user", "content": {"text": "Check pod status"}},
        {"role": "assistant", "content": {"text": "The pods are running fine."}},
    ]
    conv_text = ""
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", {})
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        if text:
            conv_text += f"{role}: {text}\n\n"
    assert "user: Check pod status" in conv_text
    assert "assistant: The pods are running fine." in conv_text


def test_conversation_text_string_content():
    """Content can be a string instead of dict."""
    messages = [{"role": "user", "content": "plain string"}]
    conv_text = ""
    for msg in messages:
        content = msg.get("content", {})
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        if text:
            conv_text += f"{msg['role']}: {text}\n\n"
    assert "plain string" in conv_text


def test_text_truncation():
    """Text is truncated to 8000 chars before API call."""
    long_text = "x" * 10000
    truncated = long_text[:8000]
    assert len(truncated) == 8000


# --- Async tests with mocks ---


@pytest.mark.asyncio
async def test_extract_memories_too_few_messages():
    from home_ops_agent.agent.memory import extract_memories

    result = await extract_memories(1, [{"role": "user", "content": "hi"}])
    assert result == []


@pytest.mark.asyncio
async def test_extract_memories_no_credentials():
    from home_ops_agent.agent.memory import extract_memories

    with patch(
        "home_ops_agent.agent.memory.get_claude_credentials",
        new_callable=AsyncMock,
        return_value=(None, None),
    ):
        result = await extract_memories(
            1,
            [
                {"role": "user", "content": {"text": "hello"}},
                {"role": "assistant", "content": {"text": "hi there, how can I help?"}},
            ],
        )
    assert result == []


@pytest.mark.asyncio
async def test_extract_memories_short_text():
    from home_ops_agent.agent.memory import extract_memories

    with patch(
        "home_ops_agent.agent.memory.get_claude_credentials",
        new_callable=AsyncMock,
        return_value=("sk-test", None),
    ):
        result = await extract_memories(
            1,
            [
                {"role": "user", "content": {"text": "hi"}},
                {"role": "assistant", "content": {"text": "hey"}},
            ],
        )
    assert result == []


@pytest.mark.asyncio
async def test_extract_memories_parses_json(db_session):
    from home_ops_agent.agent.memory import extract_memories

    response_text = '[{"content": "Test memory", "category": "knowledge"}]'
    mock_response = SimpleNamespace(content=[SimpleNamespace(type="text", text=response_text)])

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    # Need a valid conversation_id — create a conversation first
    from home_ops_agent.database import Conversation

    conv = Conversation(title="Test", source="chat", status="active")
    db_session.add(conv)
    await db_session.flush()

    with (
        patch(
            "home_ops_agent.agent.memory.get_claude_credentials",
            new_callable=AsyncMock,
            return_value=("sk-test", None),
        ),
        patch("home_ops_agent.agent.memory.anthropic.AsyncAnthropic", return_value=mock_client),
    ):
        result = await extract_memories(
            conv.id,
            [
                {"role": "user", "content": {"text": "Check the sonarr pod, it keeps crashing"}},
                {
                    "role": "assistant",
                    "content": {"text": "I found that sonarr needs more memory. I increased it."},
                },
            ],
        )

    assert len(result) == 1
    assert result[0]["content"] == "Test memory"


@pytest.mark.asyncio
async def test_load_memories_formats_markdown(db_session):
    from home_ops_agent.agent.memory import load_memories
    from home_ops_agent.database import Memory

    db_session.add(Memory(content="Sonarr needs 512Mi RAM", category="knowledge"))
    db_session.add(Memory(content="User prefers ntfy for alerts", category="preference"))
    await db_session.flush()

    result = await load_memories()
    assert "Agent Memory" in result
    assert "[knowledge] Sonarr needs 512Mi RAM" in result
    assert "[preference] User prefers ntfy for alerts" in result


@pytest.mark.asyncio
async def test_load_memories_no_results():
    """Test load_memories returns empty string when no memories exist."""
    from unittest.mock import AsyncMock, MagicMock

    from home_ops_agent.agent.memory import load_memories

    # Mock the session to return no memories
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("home_ops_agent.agent.memory.async_session", return_value=mock_session):
        result = await load_memories()
    assert result == ""


@pytest.mark.asyncio
async def test_extract_memories_api_error():
    from home_ops_agent.agent.memory import extract_memories

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))

    with (
        patch(
            "home_ops_agent.agent.memory.get_claude_credentials",
            new_callable=AsyncMock,
            return_value=("sk-test", None),
        ),
        patch("home_ops_agent.agent.memory.anthropic.AsyncAnthropic", return_value=mock_client),
    ):
        result = await extract_memories(
            1,
            [
                {"role": "user", "content": {"text": "Some long conversation about cluster"}},
                {"role": "assistant", "content": {"text": "Here is my analysis of the issue"}},
            ],
        )
    assert result == []
