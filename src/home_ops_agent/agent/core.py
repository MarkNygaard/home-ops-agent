"""Agent core — provider-aware tool-use loop.

A single ``Agent`` can route each ``run()`` to a different provider based on the
model ID, so a worker can build one agent and use Claude, Kimi, and GPT/Codex
models interchangeably (whichever has credentials):

- Anthropic & Kimi share the Anthropic wire protocol (Kimi via its
  Anthropic-compatible endpoint), handled by the same backend.
- OpenAI / Codex models use the ChatGPT-backend Responses API.
"""

import json
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import anthropic

from home_ops_agent.agent import providers
from home_ops_agent.auth.credentials import Credentials, ensure_openai_token

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
    """Provider-aware agent with tool use."""

    def __init__(self, credentials: Credentials | None = None, *, api_key: str | None = None):
        # ``api_key`` is a convenience for Anthropic-only callers (and tests).
        if credentials is None:
            if api_key:
                credentials = Credentials(anthropic_api_key=api_key)
            else:
                raise ValueError("credentials (or api_key) must be provided")
        if not credentials.has_any():
            raise ValueError("No provider credentials configured")

        self.credentials = credentials
        self.tools: dict[str, ToolDefinition] = {}
        self._anthropic_clients: dict[str, anthropic.AsyncAnthropic] = {}

    # --- tool registration ---

    def register_tool(self, tool: ToolDefinition):
        """Register a tool for the agent to use."""
        self.tools[tool.name] = tool

    def register_tools(self, tools: list[ToolDefinition]):
        """Register multiple tools."""
        for tool in tools:
            self.register_tool(tool)

    # --- client factories ---

    def _anthropic_client(self, provider: str) -> anthropic.AsyncAnthropic:
        if provider not in self._anthropic_clients:
            if provider == providers.KIMI:
                client = anthropic.AsyncAnthropic(
                    api_key=self.credentials.kimi_api_key,
                    base_url=providers.KIMI_BASE_URL,
                )
            else:
                client = anthropic.AsyncAnthropic(api_key=self.credentials.anthropic_api_key)
            self._anthropic_clients[provider] = client
        return self._anthropic_clients[provider]

    async def _openai_client(self):
        from openai import AsyncOpenAI

        token = await ensure_openai_token(self.credentials)
        if not token:
            raise ValueError("OpenAI credentials unavailable")
        # Rebuilt per use so a refreshed token is always applied.
        return AsyncOpenAI(
            base_url=providers.OPENAI_BASE_URL,
            api_key=token,
            default_headers={
                "chatgpt-account-id": self.credentials.openai_account_id or "",
                "OpenAI-Beta": "responses=experimental",
                "originator": "codex_cli_rs",
            },
        )

    def _provider_for(self, model: str) -> str:
        provider = providers.resolve_provider(model)
        if not self.credentials.has_provider(provider):
            raise ValueError(f"No credentials configured for provider '{provider}' (model {model})")
        return provider

    # --- tool schemas ---

    def _anthropic_tool_schemas(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self.tools.values()
        ]

    def _openai_tool_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            }
            for t in self.tools.values()
        ]

    async def _execute_tool(self, name: str, tool_input: Any) -> str:
        """Execute a registered tool, returning a string result."""
        tool_def = self.tools.get(name)
        if tool_def is None:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            result = await tool_def.handler(tool_input)
            if not isinstance(result, str):
                result = json.dumps(result, default=str)
            return result
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return json.dumps({"error": str(e)})

    # --- public entry points ---

    async def run(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        model: str = "claude-sonnet-4-6",
        max_turns: int = 20,
    ) -> AgentResult:
        """Run the agent with a conversation and tools (provider auto-selected)."""
        provider = self._provider_for(model)
        if provider in providers.ANTHROPIC_PROTOCOL:
            return await self._run_anthropic(
                self._anthropic_client(provider), system_prompt, messages, model, max_turns
            )
        return await self._run_openai(system_prompt, messages, model, max_turns)

    async def run_streaming(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        model: str = "claude-sonnet-4-6",
        max_turns: int = 20,
        on_tool_start: Callable[..., Coroutine] | None = None,
        on_tool_end: Callable[..., Coroutine] | None = None,
    ) -> AsyncGenerator[str | AgentResult, None]:
        """Streaming variant; yields text chunks then a final AgentResult."""
        provider = self._provider_for(model)
        if provider in providers.ANTHROPIC_PROTOCOL:
            gen = self._run_anthropic_streaming(
                self._anthropic_client(provider),
                system_prompt,
                messages,
                model,
                max_turns,
                on_tool_start,
                on_tool_end,
            )
        else:
            gen = self._run_openai_streaming(
                system_prompt, messages, model, max_turns, on_tool_start, on_tool_end
            )
        async for chunk in gen:
            yield chunk

    # --- Anthropic / Kimi backend ---

    async def _run_anthropic(
        self,
        client: anthropic.AsyncAnthropic,
        system_prompt: str,
        messages: list[dict[str, Any]],
        model: str,
        max_turns: int,
    ) -> AgentResult:
        tool_schemas = self._anthropic_tool_schemas()
        all_tool_calls: list[dict[str, Any]] = []
        total_tokens = total_input = total_output = 0

        for _turn in range(max_turns):
            response = await client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                messages=messages,
                tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
            )

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
            total_tokens += response.usage.input_tokens + response.usage.output_tokens

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]

            if not tool_use_blocks:
                final_text = "\n".join(b.text for b in text_blocks)
                return AgentResult(
                    response=final_text,
                    tool_calls=all_tool_calls,
                    total_tokens=total_tokens,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    model=model,
                )

            messages.append(
                {"role": "assistant", "content": [_block_to_dict(b) for b in response.content]}
            )

            tool_results = []
            for tool_block in tool_use_blocks:
                logger.info("Executing tool: %s", tool_block.name)
                all_tool_calls.append({"tool": tool_block.name, "input": tool_block.input})
                result = await self._execute_tool(tool_block.name, tool_block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": result,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        return AgentResult(
            response="[Agent reached maximum tool-use turns]",
            tool_calls=all_tool_calls,
            total_tokens=total_tokens,
            input_tokens=total_input,
            output_tokens=total_output,
            model=model,
        )

    async def _run_anthropic_streaming(
        self,
        client: anthropic.AsyncAnthropic,
        system_prompt: str,
        messages: list[dict[str, Any]],
        model: str,
        max_turns: int,
        on_tool_start: Callable[..., Coroutine] | None,
        on_tool_end: Callable[..., Coroutine] | None,
    ) -> AsyncGenerator[str | AgentResult, None]:
        tool_schemas = self._anthropic_tool_schemas()
        all_tool_calls: list[dict[str, Any]] = []
        total_tokens = total_input = total_output = 0
        tool_index = 0

        for _turn in range(max_turns):
            response = await client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                messages=messages,
                tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
            )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                async with client.messages.stream(
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

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
            total_tokens += response.usage.input_tokens + response.usage.output_tokens

            messages.append(
                {"role": "assistant", "content": [_block_to_dict(b) for b in response.content]}
            )

            tool_results = []
            for tool_block in tool_use_blocks:
                logger.info("Executing tool: %s", tool_block.name)
                all_tool_calls.append({"tool": tool_block.name, "input": tool_block.input})

                if on_tool_start:
                    await on_tool_start(tool_block.name, tool_index)

                result = await self._execute_tool(tool_block.name, tool_block.input)

                if on_tool_end:
                    await on_tool_end(tool_block.name, tool_index)

                tool_index += 1
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": result,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        yield AgentResult(
            response="[Agent reached maximum tool-use turns]",
            tool_calls=all_tool_calls,
            total_tokens=total_tokens,
            input_tokens=total_input,
            output_tokens=total_output,
            model=model,
        )

    # --- OpenAI / Codex backend (Responses API) ---

    async def _consume_openai_stream(self, stream, state) -> AsyncGenerator[str, None]:
        """Iterate a Responses stream: yield text deltas, record items + usage.

        Robust to the ChatGPT backend, whose final Response may leave
        ``output``/``usage`` unset. Output items are collected from
        ``response.output_item.done`` events and usage from
        ``response.completed``; ``get_final_response()`` is only a fallback.
        """
        async for event in stream:
            etype = getattr(event, "type", "")
            if etype == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    state.text += delta
                    yield delta
            elif etype == "response.output_item.done":
                item = getattr(event, "item", None)
                if item is not None:
                    state.output_items.append(item)
            elif etype == "response.completed":
                response = getattr(event, "response", None)
                if response is not None:
                    if getattr(response, "usage", None) is not None:
                        state.usage = response.usage
                    if getattr(response, "output", None):
                        state.output_items = list(response.output)

        # Fall back to the final response only if events didn't supply what we
        # need (the ChatGPT backend doesn't always populate these on the stream).
        if state.usage is None or not state.output_items:
            try:
                final = await stream.get_final_response()
            except Exception:
                final = None
            if final is not None:
                if state.usage is None:
                    state.usage = getattr(final, "usage", None)
                if not state.output_items and getattr(final, "output", None):
                    state.output_items = list(final.output)
                if not state.text:
                    state.text = _text_from_items(getattr(final, "output", None))

    async def _run_openai(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        model: str,
        max_turns: int,
    ) -> AgentResult:
        client = await self._openai_client()
        tools = self._openai_tool_schemas()
        input_items = _messages_to_openai_input(messages)
        all_tool_calls: list[dict[str, Any]] = []
        total_tokens = total_input = total_output = 0

        for _turn in range(max_turns):
            # ChatGPT backend requires streaming (stream=true) + store=false.
            state = _OpenAIStreamState()
            async with client.responses.stream(
                model=model,
                instructions=system_prompt,
                input=input_items,
                store=False,
                **({"tools": tools} if tools else {}),
            ) as stream:
                async for _delta in self._consume_openai_stream(stream, state):
                    pass

            total_input += _usage_int(state.usage, "input_tokens")
            total_output += _usage_int(state.usage, "output_tokens")
            total_tokens = total_input + total_output

            func_calls = [
                o for o in state.output_items if getattr(o, "type", None) == "function_call"
            ]
            if not func_calls:
                return AgentResult(
                    response=state.text or _text_from_items(state.output_items),
                    tool_calls=all_tool_calls,
                    total_tokens=total_tokens,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    model=model,
                )

            # Feed the model's own output back, then the tool outputs.
            for item in state.output_items:
                input_items.append(item.model_dump())

            for fc in func_calls:
                tool_input = json.loads(getattr(fc, "arguments", "") or "{}")
                logger.info("Executing tool: %s", fc.name)
                all_tool_calls.append({"tool": fc.name, "input": tool_input})
                result = await self._execute_tool(fc.name, tool_input)
                input_items.append(
                    {"type": "function_call_output", "call_id": fc.call_id, "output": result}
                )

        return AgentResult(
            response="[Agent reached maximum tool-use turns]",
            tool_calls=all_tool_calls,
            total_tokens=total_tokens,
            input_tokens=total_input,
            output_tokens=total_output,
            model=model,
        )

    async def _run_openai_streaming(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        model: str,
        max_turns: int,
        on_tool_start: Callable[..., Coroutine] | None,
        on_tool_end: Callable[..., Coroutine] | None,
    ) -> AsyncGenerator[str | AgentResult, None]:
        client = await self._openai_client()
        tools = self._openai_tool_schemas()
        input_items = _messages_to_openai_input(messages)
        all_tool_calls: list[dict[str, Any]] = []
        total_tokens = total_input = total_output = 0
        tool_index = 0

        for _turn in range(max_turns):
            # Stream every turn (backend requirement), forwarding text live.
            state = _OpenAIStreamState()
            async with client.responses.stream(
                model=model,
                instructions=system_prompt,
                input=input_items,
                store=False,
                **({"tools": tools} if tools else {}),
            ) as stream:
                async for delta in self._consume_openai_stream(stream, state):
                    yield delta

            total_input += _usage_int(state.usage, "input_tokens")
            total_output += _usage_int(state.usage, "output_tokens")
            total_tokens = total_input + total_output

            func_calls = [
                o for o in state.output_items if getattr(o, "type", None) == "function_call"
            ]

            if not func_calls:
                yield AgentResult(
                    response=state.text or _text_from_items(state.output_items),
                    tool_calls=all_tool_calls,
                    total_tokens=total_tokens,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    model=model,
                )
                return

            for item in state.output_items:
                input_items.append(item.model_dump())

            for fc in func_calls:
                tool_input = json.loads(getattr(fc, "arguments", "") or "{}")
                logger.info("Executing tool: %s", fc.name)
                all_tool_calls.append({"tool": fc.name, "input": tool_input})
                if on_tool_start:
                    await on_tool_start(fc.name, tool_index)
                result = await self._execute_tool(fc.name, tool_input)
                if on_tool_end:
                    await on_tool_end(fc.name, tool_index)
                tool_index += 1
                input_items.append(
                    {"type": "function_call_output", "call_id": fc.call_id, "output": result}
                )

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


def _messages_to_openai_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert simple {role, content} messages into Responses API input items."""
    items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            ctype = "output_text" if role == "assistant" else "input_text"
            items.append({"role": role, "content": [{"type": ctype, "text": content}]})
        else:
            items.append({"role": role, "content": content})
    return items


@dataclass
class _OpenAIStreamState:
    """Mutable accumulator filled while consuming a Responses stream."""

    text: str = ""
    output_items: list = field(default_factory=list)
    usage: Any = None


def _usage_int(usage, attr: str) -> int:
    """Safely read an int token count off a (possibly None) usage object."""
    if usage is None:
        return 0
    return int(getattr(usage, attr, 0) or 0)


def _text_from_items(items) -> str:
    """Extract concatenated assistant text from Responses output items."""
    parts: list[str] = []
    for item in items or []:
        if getattr(item, "type", None) == "message":
            for block in getattr(item, "content", None) or []:
                if getattr(block, "type", None) == "output_text":
                    parts.append(getattr(block, "text", "") or "")
    return "\n".join(p for p in parts if p)
