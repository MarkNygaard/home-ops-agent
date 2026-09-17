"""Tests for the pi harness.

These cover the parts that are cheap to get wrong and expensive to notice: the
safety flags on the command line, the credential file pi reads, and the parsing
of pi's event stream. Running pi itself is not attempted — that needs a live
provider and belongs in a manual check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from home_ops_agent.agent import pi
from home_ops_agent.auth.credentials import Credentials


def test_extension_discovery_is_disabled():
    """`-ne` must always be present.

    Without it pi loads extensions from the working directory, and the working
    directory during a code fix is a checkout of home-ops — so a file in the
    repository being edited could introduce a tool the image never shipped.
    """
    argv = pi.build_argv("gpt-6-astra", "sys", "hello")
    assert "-ne" in argv


def test_model_gets_a_provider_prefix():
    """A bare model name is ambiguous to pi; prefix it with the provider."""
    argv = pi.build_argv("gpt-6-astra", "sys", "hello")
    assert "openai-codex/gpt-6-astra" in argv


def test_explicit_provider_prefix_is_left_alone():
    argv = pi.build_argv("moonshotai/kimi-k2.6", "sys", "hello")
    assert "moonshotai/kimi-k2.6" in argv
    assert "openai-codex/moonshotai/kimi-k2.6" not in argv


def test_json_mode_and_non_interactive():
    """Anything else hangs: the default mode waits on a terminal."""
    argv = pi.build_argv("gpt-6-astra", "sys", "hello")
    assert argv[argv.index("--mode") + 1] == "json"
    assert "-p" in argv


def test_write_auth_without_credentials_reports_failure():
    """No token must fail here, not inside pi as an opaque 401."""
    assert pi.write_auth(Credentials()) is False


def test_auth_file_sits_under_dot_pi():
    """pi reads ~/.pi/agent/auth.json, so the path must carry the .pi level.

    Without it the file lands somewhere pi never looks and the run fails as
    "No API key found" rather than as a missing credential.
    """
    assert pi.AUTH_FILE.parent.parent.name == ".pi"
    assert pi.AUTH_FILE.parent.name == "agent"


def test_write_auth_shape_and_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "PI_HOME", tmp_path)
    monkeypatch.setattr(pi, "AUTH_FILE", tmp_path / ".pi" / "agent" / "auth.json")

    creds = Credentials(
        openai_access_token="access-token",
        openai_refresh_token="refresh-token",
        openai_account_id="account-id",
        openai_expires_at=datetime(2026, 9, 17, 12, 0, tzinfo=UTC),
    )
    assert pi.write_auth(creds) is True

    written = json.loads(pi.AUTH_FILE.read_text(encoding="utf-8"))
    entry = written[pi.CODEX_PROVIDER]
    assert entry["access"] == "access-token"
    assert entry["accountId"] == "account-id"
    # Withheld on purpose: OpenAI rotates the refresh token on use, so letting pi
    # refresh invalidates the copy the agent depends on (401 refresh_token_reused).
    assert "refresh" not in entry
    # pi expects epoch milliseconds, not seconds and not ISO-8601.
    assert entry["expires"] == int(creds.openai_expires_at.timestamp() * 1000)


def test_text_of_joins_only_text_blocks():
    """Tool-call blocks share the content array and must not leak into the reply."""
    message = {
        "content": [
            {"type": "text", "text": "first "},
            {"type": "toolCall", "name": "web_search"},
            {"type": "text", "text": "second"},
        ]
    }
    assert pi._text_of(message) == "first second"


async def _noop_ensure(_creds):
    """The agent refreshes before writing auth.json; tests do not need a network."""
    return "token"


@pytest.mark.asyncio
async def test_stop_reason_error_is_a_failure_even_with_clean_exit_and_no_stderr(monkeypatch):
    """The shape a live provider rejection actually has.

    Measured in the cluster against a spent credential: exit 0, stderr empty,
    and a well-formed stream whose assistant message carries stopReason=error
    with no text and error=None. Neither the exit code nor stderr says anything,
    so guards keyed on those returned it as a successful empty answer twice.
    """
    events = [
        {"type": "agent_start"},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [],
                "stopReason": "error",
                "error": None,
                "usage": {"input": 0, "output": 0},
            },
        },
        {"type": "agent_end"},
    ]

    class _FakeStdout:
        def __aiter__(self):
            async def gen():
                for e in events:
                    yield (json.dumps(e) + "\n").encode()

            return gen()

    class _FakeStderr:
        async def read(self):
            return b""

    class _FakeProc:
        returncode = 0
        stdout = _FakeStdout()
        stderr = _FakeStderr()

        async def wait(self):
            return 0

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr(pi.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pi, "write_auth", lambda _creds: True)
    monkeypatch.setattr(pi, "ensure_openai_token", _noop_ensure)

    with pytest.raises(RuntimeError, match="stopReason=error"):
        async for _ in pi.stream(
            "sys", [{"role": "user", "content": "hi"}], "gpt-6-astra", Credentials()
        ):
            pass


@pytest.mark.asyncio
async def test_clean_exit_with_no_text_is_still_a_failure(monkeypatch):
    """pi returns 0 when the provider rejects the credential.

    The auth error goes to stderr and the JSON stream simply carries no
    assistant text, so keying only on the exit code let a dead credential
    surface as a model with nothing to say.
    """

    class _FakeStdout:
        def __aiter__(self):
            async def gen():
                if False:
                    yield b""

            return gen()

    class _FakeStderr:
        async def read(self):
            return b"OAuth refresh failed for openai-codex: 401 refresh_token_reused"

    class _FakeProc:
        returncode = 0
        stdout = _FakeStdout()
        stderr = _FakeStderr()

        async def wait(self):
            return 0

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr(pi.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pi, "write_auth", lambda _creds: True)
    monkeypatch.setattr(pi, "ensure_openai_token", _noop_ensure)

    with pytest.raises(RuntimeError, match="refresh_token_reused"):
        async for _ in pi.stream(
            "sys", [{"role": "user", "content": "hi"}], "gpt-6-astra", Credentials()
        ):
            pass


@pytest.mark.asyncio
async def test_subprocess_gets_a_writable_home(monkeypatch, tmp_path):
    """pi resolves ~/.pi from HOME and has no override for it.

    The container's HOME is a read-only filesystem, so leaving it alone kills pi
    before it reaches the model with an ENOENT creating its session directory.
    """
    captured: dict = {}

    class _FakeStdout:
        def __aiter__(self):
            async def gen():
                if False:
                    yield b""

            return gen()

    class _FakeStderr:
        async def read(self):
            return b""

    class _FakeProc:
        returncode = 0
        stdout = _FakeStdout()
        stderr = _FakeStderr()

        async def wait(self):
            return 0

    async def _fake_exec(*_args, **kwargs):
        captured.update(kwargs.get("env") or {})
        return _FakeProc()

    monkeypatch.setattr(pi, "PI_HOME", tmp_path)
    monkeypatch.setattr(pi.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pi, "write_auth", lambda _creds: True)
    monkeypatch.setattr(pi, "ensure_openai_token", _noop_ensure)

    async for _ in pi.stream(
        "sys", [{"role": "user", "content": "hi"}], "gpt-6-astra", Credentials()
    ):
        pass

    assert captured["HOME"] == str(tmp_path)


@pytest.mark.asyncio
async def test_stream_parses_events(monkeypatch):
    """Drive the parser over a recorded stream, without launching pi.

    The events below are the shape pi 0.85.1 actually emits, taken from a real
    gpt-6-astra run that called the SearXNG tool.
    """
    from home_ops_agent.agent.core import AgentResult

    events = [
        {"type": "agent_start"},
        {
            "type": "tool_execution_start",
            "toolCallId": "call_abc",
            "toolName": "web_search",
            "args": {"query": "external-dns 1.22.0"},
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "the answer"}],
                "usage": {"input": 1500, "output": 86},
            },
        },
        {"type": "agent_end"},
    ]

    class _FakeStdout:
        def __aiter__(self):
            async def gen():
                for event in events:
                    yield (json.dumps(event) + "\n").encode()

            return gen()

    class _FakeStderr:
        async def read(self):
            return b""

    class _FakeProc:
        returncode = 0
        stdout = _FakeStdout()
        stderr = _FakeStderr()

        async def wait(self):
            return 0

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr(pi.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pi, "write_auth", lambda _creds: True)
    monkeypatch.setattr(pi, "ensure_openai_token", _noop_ensure)

    result = None
    texts = []
    async for item in pi.stream(
        "sys", [{"role": "user", "content": "hi"}], "gpt-6-astra", Credentials()
    ):
        if isinstance(item, AgentResult):
            result = item
        else:
            texts.append(item)

    assert texts == ["the answer"]
    assert result is not None
    assert result.response == "the answer"
    assert result.input_tokens == 1500
    assert result.output_tokens == 86
    assert result.total_tokens == 1586
    assert result.tool_calls == [
        {"id": "call_abc", "name": "web_search", "input": {"query": "external-dns 1.22.0"}}
    ]


@pytest.mark.asyncio
async def test_provider_rejection_raises_rather_than_returning_empty(monkeypatch):
    """A rejected model must not look like a successful empty answer.

    pi reports provider rejections on stderr; the JSON stream carries only
    `stopReason: error` with no detail. Returning "" here would present a 400 as
    a model that simply had nothing to say.
    """

    class _FakeStdout:
        def __aiter__(self):
            async def gen():
                if False:
                    yield b""

            return gen()

    class _FakeStderr:
        async def read(self):
            return (
                b'Codex error: {"type":"error","status":400,"error":'
                b'{"message":"The model is not supported when using '
                b'Codex with a ChatGPT account."}}'
            )

    class _FakeProc:
        returncode = 1
        stdout = _FakeStdout()
        stderr = _FakeStderr()

        async def wait(self):
            return 1

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr(pi.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pi, "write_auth", lambda _creds: True)
    monkeypatch.setattr(pi, "ensure_openai_token", _noop_ensure)

    # The message must survive even though a Node crash puts a useless version
    # banner on the last line.
    with pytest.raises(RuntimeError, match="not supported when using Codex"):
        async for _ in pi.stream(
            "sys", [{"role": "user", "content": "hi"}], "gpt-5.4", Credentials()
        ):
            pass
