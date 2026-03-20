"""Agent core — manages Claude API calls with tool use."""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic

from home_ops_agent.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """A tool that the agent can use."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Any  # async callable(params) -> str


@dataclass
class AgentResult:
    """Result of an agent run."""

    response: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0


class Agent:
    """Claude-powered agent with tool use."""

    def __init__(self, api_key: str | None = None, oauth_token: str | None = None):
        if oauth_token:
            # OAuth token from Max/Pro subscription
            self.client = anthropic.Anthropic(auth_token=oauth_token)
        elif api_key:
            self.client = anthropic.Anthropic(api_key=api_key)
        else:
            raise ValueError("Either api_key or oauth_token must be provided")

        self.tools: dict[str, ToolDefinition] = {}
        self.model = settings.claude_model

    def register_tool(self, tool: ToolDefinition):
        """Register a tool for the agent to use."""
        self.tools[tool.name] = tool

    def register_tools(self, tools: list[ToolDefinition]):
        """Register multiple tools."""
        for tool in tools:
            self.register_tool(tool)

    def _get_tool_schemas(self) -> list[dict]:
        """Get tool schemas in Anthropic API format."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self.tools.values()
        ]

    async def run(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        max_turns: int = 20,
    ) -> AgentResult:
        """Run the agent with a conversation and tools.

        Implements the tool-use loop: send message → get tool_use response →
        execute tool → send tool_result → repeat until text response.
        """
        tool_schemas = self._get_tool_schemas()
        all_tool_calls: list[dict[str, Any]] = []
        total_tokens = 0

        for _turn in range(max_turns):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=system_prompt,
                messages=messages,
                tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
            )

            total_tokens += response.usage.input_tokens + response.usage.output_tokens

            # Check if the response contains tool use
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]

            if not tool_use_blocks:
                # No tool use — return the text response
                final_text = "\n".join(b.text for b in text_blocks)
                return AgentResult(
                    response=final_text,
                    tool_calls=all_tool_calls,
                    total_tokens=total_tokens,
                )

            # Append assistant message with all content blocks
            messages.append({
                "role": "assistant",
                "content": [_block_to_dict(b) for b in response.content],
            })

            # Execute each tool call and collect results
            tool_results = []
            for tool_block in tool_use_blocks:
                tool_name = tool_block.name
                tool_input = tool_block.input

                logger.info("Executing tool: %s", tool_name)
                all_tool_calls.append({
                    "tool": tool_name,
                    "input": tool_input,
                })

                tool_def = self.tools.get(tool_name)
                if tool_def is None:
                    result = json.dumps({"error": f"Unknown tool: {tool_name}"})
                else:
                    try:
                        result = await tool_def.handler(tool_input)
                        if not isinstance(result, str):
                            result = json.dumps(result, default=str)
                    except Exception as e:
                        logger.exception("Tool %s failed", tool_name)
                        result = json.dumps({"error": str(e)})

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result,
                })

            # Add tool results to the conversation
            messages.append({"role": "user", "content": tool_results})

        # Hit max turns
        return AgentResult(
            response="[Agent reached maximum tool-use turns]",
            tool_calls=all_tool_calls,
            total_tokens=total_tokens,
        )


def _block_to_dict(block) -> dict:
    """Convert an Anthropic content block to a dict for message history."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    elif block.type == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    return {"type": block.type}
