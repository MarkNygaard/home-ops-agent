"""Tests for what happens to a PR after its review.

The router decides whether an agent edits and pushes to the cluster repository,
so these are weighted towards the cases where it must *not*.
"""

from __future__ import annotations

import json

import pytest

from home_ops_agent.workers import pr_monitor

PR = {"number": 1046, "title": "chore: bump a chart", "author": "renovate[bot]"}


def _files(*names):
    async def _get_pr_files(_params):
        return json.dumps([{"filename": n} for n in names])

    return _get_pr_files


@pytest.fixture
def routed(monkeypatch):
    """Capture which branch the router took, without running either."""
    calls = {}

    async def _fix(pr, response, _agent):
        calls["fix"] = (pr["number"], response)

    async def _deep(pr, response, _agent):
        calls["deep"] = (pr["number"], response)

    monkeypatch.setattr("home_ops_agent.workers.pr_fix.attempt_code_fix", _fix)
    monkeypatch.setattr("home_ops_agent.workers.pr_merge.deep_review_pr", _deep)
    return calls


@pytest.mark.asyncio
async def test_fixable_and_in_scope_goes_to_the_fixer(routed, monkeypatch):
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files",
        _files("kubernetes/apps/media/jellyfin/app/helmrelease.yaml"),
    )
    await pr_monitor._route(PR, "SAFE_TO_MERGE: no\nFIXABLE: yes", object(), "auto_merge_all")

    assert "fix" in routed
    assert "deep" not in routed


@pytest.mark.asyncio
async def test_fixable_but_out_of_scope_never_reaches_the_fixer(routed, monkeypatch):
    """The router and the commit guard have to agree.

    Before this, a PR touching talos/ was routed to the fixer, which opened a
    checkout, read the repository, made the edit, and only then had
    workspace_commit reject it — a wasted model run ending in a confusing
    failure, when the file list said so up front.
    """
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files",
        _files("talos/talenv.yaml", "kubernetes/apps/media/jellyfin/app/helmrelease.yaml"),
    )
    await pr_monitor._route(PR, "SAFE_TO_MERGE: no\nFIXABLE: yes", object(), "auto_merge_all")

    assert "fix" not in routed
    assert "deep" in routed


@pytest.mark.asyncio
async def test_safe_is_left_to_the_merge_gate(routed, monkeypatch):
    """Merging happens in auto_merge_reviewed_prs, against _is_safe_to_auto_merge.
    The router must not start a second path to the same place."""
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files", _files("kubernetes/apps/x.yaml")
    )
    await pr_monitor._route(PR, "SAFE_TO_MERGE: yes\nFIXABLE: no", object(), "auto_merge_all")

    assert routed == {}


@pytest.mark.asyncio
async def test_not_fixable_escalates_only_in_auto_merge_all(routed, monkeypatch):
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files", _files("kubernetes/apps/x.yaml")
    )
    await pr_monitor._route(PR, "SAFE_TO_MERGE: no\nFIXABLE: no", object(), "auto_merge")
    assert routed == {}

    await pr_monitor._route(PR, "SAFE_TO_MERGE: no\nFIXABLE: no", object(), "auto_merge_all")
    assert "deep" in routed


@pytest.mark.asyncio
async def test_a_passing_mention_no_longer_starts_a_fix(routed, monkeypatch):
    """The old dispatch was a bare substring match on the whole response."""
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files", _files("kubernetes/apps/x.yaml")
    )
    await pr_monitor._route(
        PR,
        "I considered NEEDS_FIX but the change is cosmetic.\nSAFE_TO_MERGE: yes\nFIXABLE: no",
        object(),
        "auto_merge_all",
    )
    assert "fix" not in routed


@pytest.mark.asyncio
async def test_an_unreadable_file_list_does_not_block_fixes(monkeypatch):
    """An API blip must not silently stop fixes happening — the commit guard
    still has the last word, so failing open here costs at most a rejected
    commit, while failing closed would look like the feature quietly dying."""

    async def _boom(_params):
        raise RuntimeError("502 from GitHub")

    monkeypatch.setattr("home_ops_agent.agent.tools.github.get_pr_files", _boom)
    assert await pr_monitor.out_of_scope_paths(1046) == []


@pytest.mark.asyncio
async def test_the_scope_gate_uses_the_same_rule_as_the_commit_guard(monkeypatch):
    """One function, so the two cannot drift apart."""
    from home_ops_agent.agent.workspace import blocked_paths

    paths = ["talos/talenv.yaml", ".github/workflows/ci.yaml", "kubernetes/apps/ok.yaml"]
    monkeypatch.setattr("home_ops_agent.agent.tools.github.get_pr_files", _files(*paths))

    assert await pr_monitor.out_of_scope_paths(1046) == blocked_paths(paths)
