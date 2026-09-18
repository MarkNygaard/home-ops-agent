"""Tests for the write audit.

What these pin is the set of tools considered writes (the list is the feature —
a mutating tool missing from it is invisible), that a guardrail refusal is
distinguishable from a failure, and that recording can never break a tool call.
"""

from __future__ import annotations

import inspect
import json

import pytest

from home_ops_agent import audit


def test_every_mutating_tool_is_tracked():
    """The audit is only as good as this list, and the list is hand-written —
    so it is checked against the registry rather than trusted.

    A tool that changes something and is not here does not appear in the log at
    all, which is worse than having no log: the page would say the agent had
    done nothing.
    """
    from home_ops_agent.agent.tools import flux, github, kubernetes

    mutating = {
        # Kubernetes
        "k8s_restart_workload",
        "k8s_delete_pod",
        # Flux
        "flux_reconcile",
        "flux_suspend",
        "flux_resume",
        # GitHub
        "github_create_branch",
        "github_create_commit",
        "github_create_pr",
        "github_create_pr_comment",
        "github_merge_pr",
    }
    registered = (
        {t.name for t in kubernetes.get_kubernetes_tools()}
        | {t.name for t in flux._get_tools({})}
        | {t.name for t in github._get_tools({})}
    )
    # Every name we claim is mutating still exists under that name.
    assert mutating <= registered, mutating - registered
    # And every one of them is tracked.
    assert mutating <= set(audit.WRITE_TOOLS), mutating - set(audit.WRITE_TOOLS)


def test_reads_are_not_recorded():
    """Reads outnumber writes by an order of magnitude. A log that includes
    them is a log nobody reads."""
    for name in ("k8s_get_pods", "k8s_get_pod_logs", "github_get_pr", "prometheus_query"):
        assert not audit.is_write(name)


def test_a_guardrail_refusal_is_not_filed_as_an_error():
    """The most interesting line in the log is the agent trying something it is
    not allowed to do. Filed next to timeouts and typos, it would never be
    found."""
    blocked = json.dumps(
        {"error": "BLOCKED: Cannot delete pods in protected namespace 'kube-system'"}
    )
    assert audit.classify(blocked)[0] == "blocked"

    failed = json.dumps({"error": "Failed to restart: NotFound"})
    assert audit.classify(failed)[0] == "error"

    ok = json.dumps({"status": "ok", "message": "Restarted deployment/x in media"})
    assert audit.classify(ok)[0] == "ok"


def test_a_non_json_result_is_not_mistaken_for_a_failure():
    outcome, detail = audit.classify("committed 1 file")
    assert outcome == "ok"
    assert "committed" in detail


def test_the_target_says_what_was_touched():
    assert (
        audit.describe_target("k8s_restart_workload", {"namespace": "media", "name": "sonarr"})
        == "media sonarr"
    )
    assert audit.describe_target("github_merge_pr", {"pr_number": 1066}) == "1066"
    # Missing arguments are skipped rather than rendered as None.
    assert "None" not in audit.describe_target("flux_reconcile", {"name": "x"})


