"""Bridge MCP tools into Claude tool definitions."""

import logging

from home_ops_agent.agent.core import ToolDefinition
from home_ops_agent.mcp.client import MCPClient

logger = logging.getLogger(__name__)


def mcp_tools_to_agent_tools(mcp_client: MCPClient) -> list[ToolDefinition]:
    """Convert discovered MCP tools into ToolDefinition objects for the agent."""
    tools = []

    for prefixed_name, info in mcp_client.get_tool_schemas().items():
        async def handler(params, _name=prefixed_name):
            return await mcp_client.call_tool(_name, params)

        tools.append(ToolDefinition(
            name=prefixed_name,
            description=info["description"],
            input_schema=info["input_schema"],
            handler=handler,
        ))

    logger.info("Bridged %d MCP tools to agent", len(tools))
    return tools
