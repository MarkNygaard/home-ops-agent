"""Tests for workers/pr_merge.py — auto-merge and CI gating."""

import json

import pytest

from home_ops_agent.workers.pr_merge import (
    PASSING_CONCLUSIONS,
    checks_all_passed,
    is_approved_by_deep_review,
)

# --- checks_all_passed() tests ---


def test_checks_all_passed_success():
    checks = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "neutral"},
        {"status": "completed", "conclusion": "skipped"},
    ]
    assert checks_all_passed(checks) is True


def test_checks_all_passed_failure():
    checks = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "failure"},
    ]
    assert checks_all_passed(checks) is False


def test_checks_all_passed_not_completed():
    checks = [
        {"status": "completed", "conclusion": "success"},
        {"status": "in_progress", "conclusion": None},
    ]
    assert checks_all_passed(checks) is False


def test_checks_all_passed_empty_list():
    assert checks_all_passed([]) is False


def test_passing_conclusions_values():
    assert PASSING_CONCLUSIONS == {"success", "neutral", "skipped"}


# --- is_approved_by_deep_review() tests ---


def test_deep_review_approved_underscore():
    assert is_approved_by_deep_review("This is SAFE_TO_MERGE. No breaking changes.") is True


def test_deep_review_approved_spaces():
    assert is_approved_by_deep_review("This is safe to merge after review.") is True


def test_deep_review_not_approved():
    assert is_approved_by_deep_review("This NEEDS_REVIEW by a human.") is False


def test_deep_review_case_insensitive():
    assert is_approved_by_deep_review("Safe_To_Merge") is True
    assert is_approved_by_deep_review("SAFE TO MERGE") is True


# --- the deep review escalation, and the gate on a pushed fix ---------------


async def _no_sleep(_seconds):
    """wait_for_ci_and_merge polls on a 30s timer; tests must not."""
    return None


@pytest.mark.asyncio
async def test_a_pushed_fix_is_not_merged_without_a_second_opinion(monkeypatch):
    """CI proves the manifests render, not that the fix is right.

    Before this, a fix changed the head SHA and was merged within five minutes —
    long before the next monitor cycle, which is the only thing that would have
    reviewed the new SHA. A semantically wrong but perfectly valid manifest
    merged unseen.
    """
    from home_ops_agent.workers import pr_merge

    merged = False
    notified = []

    async def _checks(_params):
        return json.dumps([{"status": "completed", "conclusion": "success"}])

    async def _get_pr(_params):
        return json.dumps({"head_sha": "newsha", "title": "t", "head_ref": "b", "html_url": ""})

    async def _merge(_params):
        nonlocal merged
        merged = True
        return json.dumps({"status": "merged"})

    async def _review(_pr_number, _agent):
        return False, "SAFE_TO_MERGE: no\nFIXABLE: no"

    async def _notify(_level, payload):
        notified.append(payload)

    monkeypatch.setattr("home_ops_agent.agent.tools.github.get_check_runs", _checks)
    monkeypatch.setattr("home_ops_agent.agent.tools.github.get_pr", _get_pr)
    monkeypatch.setattr("home_ops_agent.agent.tools.github.merge_pr", _merge)
    monkeypatch.setattr(pr_merge, "review_fixed_pr", _review)
    monkeypatch.setattr(pr_merge.notifications, "notify", _notify)
    monkeypatch.setattr(pr_merge.asyncio, "sleep", _no_sleep)

    await pr_merge.wait_for_ci_and_merge(1046, "", "t", agent=object())

    assert merged is False
    assert notified and "needs you" in notified[0]["title"]


@pytest.mark.asyncio
async def test_an_approved_fix_still_merges(monkeypatch):
    from home_ops_agent.workers import pr_merge

    merged = False

    async def _checks(_params):
        return json.dumps([{"status": "completed", "conclusion": "success"}])

    async def _get_pr(_params):
        return json.dumps({"head_sha": "newsha", "title": "t", "head_ref": "b", "html_url": ""})

    async def _merge(_params):
        nonlocal merged
        merged = True
        return json.dumps({"status": "merged", "sha": "abc"})

    async def _review(_pr_number, _agent):
        return True, "SAFE_TO_MERGE: yes\nFIXABLE: no"

    async def _notify(_level, _payload):
        return None

    monkeypatch.setattr("home_ops_agent.agent.tools.github.get_check_runs", _checks)
    monkeypatch.setattr("home_ops_agent.agent.tools.github.get_pr", _get_pr)
    monkeypatch.setattr("home_ops_agent.agent.tools.github.merge_pr", _merge)
    monkeypatch.setattr(pr_merge, "review_fixed_pr", _review)
    monkeypatch.setattr(pr_merge.notifications, "notify", _notify)
    monkeypatch.setattr(pr_merge.asyncio, "sleep", _no_sleep)

    await pr_merge.wait_for_ci_and_merge(1046, "", "t", agent=object())
    assert merged is True


@pytest.mark.asyncio
async def test_without_an_agent_the_old_ci_only_behaviour_is_kept(monkeypatch):
    """The parameter is optional so existing callers are unchanged."""
    from home_ops_agent.workers import pr_merge

    merged = False

    async def _checks(_params):
        return json.dumps([{"status": "completed", "conclusion": "success"}])

    async def _get_pr(_params):
        return json.dumps({"head_sha": "newsha", "title": "t"})

    async def _merge(_params):
        nonlocal merged
        merged = True
        return json.dumps({"status": "merged", "sha": "abc"})

    async def _boom(*_a, **_k):
        raise AssertionError("must not re-review without an agent")

    async def _notify(_level, _payload):
        return None

    monkeypatch.setattr("home_ops_agent.agent.tools.github.get_check_runs", _checks)
    monkeypatch.setattr("home_ops_agent.agent.tools.github.get_pr", _get_pr)
    monkeypatch.setattr("home_ops_agent.agent.tools.github.merge_pr", _merge)
    monkeypatch.setattr(pr_merge, "review_fixed_pr", _boom)
    monkeypatch.setattr(pr_merge.notifications, "notify", _notify)
    monkeypatch.setattr(pr_merge.asyncio, "sleep", _no_sleep)

    await pr_merge.wait_for_ci_and_merge(1046, "", "t")
    assert merged is True


def test_deep_review_can_reach_the_fixer():
    """The escalation that did not exist.

    Opus does the research — release notes, upstream changelog, the diff — and
    used to write it on the PR and stop. Nothing picked it up, because the next
    cycle skips any PR whose head SHA is already reviewed.
    """
    import inspect

    from home_ops_agent.workers import pr_merge

    source = inspect.getsource(pr_merge.deep_review_pr)
    assert "attempt_code_fix" in source
    assert "out_of_scope_paths" in source


def test_the_deep_review_prompt_asks_for_the_verdict_block():
    """Without the two lines the parser falls back to markers, and 'fixable' has
    no marker at all — so the escalation would never fire."""
    import inspect

    from home_ops_agent.workers import pr_merge

    source = inspect.getsource(pr_merge.deep_review_pr)
    assert "SAFE_TO_MERGE: yes|no" in source
    assert "FIXABLE: yes|no" in source
