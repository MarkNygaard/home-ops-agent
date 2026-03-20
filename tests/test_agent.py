"""Basic tests for the agent module."""

from home_ops_agent.agent.core import ToolDefinition


def test_tool_definition():
    """Test that ToolDefinition can be created."""
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


def test_tool_schema():
    """Test tool schema format."""
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
