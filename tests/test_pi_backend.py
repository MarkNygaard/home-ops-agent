"""Tests for the pi harness.

These cover the parts that are cheap to get wrong and expensive to notice: the
safety flags on the command line, the credential file pi reads, and the parsing
of pi's event stream. Running pi itself is not attempted — that needs a live
provider and belongs in a manual check.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime

import pytest

from home_ops_agent.agent import pi
from home_ops_agent.auth.credentials import Credentials


def _stdout(events=()):
    """pi's stdout as a real StreamReader.

    These used to build a hand-rolled async iterator, which could not
    reach the 64 KiB per-line limit that killed a live chat -- the tests
    passed while the read path they stood for could not read a large tool
    result.
    """
    reader = asyncio.StreamReader()
    for event in events:
        reader.feed_data(json.dumps(event).encode() + b"\n")
    reader.feed_eof()
    return reader


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

    class _FakeStderr:
        async def read(self):
            return b""

    class _FakeProc:
        returncode = 0
        stdout = _stdout(events)
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

    class _FakeStderr:
        async def read(self):
            return b"OAuth refresh failed for openai-codex: 401 refresh_token_reused"

    class _FakeProc:
        returncode = 0
        stdout = _stdout()
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

    class _FakeStderr:
        async def read(self):
            return b""

    class _FakeProc:
        returncode = 0
        stdout = _stdout()
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

    class _FakeStderr:
        async def read(self):
            return b""

    class _FakeProc:
        returncode = 0
        stdout = _stdout(events)
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
    # `tool`, matching every other backend -- the MCP server reads `c.get("tool")`
    # and the chat UI renders `tc.tool`, so a `name` key here renders as a blank
    # chip rather than as an error.
    assert result.tool_calls == [{"tool": "web_search", "input": {"query": "external-dns 1.22.0"}}]


@pytest.mark.asyncio
async def test_provider_rejection_raises_rather_than_returning_empty(monkeypatch):
    """A rejected model must not look like a successful empty answer.

    pi reports provider rejections on stderr; the JSON stream carries only
    `stopReason: error` with no detail. Returning "" here would present a 400 as
    a model that simply had nothing to say.
    """

    class _FakeStderr:
        async def read(self):
            return (
                b'Codex error: {"type":"error","status":400,"error":'
                b'{"message":"The model is not supported when using '
                b'Codex with a ChatGPT account."}}'
            )

    class _FakeProc:
        returncode = 1
        stdout = _stdout()
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


def test_every_shipped_extension_is_loadable_as_one():
    """`_extension_args` globs `extensions/*.ts` and passes each one with `-e`.

    So a shared helper or a types file dropped in that directory is not a
    neighbour of the extensions — it is loaded *as* an extension, and pi reports
    `Failed to load extension` for it on every single run.

    There is one extension now. The tools that used to be written here are the
    agent's own, reached over the bridge, because a third of them carry
    credentials this process must not hold and the rest would otherwise exist
    twice.
    """
    from pathlib import Path

    extensions = Path(__file__).resolve().parents[1] / "extensions"
    shipped = sorted(p.name for p in extensions.glob("*.ts"))
    assert shipped == ["bridge.ts"]
    for path in extensions.glob("*.ts"):
        assert "export default" in path.read_text(encoding="utf-8"), path.name


class _Silent:
    """A pi subprocess that emits nothing and exits cleanly."""

    returncode = 0

    class _Stderr:
        async def read(self):
            return b""

    def __init__(self):
        self.stdout = _stdout()
        self.stderr = self._Stderr()

    async def wait(self):
        return 0


async def _capture_env(monkeypatch, workspace, tools=None):
    """Run stream() to completion and return the env pi was launched with."""
    captured: dict = {}

    async def _fake_exec(*_args, **kwargs):
        captured.update(kwargs.get("env") or {})
        return _Silent()

    monkeypatch.setattr(pi.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pi, "write_auth", lambda _creds: True)
    monkeypatch.setattr(pi, "ensure_openai_token", _noop_ensure)

    async for _ in pi.stream(
        "sys", [{"role": "user", "content": "hi"}], "gpt-6-astra", Credentials(), workspace, tools
    ):
        pass
    return captured


@pytest.mark.asyncio
async def test_no_tools_means_no_socket(monkeypatch):
    """A run given no tools opens nothing.

    Not merely tidiness: the socket is a live path into the agent's registry,
    and it should exist for exactly as long as something needs it.
    """
    from home_ops_agent.agent import tool_bridge

    env = await _capture_env(monkeypatch, None, tools=[])
    assert tool_bridge.SOCKET_ENV not in env
    assert tool_bridge.TOKEN_ENV not in env


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32", reason="the bridge is a Unix socket; the agent runs on Linux"
)
async def test_tools_are_served_and_no_secret_goes_with_them(monkeypatch, tmp_path):
    """The point of the bridge, pinned.

    pi has a `bash` tool, so anything in this environment is readable by the
    model. A GitHub token here would let it push directly, past
    ALLOWED_COMMIT_PATHS — which is what handing the registry over as native
    TypeScript would have required.
    """
    from home_ops_agent.agent import tool_bridge
    from home_ops_agent.agent.core import ToolDefinition
    from home_ops_agent.agent.workspace import Workspace

    secret = "ghp_thisisthepushtoken"
    monkeypatch.setenv("GITHUB_TOKEN", secret)

    async def _handler(_params):
        return "ok"

    tool = ToolDefinition(name="k8s_get_pods", description="d", input_schema={}, handler=_handler)
    ws = Workspace(path=tmp_path, branch="renovate/chart", token=secret)

    env = await _capture_env(monkeypatch, ws, tools=[tool])

    assert env[tool_bridge.SOCKET_ENV].endswith(".sock")
    assert env[tool_bridge.TOKEN_ENV]
    assert "GITHUB_TOKEN" not in env
    assert not any(secret in str(v) for v in env.values())


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32", reason="the bridge is a Unix socket; the agent runs on Linux"
)
async def test_the_socket_does_not_outlive_the_run(monkeypatch, tmp_path):
    """A token read out of the environment must be worthless afterwards."""
    from pathlib import Path

    from home_ops_agent.agent import tool_bridge
    from home_ops_agent.agent.core import ToolDefinition

    async def _handler(_params):
        return "ok"

    env = await _capture_env(
        monkeypatch,
        None,
        tools=[ToolDefinition(name="t", description="d", input_schema={}, handler=_handler)],
    )
    assert not Path(env[tool_bridge.SOCKET_ENV]).exists()


def test_tool_calls_use_the_same_key_as_every_other_backend():
    """`tool`, not `name`.

    core.py and claude_code.py both emit {"tool", "input"}, the MCP server reads
    `c.get("tool")` and the chat UI renders `tc.tool`. A `name` key here is not
    an error anywhere -- it just renders as a blank chip and a null tool name,
    which is why it survived from 0.14.0 unnoticed.
    """
    import inspect

    from home_ops_agent.agent import claude_code, core

    for module in (core, claude_code):
        assert '{"tool":' in inspect.getsource(module), module.__name__
    assert '{"tool":' in inspect.getsource(pi)
    assert '"name": event.get("toolName")' not in inspect.getsource(pi)


def test_no_secret_of_this_process_reaches_pi(monkeypatch):
    """pi has a `bash` tool, so its environment is readable by the model.

    Every name here was actually being passed at one point, measured in the
    cluster: GITHUB_TOKEN (40 chars), DATABASE_URL (139), SESSION_SECRET (44),
    MCP_API_TOKEN (64), NTFY_TOKEN (32).
    """
    for name in (
        "GITHUB_TOKEN",
        "DATABASE_URL",
        "SESSION_SECRET",
        "MCP_API_TOKEN",
        "NTFY_TOKEN",
        "ANTHROPIC_CLIENT_SECRET",
        "GPG_KEY",
    ):
        monkeypatch.setenv(name, "s3cret")

    env = pi.build_env()
    assert "s3cret" not in env.values()
    for name in ("GITHUB_TOKEN", "DATABASE_URL", "SESSION_SECRET", "MCP_API_TOKEN", "NTFY_TOKEN"):
        assert name not in env, name


def test_the_allowlist_is_a_list_not_a_filter(monkeypatch):
    """A secret added to the deployment tomorrow must be excluded by default.

    Denying a known list would mean every future variable is passed until
    someone remembers to block it; this is the other way round.
    """
    monkeypatch.setenv("SOME_FUTURE_API_KEY", "nope")
    assert "SOME_FUTURE_API_KEY" not in pi.build_env()


def test_the_extensions_still_get_what_they_need(monkeypatch):
    """Trimming the environment must not quietly disable the tools.

    Without SEARXNG_URL the web_search tool is not registered at all, and
    without KUBERNETES_SERVICE_HOST none of the cluster tools are — both fail
    silently by design, which is exactly how this would go unnoticed.
    """
    monkeypatch.setenv("SEARXNG_URL", "http://searxng.productivity.svc.cluster.local:8080")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.43.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    monkeypatch.setenv("PATH", "/usr/local/bin")

    env = pi.build_env()
    assert env["SEARXNG_URL"].endswith(":8080")
    assert env["KUBERNETES_SERVICE_HOST"] == "10.43.0.1"
    assert env["KUBERNETES_SERVICE_PORT_HTTPS"] == "443"
    assert env["PATH"]
    assert env["HOME"] == str(pi.PI_HOME)


def test_run_scoped_values_are_passed_through():
    """The bridge's socket and token are minted per run, not secrets of this
    process, so they ride in `extra` rather than the allowlist."""
    env = pi.build_env({"HOMEOPS_WORKSPACE_SOCKET": "/tmp/x/ws.sock"})
    assert env["HOMEOPS_WORKSPACE_SOCKET"] == "/tmp/x/ws.sock"


@pytest.mark.asyncio
async def test_an_oversized_event_does_not_kill_the_run():
    """The chat died with "Separator is found, but chunk is longer than limit".

    pi embeds whole tool results in its event stream, so one line carries a pod
    list or a page of logs. asyncio's default limit is 64 KiB per line and
    exceeding it raises out of the read loop — asking the cluster for its
    health was enough. The oversized line is dropped now; the ones around it
    still arrive.
    """
    reader = asyncio.StreamReader(limit=256)
    reader.feed_data(b'{"type": "first"}\n')
    reader.feed_data(b'{"type": "huge", "payload": "' + b"x" * 4096 + b'"}\n')
    reader.feed_data(b'{"type": "last"}\n')
    reader.feed_eof()

    kinds = [event.get("type") async for event in pi._events(reader)]

    assert kinds == ["first", "last"]


@pytest.mark.asyncio
async def test_a_non_json_line_is_skipped_not_fatal():
    reader = asyncio.StreamReader()
    reader.feed_data(b"Debugger listening on ws://127.0.0.1:9229\n")
    reader.feed_data(b'{"type": "message_end"}\n')
    reader.feed_data(b"\n")
    reader.feed_eof()

    kinds = [event.get("type") async for event in pi._events(reader)]

    assert kinds == ["message_end"]


def test_the_subprocess_gets_the_raised_limit():
    """Dropping an event is the fallback, not the plan: the limit is raised so
    an ordinary tool result never reaches it."""
    import inspect

    source = inspect.getsource(pi._drive)
    assert "limit=STREAM_LIMIT" in source
    assert pi.STREAM_LIMIT >= 8 * 1024 * 1024
