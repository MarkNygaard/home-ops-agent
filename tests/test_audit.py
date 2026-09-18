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


def test_both_tool_paths_record():
    """pi reaches the same registry over a Unix socket rather than through
    `core._execute_tool`. Recording in only one place would make the log blind
    to every GPT run — the runs where the tools came from elsewhere."""
    from home_ops_agent.agent import core, tool_bridge

    assert "audit.record" in inspect.getsource(core.Agent._execute_tool)
    assert "audit.record" in inspect.getsource(tool_bridge._handle)


def test_a_failing_write_is_still_recorded():
    """The error path is the one worth having. A restart that raised is a
    change that was attempted, and the log exists to show attempts."""
    src = inspect.getsource(
        __import__("home_ops_agent.agent.core", fromlist=["x"]).Agent._execute_tool
    )
    # The record call sits after the try/except, not inside the success branch.
    assert src.index("except Exception") < src.index("audit.record")
