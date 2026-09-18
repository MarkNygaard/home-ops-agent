"""Tests for what happens to a PR after its review.

The router decides whether an agent edits and pushes to the cluster repository,
so these are weighted towards the cases where it must *not*.
"""

from __future__ import annotations

import json

import pytest

from home_ops_agent.workers import pr_monitor

PR = {"number": 1046, "title": "chore: bump a chart", "author": "renovate[bot]"}


def _result(response: str, posted: str | None = None):
    """A review result. `posted` is the comment body it wrote on the PR.

    The router reads the posted comment first, so a test that only sets the
    response is exercising the fallback.
    """
    from home_ops_agent.agent.core import AgentResult

    calls = (
        [{"tool": "github_create_pr_comment", "input": {"pr_number": 1046, "body": posted}}]
        if posted is not None
        else []
    )
    return AgentResult(response=response, tool_calls=calls)


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

    async def _merge(pr):
        calls["merge"] = pr["number"]
        return True

    async def _gate(_pr, _summary):
        # The gate itself is tested in test_pr_monitor; here it is the router's
        # decision to consult it at all that matters.
        calls["gated"] = True
        return True

    monkeypatch.setattr("home_ops_agent.workers.pr_fix.attempt_code_fix", _fix)
    monkeypatch.setattr("home_ops_agent.workers.pr_merge.deep_review_pr", _deep)
    monkeypatch.setattr("home_ops_agent.workers.pr_merge.merge_now", _merge)
    monkeypatch.setattr(pr_monitor, "_is_safe_to_auto_merge", _gate)
    return calls


@pytest.mark.asyncio
async def test_fixable_and_in_scope_goes_to_the_fixer(routed, monkeypatch):
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files",
        _files("kubernetes/apps/media/jellyfin/app/helmrelease.yaml"),
    )
    await pr_monitor._route(
        PR, _result("SAFE_TO_MERGE: no\nFIXABLE: yes"), object(), "auto_merge_all"
    )

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
    await pr_monitor._route(
        PR, _result("SAFE_TO_MERGE: no\nFIXABLE: yes"), object(), "auto_merge_all"
    )

    assert "fix" not in routed
    assert "deep" in routed


@pytest.mark.asyncio
async def test_safe_merges_in_its_own_cycle_when_fully_autonomous(routed, monkeypatch):
    """Deferring the merge to the next cycle surprised the operator three times.

    The gate is the same function either way and the review has just read CI,
    so a clean PR sat for up to an interval while the dashboard said it was
    safe to merge. The deep-review path always merged inline; this makes the
    ordinary path agree.
    """
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files", _files("kubernetes/apps/x.yaml")
    )
    await pr_monitor._route(
        PR, _result("SAFE_TO_MERGE: yes\nFIXABLE: no"), object(), "auto_merge_all"
    )

    assert routed.get("merge") == PR["number"]
    assert routed.get("gated") is True
    # And it is still not a fix or a deep review.
    assert "fix" not in routed
    assert "deep" not in routed


@pytest.mark.asyncio
async def test_the_cautious_modes_still_wait_for_the_next_cycle(routed, monkeypatch):
    """Only fully autonomous merges in-cycle. The other auto-merge modes are
    the cautious settings, and this is the change that makes the agent act
    sooner — so it is not applied to them."""
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files", _files("kubernetes/apps/x.yaml")
    )
    for mode in ("auto_merge", "auto_merge_minor"):
        await pr_monitor._route(PR, _result("SAFE_TO_MERGE: yes\nFIXABLE: no"), object(), mode)

    assert "merge" not in routed


@pytest.mark.asyncio
async def test_not_fixable_escalates_only_in_auto_merge_all(routed, monkeypatch):
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files", _files("kubernetes/apps/x.yaml")
    )
    await pr_monitor._route(PR, _result("SAFE_TO_MERGE: no\nFIXABLE: no"), object(), "auto_merge")
    assert routed == {}

    await pr_monitor._route(
        PR, _result("SAFE_TO_MERGE: no\nFIXABLE: no"), object(), "auto_merge_all"
    )
    assert "deep" in routed


@pytest.mark.asyncio
async def test_a_passing_mention_no_longer_starts_a_fix(routed, monkeypatch):
    """The old dispatch was a bare substring match on the whole response."""
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files", _files("kubernetes/apps/x.yaml")
    )
    await pr_monitor._route(
        PR,
        _result(
            "I considered NEEDS_FIX but the change is cosmetic.\nSAFE_TO_MERGE: yes\nFIXABLE: no"
        ),
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


@pytest.mark.asyncio
async def test_the_verdict_comes_from_the_comment_not_the_closing_summary(routed, monkeypatch):
    """PR #1072, exactly.

    The deep review posted `SAFE_TO_MERGE: yes` on the PR and then closed with
    "I disagree with the `NEEDS_REVIEW` flag". Reading the summary found no
    structured block, fell back to markers, matched NEEDS_REVIEW inside the
    sentence disputing it, and filed the PR as needing attention — by the
    review that had just cleared it.
    """
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files",
        _files("kubernetes/apps/kube-system/snapshot-controller/app/ocirepository.yaml"),
    )

    await pr_monitor._route(
        PR,
        _result(
            "Verdict: safe to merge. I disagree with the `NEEDS_REVIEW` flag.",
            posted="## Review\n\nAll green.\n\nSAFE_TO_MERGE: yes\nFIXABLE: no",
        ),
        object(),
        "auto_merge_all",
    )

    # Safe: merged, neither escalated nor fixed.
    assert routed.get("merge") == PR["number"]
    assert "deep" not in routed
    assert "fix" not in routed


@pytest.mark.asyncio
async def test_a_comment_without_a_verdict_falls_back_to_the_response(routed, monkeypatch):
    """A review that commented without the block must still route on whatever
    it did say."""
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.get_pr_files",
        _files("kubernetes/apps/media/jellyfin/app/helmrelease.yaml"),
    )

    await pr_monitor._route(
        PR,
        _result("SAFE_TO_MERGE: no\nFIXABLE: yes", posted="I looked at it and it is fine."),
        object(),
        "auto_merge_all",
    )

    assert "fix" in routed


def test_the_posted_comment_is_the_last_one():
    """A run may comment more than once; the verdict is the one it ended on."""
    from home_ops_agent.workers import verdict

    calls = [
        {"tool": "github_create_pr_comment", "input": {"body": "SAFE_TO_MERGE: no\nFIXABLE: no"}},
        {"tool": "github_get_pr", "input": {"pr_number": 1}},
        {"tool": "github_create_pr_comment", "input": {"body": "SAFE_TO_MERGE: yes\nFIXABLE: no"}},
    ]
    assert "yes" in verdict.posted_review(calls)


def test_no_comment_means_no_posted_verdict():
    from home_ops_agent.workers import verdict

    assert verdict.posted_review([]) == ""
    assert verdict.posted_review(None) == ""
    assert verdict.posted_review([{"tool": "k8s_get_pods", "input": {}}]) == ""