@pytest.mark.asyncio
async def test_recording_never_raises(monkeypatch):
    """A write that succeeded and went unrecorded is bad. A write refused
    because the audit log was down would be worse — and this log is not what
    decides whether a write happens."""
    import home_ops_agent.database as database

    def _boom(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(database, "async_session", _boom)
    await audit.record("k8s_delete_pod", {"namespace": "media", "name": "x"}, "{}")


@pytest.mark.asyncio
async def test_recording_is_skipped_entirely_for_reads(monkeypatch):
    """Cheap enough that no caller has to check first — and it must not touch
    the database on the read path, which is most of every run."""
    import home_ops_agent.database as database

    def _boom(*_a, **_k):
        raise AssertionError("a read reached the database")

    monkeypatch.setattr(database, "async_session", _boom)
    await audit.record("k8s_get_pods", {"namespace": "media"}, "[]")


def test_every_write_tool_records_itself():
    """The test that would have caught the first version of this log.

    Recording used to live in the dispatchers. There are *three* -- the
    Anthropic loop, the pi socket bridge, and the Claude Code SDK wrapper --
    and the workers call handlers like `merge_pr` directly with no dispatcher
    at all. Two paths were wired, the busiest was not, and the log recorded
    nothing at all through two real PR reviews while looking like it worked.

    So the check is on the handler, which is where all four paths end.
    """
    from home_ops_agent.agent.tools import code_fix, flux, github, kubernetes, ntfy

    tools = {}
    for factory in (
        kubernetes.get_kubernetes_tools(),
        flux._get_tools({}),
        github._get_tools({}),
        ntfy._get_tools({}),
        code_fix._get_tools({}),
    ):
        tools.update({t.name: t for t in factory})

    for name, tool in tools.items():
        marked = getattr(tool.handler, "__audit_tool__", None)
        if name in audit.WRITE_TOOLS:
            assert marked == name, f"{name} mutates and is not recorded"
        else:
            # And the reverse: a read that records would bury the writes.
            assert marked is None, f"{name} is a read and should not be recorded"


def test_the_tools_that_are_not_always_registered_are_decorated_too():
    """workspace_commit exists only while a checkout is open and code_fix can
    be disabled, so neither shows up in a registry snapshot. They are the two
    that change the repository, so they are checked directly."""

    from home_ops_agent.agent import workspace
    from home_ops_agent.agent.tools import code_fix

    assert '@audit.records("workspace_commit")' in inspect.getsource(workspace)
    assert '@audit.records("code_fix")' in inspect.getsource(code_fix)


def test_the_dispatchers_do_not_also_record():
    """Recording in both places would double every row. The handler is the
    single point on purpose."""

    from home_ops_agent.agent import core, tool_bridge

    assert "audit.record(" not in inspect.getsource(core.Agent._execute_tool)
    assert "audit.record(" not in inspect.getsource(tool_bridge._handle)


@pytest.mark.asyncio
async def test_a_worker_calling_a_handler_directly_is_still_recorded(monkeypatch):
    """`pr_merge` imports `merge_pr` and calls it — no agent, no dispatcher.
    That is how the PR that merged today was merged, and it left no trace."""

    recorded: list[tuple] = []

    async def _capture(tool, args, result, **_kw):
        recorded.append((tool, args, result))

    monkeypatch.setattr(audit, "record", _capture)

    async def _fake_merge(params):
        return json.dumps({"status": "ok", "message": "merged"})

    # The decorator is applied at import time, so re-wrap the inner function
    # the same way the module does.
    wrapped = audit.records("github_merge_pr")(_fake_merge)
    await wrapped({"pr_number": 1069})

    assert recorded and recorded[0][0] == "github_merge_pr"
    assert recorded[0][1] == {"pr_number": 1069}


@pytest.mark.asyncio
async def test_a_handler_that_raises_is_recorded_and_still_raises(monkeypatch):
    recorded: list[str] = []

    async def _capture(tool, _args, result, **_kw):
        recorded.append(result)

    monkeypatch.setattr(audit, "record", _capture)

    async def _boom(_params):
        raise RuntimeError("github is down")

    wrapped = audit.records("github_merge_pr")(_boom)
    with pytest.raises(RuntimeError):
        await wrapped({"pr_number": 1})

    assert "github is down" in recorded[0]


@pytest.mark.asyncio
async def test_a_write_is_attributed_without_the_caller_saying_who(monkeypatch):
    """The first real rows in this log all read "unknown".

    The decorator that records sits on the handler and has no idea who called
    it, so a `source` argument defaulting to "unknown" meant every row was
    anonymous — which makes the per-agent filter useless and the log much
    harder to read.
    """
    from home_ops_agent.workers import progress

    captured: dict = {}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, stmt):
            captured.update(stmt.compile().params)

        async def commit(self):
            return None

    import home_ops_agent.database as database

    monkeypatch.setattr(database, "async_session", lambda: _Session())
    progress.begin("pr_review", "1 open PR(s)")
    try:
        await audit.record("github_merge_pr", {"pr_number": 1069}, '{"status": "ok"}')
    finally:
        progress.finish()

    assert captured.get("source") == "pr_review"


def test_the_merge_pass_runs_inside_a_declared_run():
    """It merges what the previous cycle reviewed, and it used to run before
    `progress.begin` — so its step lit nothing on the flow diagram and its
    writes were filed under "unknown"."""
    import inspect

    from home_ops_agent.workers import pr_monitor

    src = inspect.getsource(pr_monitor.check_prs)
    assert src.index('progress.begin("pr_review"') < src.index("auto_merge_reviewed_prs(prs")
