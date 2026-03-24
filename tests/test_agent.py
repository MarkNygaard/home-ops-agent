"""Tests for agent/core.py — Agent class, tool-use loop, and helpers."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from home_ops_agent.agent.core import Agent, AgentResult, ToolDefinition, _block_to_dict
from tests.conftest import make_anthropic_response

# --- Pure function tests ---


def test_tool_definition_creation():
    async def handler(params):
        return "ok"

    tool = ToolDefinition(
        name="test_tool",
        description="A test tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )
    assert tool.name == "test_tool"
    assert tool.description == "A test tool"


def test_tool_schema_format():
    async def handler(params):
        return "ok"

    tool = ToolDefinition(
        name="k8s_get_pods",
        description="List pods",
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
            },
        },
        handler=handler,
    )
    assert tool.input_schema["type"] == "object"
    assert "namespace" in tool.input_schema["properties"]


def test_block_to_dict_text():
    block = SimpleNamespace(type="text", text="Hello world")
    result = _block_to_dict(block)
    assert result == {"type": "text", "text": "Hello world"}


def test_block_to_dict_tool_use():
    block = SimpleNamespace(type="tool_use", id="tu_123", name="my_tool", input={"key": "val"})
    result = _block_to_dict(block)
    expected = {"type": "tool_use", "id": "tu_123", "name": "my_tool", "input": {"key": "val"}}
    assert result == expected


def test_block_to_dict_unknown_type():
    block = SimpleNamespace(type="image")
    result = _block_to_dict(block)
    assert result == {"type": "image"}


def test_agent_result_defaults():
    r = AgentResult(response="test")
    assert r.response == "test"
    assert r.tool_calls == []
    assert r.total_tokens == 0


# --- Agent init tests ---


def test_agent_requires_credentials():
    with pytest.raises(ValueError, match="api_key or oauth_token"):
        Agent()


@patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic")
def test_agent_with_api_key(mock_cls):
    agent = Agent(api_key="sk-test")
    assert agent.client is not None
    mock_cls.assert_called_once_with(api_key="sk-test")


@patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic")
def test_agent_with_oauth_token(mock_cls):
    Agent(oauth_token="oauth-token-123")
    mock_cls.assert_called_once_with(auth_token="oauth-token-123")


# --- Tool registration tests ---


@patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic")
def test_register_tool(mock_cls):
    agent = Agent(api_key="sk-test")

    async def handler(params):
        return "ok"

    tool = ToolDefinition(
        name="test_tool", description="desc", input_schema={"type": "object"}, handler=handler
    )
    agent.register_tool(tool)
    assert "test_tool" in agent.tools


@patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic")
def test_register_tools_multiple(mock_cls):
    agent = Agent(api_key="sk-test")

    async def h(p):
        return "ok"

    tools = [
        ToolDefinition(name=f"tool_{i}", description=f"d{i}", input_schema={}, handler=h)
        for i in range(3)
    ]
    agent.register_tools(tools)
    assert len(agent.tools) == 3


@patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic")
def test_get_tool_schemas_empty(mock_cls):
    agent = Agent(api_key="sk-test")
    assert agent._get_tool_schemas() == []


@patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic")
def test_get_tool_schemas_format(mock_cls):
    agent = Agent(api_key="sk-test")

    async def h(p):
        return "ok"

    agent.register_tool(
        ToolDefinition(
            name="my_tool",
            description="My tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=h,
        )
    )
    schemas = agent._get_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "my_tool"
    assert schemas[0]["description"] == "My tool"
    assert schemas[0]["input_schema"]["type"] == "object"


# --- Agent.run() tests ---


async def test_run_text_response():
    """Agent returns text when no tool_use blocks in response."""
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=make_anthropic_response("Hello!"))

    with patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic", return_value=mock_client):
        agent = Agent(api_key="sk-test")
        result = await agent.run(
            system_prompt="You are helpful.",
            messages=[{"role": "user", "content": "Hi"}],
        )

    assert result.response == "Hello!"
    assert result.tool_calls == []
    assert result.total_tokens == 150


async def test_run_tool_use_loop():
    """Agent executes a tool and then gets a text response."""
    tool_response = make_anthropic_response(
        text=None,
        tool_use_blocks=[{"id": "tu_1", "name": "test_tool", "input": {"key": "val"}}],
    )
    # Add a text block so the response structure is valid
    tool_response.content.append(SimpleNamespace(type="text", text="Using tool..."))

    text_response = make_anthropic_response("Done!")

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=[tool_response, text_response])

    async def tool_handler(params):
        return json.dumps({"result": "success"})

    with patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic", return_value=mock_client):
        agent = Agent(api_key="sk-test")
        agent.register_tool(
            ToolDefinition(
                name="test_tool",
                description="Test",
                input_schema={"type": "object"},
                handler=tool_handler,
            )
        )
        result = await agent.run(
            system_prompt="test",
            messages=[{"role": "user", "content": "Do it"}],
        )

    assert result.response == "Done!"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["tool"] == "test_tool"
    assert result.total_tokens == 300  # 150 * 2 turns


async def test_run_unknown_tool():
    """Unknown tool calls return error JSON."""
    tool_response = make_anthropic_response(
        text=None,
        tool_use_blocks=[{"id": "tu_1", "name": "unknown_tool", "input": {}}],
    )
    text_response = make_anthropic_response("OK")

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=[tool_response, text_response])

    with patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic", return_value=mock_client):
        agent = Agent(api_key="sk-test")
        result = await agent.run(
            system_prompt="test",
            messages=[{"role": "user", "content": "test"}],
        )

    assert result.response == "OK"
    # The tool call was recorded
    assert result.tool_calls[0]["tool"] == "unknown_tool"


async def test_run_tool_exception():
    """Tool handler exceptions are caught and returned as error JSON."""
    tool_response = make_anthropic_response(
        text=None,
        tool_use_blocks=[{"id": "tu_1", "name": "failing_tool", "input": {}}],
    )
    text_response = make_anthropic_response("Handled error")

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=[tool_response, text_response])

    async def failing_handler(params):
        raise RuntimeError("Boom!")

    with patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic", return_value=mock_client):
        agent = Agent(api_key="sk-test")
        agent.register_tool(
            ToolDefinition(
                name="failing_tool",
                description="Fails",
                input_schema={"type": "object"},
                handler=failing_handler,
            )
        )
        result = await agent.run(
            system_prompt="test",
            messages=[{"role": "user", "content": "test"}],
        )

    assert result.response == "Handled error"


async def test_run_max_turns():
    """Agent returns sentinel message when max turns exceeded."""
    # Always return a tool_use block to keep looping
    tool_response = make_anthropic_response(
        text=None,
        tool_use_blocks=[{"id": "tu_1", "name": "looper", "input": {}}],
    )

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=tool_response)

    async def h(p):
        return "ok"

    with patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic", return_value=mock_client):
        agent = Agent(api_key="sk-test")
        agent.register_tool(
            ToolDefinition(name="looper", description="loops", input_schema={}, handler=h)
        )
        result = await agent.run(
            system_prompt="test",
            messages=[{"role": "user", "content": "loop"}],
            max_turns=2,
        )

    assert "maximum tool-use turns" in result.response
    assert result.total_tokens == 300  # 2 turns * 150


async def test_run_tool_result_non_string():
    """Tool handler returning a dict gets JSON-serialized."""
    tool_response = make_anthropic_response(
        text=None,
        tool_use_blocks=[{"id": "tu_1", "name": "dict_tool", "input": {}}],
    )
    text_response = make_anthropic_response("Got dict")

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=[tool_response, text_response])

    async def dict_handler(params):
        return {"result": "data"}  # Returns dict, not string

    with patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic", return_value=mock_client):
        agent = Agent(api_key="sk-test")
        agent.register_tool(
            ToolDefinition(
                name="dict_tool", description="returns dict", input_schema={}, handler=dict_handler
            )
        )
        result = await agent.run(
            system_prompt="test",
            messages=[{"role": "user", "content": "test"}],
        )

    assert result.response == "Got dict"


async def test_run_no_tools():
    """Agent works with no registered tools."""
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=make_anthropic_response("No tools"))

    with patch("home_ops_agent.agent.core.anthropic.AsyncAnthropic", return_value=mock_client):
        agent = Agent(api_key="sk-test")
        result = await agent.run(
            system_prompt="test",
            messages=[{"role": "user", "content": "test"}],
        )

    assert result.response == "No tools"
