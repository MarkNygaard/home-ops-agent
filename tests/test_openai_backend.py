"""Tests for the OpenAI (ChatGPT backend) Responses path in agent/core.py.

The ChatGPT backend requires streaming (stream=true) and store=false. The
backend must use the LOW-LEVEL ``responses.create(stream=True)`` event stream,
never the high-level ``responses.stream`` helper — the helper's accumulator
calls ``parse_response`` which crashes ('NoneType' is not iterable) when the
backend emits a ``completed`` event with ``output=None``. These tests guard
that contract and the None-output handling.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from home_ops_agent.agent.core import Agent, ToolDefinition
from home_ops_agent.auth.credentials import Credentials


def _openai_creds():
    return Credentials(
        openai_access_token="tok",
        openai_refresh_token="ref",
        openai_account_id="acct",
        openai_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _final_response(
    text=None, tool_calls=None, reasoning_id=None, input_tokens=10, output_tokens=5
):
    output = []
    if reasoning_id:
        # Reasoning items carry a server-reference id (rs_*) and must be dropped
        # when re-feeding under store=false.
        output.append(
            SimpleNamespace(
                type="reasoning",
                id=reasoning_id,
                model_dump=lambda rid=reasoning_id: {
                    "type": "reasoning",
                    "id": rid,
                    "status": "completed",
                    "summary": [],
                },
            )
        )
    for tc in tool_calls or []:
        output.append(
            SimpleNamespace(
                type="function_call",
                name=tc["name"],
                arguments=tc.get("arguments", "{}"),
                call_id=tc.get("call_id", "call_1"),
                # model_dump includes output-only `id`/`status` fields that must
                # be stripped before re-feeding as an input item.
                model_dump=lambda tc=tc: {
                    "type": "function_call",
                    "id": "fc_" + tc.get("call_id", "call_1"),
                    "status": "completed",
                    **tc,
                },
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
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _delta_event(text):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _completed_event(
    text=None, tool_calls=None, reasoning_id=None, input_tokens=10, output_tokens=5
):
    return SimpleNamespace(
        type="response.completed",
        response=_final_response(text, tool_calls, reasoning_id, input_tokens, output_tokens),
    )


class _FakeEventStream:
    """Async-iterable of raw Responses stream events (what create(stream=True) returns)."""

    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for event in self._events:
            yield event


def _fake_client(events_per_turn):
    """Mock OpenAI client whose responses.create returns one event stream per turn."""
    streams = [_FakeEventStream(events) for events in events_per_turn]
    client = MagicMock()
    client.responses.create = AsyncMock(side_effect=streams)
    return client


async def test_openai_run_uses_low_level_create_not_stream_helper():
    agent = Agent(_openai_creds())
    client = _fake_client([[_completed_event(text="Hi from gpt")]])

    with patch.object(Agent, "_openai_client", AsyncMock(return_value=client)):
        result = await agent.run(
            system_prompt="sys",
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5.5",
        )

    assert result.response == "Hi from gpt"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    # Must use the low-level event stream; the crashing stream() helper must not be used.
    assert client.responses.create.called
    assert not client.responses.stream.called


async def test_openai_create_kwargs_stream_and_store():
    agent = Agent(_openai_creds())
    client = _fake_client([[_completed_event(text="ok")]])

    with patch.object(Agent, "_openai_client", AsyncMock(return_value=client)):
        await agent.run(
            system_prompt="sys", messages=[{"role": "user", "content": "x"}], model="gpt-5.5"
        )

    _, kwargs = client.responses.create.call_args
    assert kwargs["stream"] is True
    assert kwargs["store"] is False
    assert kwargs["model"] == "gpt-5.5"
    # No tools registered → the tools kwarg must be omitted entirely.
    assert "tools" not in kwargs


async def test_openai_executes_tool_then_returns_text():
    agent = Agent(_openai_creds())

    async def handler(params):
        return "tool-result"

    agent.register_tool(
        ToolDefinition(
            name="do_it", description="d", input_schema={"type": "object"}, handler=handler
        )
    )

    client = _fake_client(
        [
            [
                _completed_event(
                    tool_calls=[{"name": "do_it", "arguments": "{}", "call_id": "c1"}],
                    reasoning_id="rs_abc",
                )
            ],
            [_completed_event(text="done")],
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
    assert client.responses.create.call_count == 2
    assert result.total_tokens == 30

    # The 2nd turn re-feeds the 1st turn's output as input. Under store=false:
    #   - reasoning items (rs_*) must be dropped (would 404),
    #   - output-only `status`/`id` must be stripped from what remains.
    second_call_input = client.responses.create.call_args_list[1].kwargs["input"]
    assert all(i.get("type") != "reasoning" for i in second_call_input if isinstance(i, dict))
    fed_back = [
        i for i in second_call_input if isinstance(i, dict) and i.get("type") == "function_call"
    ]
    assert fed_back, "expected the function_call to be re-fed as input"
    assert all("status" not in i and "id" not in i for i in fed_back)


async def test_openai_streaming_yields_deltas():
    agent = Agent(_openai_creds())
    events = [_delta_event("Hello "), _delta_event("world"), _completed_event(text="Hello world")]
    client = _fake_client([events])

    deltas: list[str] = []
    final = None
    with patch.object(Agent, "_openai_client", AsyncMock(return_value=client)):
        async for chunk in agent.run_streaming(
            system_prompt="sys",
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-5.5",
        ):
            if isinstance(chunk, str):
                deltas.append(chunk)
            else:
                final = chunk

    assert deltas == ["Hello ", "world"]
    assert final is not None
    assert final.response == "Hello world"
    assert final.input_tokens == 10


async def test_openai_handles_none_output_without_crashing():
    """The exact bug: a completed event with output=None must not crash; the
    answer falls back to the text accumulated from deltas."""
    agent = Agent(_openai_creds())
    events = [
        _delta_event("partial answer"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                output=None, usage=SimpleNamespace(input_tokens=2, output_tokens=1)
            ),
        ),
    ]
    client = _fake_client([events])

    with patch.object(Agent, "_openai_client", AsyncMock(return_value=client)):
        result = await agent.run(
            system_prompt="sys", messages=[{"role": "user", "content": "x"}], model="gpt-5.5"
        )

    assert result.response == "partial answer"
    assert result.input_tokens == 2
    assert result.output_tokens == 1
