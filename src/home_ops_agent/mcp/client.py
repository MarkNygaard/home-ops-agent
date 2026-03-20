"""MCP client for connecting to sidecar MCP servers (Grafana, Flux)."""

import json
import logging
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class MCPClient:
    """Manages connections to MCP sidecar servers."""

    def __init__(self):
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, dict[str, Any]] = {}  # tool_name -> {session_name, schema}

    async def connect(self, name: str, command: str, args: list[str] | None = None, env: dict[str, str] | None = None):
        """Connect to an MCP server via stdio."""
        server_params = StdioServerParameters(
            command=command,
            args=args or [],
            env=env,
        )

        transport = stdio_client(server_params)
        read_stream, write_stream = await transport.__aenter__()
        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()

        self._sessions[name] = session

        # Discover tools
        tools_response = await session.list_tools()
        for tool in tools_response.tools:
            prefixed_name = f"{name}_{tool.name}"
            self._tools[prefixed_name] = {
                "session_name": name,
                "original_name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            logger.info("Registered MCP tool: %s (from %s)", prefixed_name, name)

    async def call_tool(self, prefixed_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on an MCP server."""
        tool_info = self._tools.get(prefixed_name)
        if not tool_info:
            return json.dumps({"error": f"Unknown MCP tool: {prefixed_name}"})

        session = self._sessions.get(tool_info["session_name"])
        if not session:
            return json.dumps({"error": f"MCP session not connected: {tool_info['session_name']}"})

        try:
            result = await session.call_tool(tool_info["original_name"], arguments)
            # MCP returns content blocks, concatenate text
            texts = []
            for content in result.content:
                if hasattr(content, "text"):
                    texts.append(content.text)
                else:
                    texts.append(str(content))
            return "\n".join(texts)
        except Exception as e:
            logger.exception("MCP tool %s failed", prefixed_name)
            return json.dumps({"error": str(e)})

    def get_tool_schemas(self) -> dict[str, dict[str, Any]]:
        """Get all discovered tool schemas keyed by prefixed name."""
        return dict(self._tools)

    async def close(self):
        """Close all MCP sessions."""
        for name, session in self._sessions.items():
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                logger.warning("Failed to close MCP session: %s", name)
        self._sessions.clear()
        self._tools.clear()
