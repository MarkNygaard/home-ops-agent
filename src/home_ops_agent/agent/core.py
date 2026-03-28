"""Agent core — manages Claude API calls with tool use."""

import json
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import anthropic

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
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class Agent:
    """Claude-powered agent with tool use."""

    def __init__(self, api_key: str | None = None, oauth_token: str | None = None):
        if oauth_token:
            self.client = anthropic.AsyncAnthropic(auth_token=oauth_token)
        elif api_key:
            self.client = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            raise ValueError("Either api_key or oauth_token must be provided")

        self.tools: dict[str, ToolDefinition] = {}

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
        model: str = "claude-sonnet-4-6",
        max_turns: int = 20,
    ) -> AgentResult:
        """Run the agent with a conversation and tools.

        Implements the tool-use loop: send message → get tool_use response →
        execute tool → send tool_result → repeat until text response.
        """
        tool_schemas = self._get_tool_schemas()
        all_tool_calls: list[dict[str, Any]] = []
        total_tokens = 0
        total_input = 0
        total_output = 0

        for _turn in range(max_turns):
            response = await self.client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                messages=messages,
                tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
            )

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
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
                    input_tokens=total_input,
                    output_tokens=total_output,
                    model=model,
                )

            # Append assistant message with all content blocks
            messages.append(
                {
                    "role": "assistant",
                    "content": [_block_to_dict(b) for b in response.content],
                }
            )

            # Execute each tool call and collect results
            tool_results = []
            for tool_block in tool_use_blocks:
                tool_name = tool_block.name
                tool_input = tool_block.input

                logger.info("Executing tool: %s", tool_name)
                all_tool_calls.append(
                    {
                        "tool": tool_name,
                        "input": tool_input,
                    }
                )

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

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": result,
                    }
                )

            # Add tool results to the conversation
            messages.append({"role": "user", "content": tool_results})

        # Hit max turns
        return AgentResult(
            response="[Agent reached maximum tool-use turns]",
            tool_calls=all_tool_calls,
            total_tokens=total_tokens,
            input_tokens=total_input,
            output_tokens=total_output,
            model=model,
        )

    async def run_streaming(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        model: str = "claude-sonnet-4-6",
        max_turns: int = 20,
        on_tool_start: Callable[..., Coroutine] | None = None,
        on_tool_end: Callable[..., Coroutine] | None = None,
    ) -> AsyncGenerator[str | AgentResult, None]:
        """Run the agent with streaming for the final text response.

        Tool-use loop is non-streaming. When the final text-only response is
        detected, it is re-issued via messages.stream() for real-time delivery.

        Yields str chunks (text deltas) followed by a final AgentResult.
        """
        tool_schemas = self._get_tool_schemas()
        all_tool_calls: list[dict[str, Any]] = []
        total_tokens = 0
        total_input = 0
        total_output = 0
        tool_index = 0

        for _turn in range(max_turns):
            response = await self.client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                messages=messages,
                tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
            )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                # Final turn detected — discard the non-streaming response
                # and re-issue as streaming (don't count discarded tokens)
                async with self.client.messages.stream(
                    model=model,
                    max_tokens=8192,
                    system=system_prompt,
                    messages=messages,
                ) as stream:
                    full_text = ""
                    async for text in stream.text_stream:
                        full_text += text
                        yield text

                    final_msg = await stream.get_final_message()
                    total_input += final_msg.usage.input_tokens
                    total_output += final_msg.usage.output_tokens
                    total_tokens += final_msg.usage.input_tokens + final_msg.usage.output_tokens

                yield AgentResult(
                    response=full_text,
                    tool_calls=all_tool_calls,
                    total_tokens=total_tokens,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    model=model,
                )
                return

            # Count tokens for intermediate turns only (final turn tokens
            # come from the streaming re-issue above)
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
            total_tokens += response.usage.input_tokens + response.usage.output_tokens

            # Intermediate turn: add assistant response, execute tools
            messages.append(
                {
                    "role": "assistant",
                    "content": [_block_to_dict(b) for b in response.content],
                }
            )

            tool_results = []
            for tool_block in tool_use_blocks:
                tool_name = tool_block.name
                tool_input = tool_block.input

                logger.info("Executing tool: %s", tool_name)
                all_tool_calls.append({"tool": tool_name, "input": tool_input})

                if on_tool_start:
                    await on_tool_start(tool_name, tool_index)

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

                if on_tool_end:
                    await on_tool_end(tool_name, tool_index)

                tool_index += 1
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": result,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        # Hit max turns
        yield AgentResult(
            response="[Agent reached maximum tool-use turns]",
            tool_calls=all_tool_calls,
            total_tokens=total_tokens,
            input_tokens=total_input,
            output_tokens=total_output,
            model=model,
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
