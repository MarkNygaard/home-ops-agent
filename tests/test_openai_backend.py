"""Tests for the OpenAI (ChatGPT backend) Responses path in agent/core.py.

The ChatGPT backend requires streaming (stream=true) and store=false, so the
backend must drive ``responses.stream`` — never the non-streaming
``responses.create``. These tests guard that contract.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from home_ops_agent.agent.core import Agent
from home_ops_agent.auth.credentials import Credentials


def _openai_creds():
    return Credentials(
        openai_access_token="tok",
        openai_refresh_token="ref",
        openai_account_id="acct",
        openai_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _final_response(text=None, tool_calls=None, input_tokens=10, output_tokens=5):
    output = []
    for tc in tool_calls or []:
        output.append(
            SimpleNamespace(
                type="function_call",
                name=tc["name"],
                arguments=tc.get("arguments", "{}"),
                call_id=tc.get("call_id", "call_1"),
                model_dump=lambda tc=tc: {"type": "function_call", **tc},
            )
        )
    if text is not None:
        output.append(
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
                model_dump=lambda text=text: {"type": "message", "text": text},
            )
        )
    return SimpleNamespace(
        output=output,
        output_text=text or "",
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class _FakeStream:
    """Mimics the async context manager returned by responses.stream()."""

    def __init__(self, final, events=None):
        self._final = final
        self._events = events or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def __aiter__(self):
        for event in self._events:
            yield event

    async def get_final_response(self):
        return self._final


def _fake_client(finals):
    """Build a mock OpenAI client whose responses.stream returns each final in turn."""
    streams = [_FakeStream(f) for f in finals]
    client = MagicMock()
    client.responses.stream = MagicMock(side_effect=streams)
    return client


async def test_openai_run_uses_streaming_not_create():
    agent = Agent(_openai_creds())
    client = _fake_client([_final_response(text="Hi from gpt")])

    with patch.object(Agent, "_openai_client", AsyncMock(return_value=client)):
        result = await agent.run(
            system_prompt="sys",
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5.5",
        )

    assert result.response == "Hi from gpt"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    # Must go through streaming; the non-streaming create must never be called.
    assert client.responses.stream.called
    assert not client.responses.create.called


async def test_openai_run_stream_kwargs_store_false_and_tools_omitted():
    agent = Agent(_openai_creds())
    client = _fake_client([_final_response(text="ok")])

    with patch.object(Agent, "_openai_client", AsyncMock(return_value=client)):
        await agent.run(
            system_prompt="sys", messages=[{"role": "user", "content": "x"}], model="gpt-5.5"
        )

    _, kwargs = client.responses.stream.call_args
    assert kwargs["store"] is False
    assert kwargs["model"] == "gpt-5.5"
    # No tools registered → the tools kwarg must be omitted entirely.
    assert "tools" not in kwargs


async def test_openai_run_executes_tool_then_returns_text():
    agent = Agent(_openai_creds())

    async def handler(params):
        return "tool-result"

    from home_ops_agent.agent.core import ToolDefinition

    agent.register_tool(
        ToolDefinition(
            name="do_it", description="d", input_schema={"type": "object"}, handler=handler
        )
    )

    # First turn: a tool call. Second turn: final text.
    client = _fake_client(
        [
            _final_response(tool_calls=[{"name": "do_it", "arguments": "{}", "call_id": "c1"}]),
            _final_response(text="done"),
        ]
    )

    with patch.object(Agent, "_openai_client", AsyncMock(return_value=client)):
        result = await agent.run(
            system_prompt="sys",
            messages=[{"role": "user", "content": "go"}],
            model="codex-5.3",
        )

    assert result.response == "done"
    assert result.tool_calls and result.tool_calls[0]["tool"] == "do_it"
    assert client.responses.stream.call_count == 2
    # Tokens summed across both streamed turns.
    assert result.total_tokens == 30
