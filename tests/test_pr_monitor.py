"""Tests for workers/pr_monitor.py — PR review logic."""

from unittest.mock import AsyncMock, patch

import pytest

from home_ops_agent.workers.pr_monitor import _extract_verdict

# --- _extract_verdict() pure function tests ---


def test_extract_verdict_safe_to_merge():
    text = "This PR is [SAFE_TO_MERGE]. No breaking changes."
    assert _extract_verdict(text) == "[SAFE_TO_MERGE] "


def test_extract_verdict_safe_to_merge_spaces():
    assert _extract_verdict("I deem this safe to merge based on review.") == "[SAFE_TO_MERGE] "


def test_extract_verdict_needs_review():
    assert _extract_verdict("This [NEEDS_REVIEW] by a human.") == "[NEEDS_REVIEW] "


def test_extract_verdict_needs_review_spaces():
    assert _extract_verdict("This needs review by the user.") == "[NEEDS_REVIEW] "


def test_extract_verdict_needs_fix():
    assert _extract_verdict("The PR [NEEDS_FIX] — breaking change detected.") == "[NEEDS_FIX] "


def test_extract_verdict_empty_string():
    assert _extract_verdict("") == ""


def test_extract_verdict_no_match():
    assert _extract_verdict("This is just a regular comment about the PR.") == ""


def test_extract_verdict_case_insensitive():
    assert _extract_verdict("SAFE_TO_MERGE is my verdict") == "[SAFE_TO_MERGE] "
    assert _extract_verdict("Safe_to_merge") == "[SAFE_TO_MERGE] "


def test_a_self_contradicting_review_is_not_labelled_safe():
    """Deliberately reversed. This used to assert [SAFE_TO_MERGE].

    The label checked safe_to_merge first while the merge gate treated any
    refusal marker as a veto, so a review saying both was *filed* as safe and
    *refused* a merge — and, once the code-fix branch existed, could be filed as
    safe while a fix ran. One parser now serves both, using the gate's
    precedence, which was the careful one.
    """
    assert _extract_verdict("safe_to_merge and also needs_review") == "[NEEDS_REVIEW] "


def test_extract_verdict_needs_fix_priority():
    """needs_fix takes priority over needs_review since it's checked second."""
    result = _extract_verdict("needs_fix and also needs_review")
    assert result == "[NEEDS_FIX] "


# --- _is_safe_to_auto_merge() tests ---


async def test_is_safe_to_auto_merge_wrong_author():
    from home_ops_agent.workers.pr_monitor import _is_safe_to_auto_merge

    pr = {"author": "human-user", "labels": ["type/patch"]}
    assert await _is_safe_to_auto_merge(pr, "[SAFE_TO_MERGE] looks good") is False


async def test_is_safe_to_auto_merge_not_safe_verdict():
    from home_ops_agent.workers.pr_monitor import _is_safe_to_auto_merge

    with patch(
        "home_ops_agent.workers.pr_monitor._get_pr_mode",
        new_callable=AsyncMock,
        return_value="auto_merge",
    ):
        pr = {"author": "renovate[bot]", "labels": ["type/patch"]}
        assert await _is_safe_to_auto_merge(pr, "[NEEDS_REVIEW] risky") is False


async def test_is_safe_to_auto_merge_patch_mode():
    from home_ops_agent.workers.pr_monitor import _is_safe_to_auto_merge

    with patch(
        "home_ops_agent.workers.pr_monitor._get_pr_mode",
        new_callable=AsyncMock,
        return_value="auto_merge",
    ):
        pr = {"author": "renovate[bot]", "labels": ["type/patch"]}
        assert await _is_safe_to_auto_merge(pr, "[SAFE_TO_MERGE] good") is True


async def test_is_safe_to_auto_merge_patch_mode_wrong_label():
    from home_ops_agent.workers.pr_monitor import _is_safe_to_auto_merge

    with patch(
        "home_ops_agent.workers.pr_monitor._get_pr_mode",
        new_callable=AsyncMock,
        return_value="auto_merge",
    ):
        pr = {"author": "renovate[bot]", "labels": ["type/minor"]}
        assert await _is_safe_to_auto_merge(pr, "[SAFE_TO_MERGE] good") is False


async def test_is_safe_to_auto_merge_minor_mode():
    from home_ops_agent.workers.pr_monitor import _is_safe_to_auto_merge

    with patch(
        "home_ops_agent.workers.pr_monitor._get_pr_mode",
        new_callable=AsyncMock,
        return_value="auto_merge_minor",
    ):
        pr = {"author": "renovate[bot]", "labels": ["type/minor"]}
        assert await _is_safe_to_auto_merge(pr, "[SAFE_TO_MERGE] good") is True


async def test_is_safe_to_auto_merge_all_mode():
    from home_ops_agent.workers.pr_monitor import _is_safe_to_auto_merge

    with patch(
        "home_ops_agent.workers.pr_monitor._get_pr_mode",
        new_callable=AsyncMock,
        return_value="auto_merge_all",
    ):
        pr = {"author": "renovate[bot]", "labels": ["type/major"]}
        assert await _is_safe_to_auto_merge(pr, "[SAFE_TO_MERGE] good") is True


async def test_is_safe_to_auto_merge_all_mode_needs_review():
    from home_ops_agent.workers.pr_monitor import _is_safe_to_auto_merge

    with patch(
        "home_ops_agent.workers.pr_monitor._get_pr_mode",
        new_callable=AsyncMock,
        return_value="auto_merge_all",
    ):
        pr = {"author": "renovate[bot]", "labels": ["type/major"]}
        assert await _is_safe_to_auto_merge(pr, "[NEEDS_REVIEW] risky") is False


async def test_is_safe_to_auto_merge_digest_label():
    from home_ops_agent.workers.pr_monitor import _is_safe_to_auto_merge

    with patch(
        "home_ops_agent.workers.pr_monitor._get_pr_mode",
        new_callable=AsyncMock,
        return_value="auto_merge",
    ):
        pr = {"author": "renovate[bot]", "labels": ["type/digest"]}
        assert await _is_safe_to_auto_merge(pr, "safe to merge") is True


# --- cycle summaries (so a no-op is distinguishable from a failure) ---


async def test_check_prs_reports_disabled(monkeypatch):
    """A switched-off agent must not look like a successful empty run."""
    from home_ops_agent.workers import pr_monitor

    monkeypatch.setattr(pr_monitor, "_is_enabled", AsyncMock(return_value=False))

    assert (await pr_monitor.check_prs())["status"] == "disabled"


async def test_check_prs_reports_missing_credentials(monkeypatch):
    from home_ops_agent.auth.credentials import Credentials
    from home_ops_agent.workers import pr_monitor

    monkeypatch.setattr(pr_monitor, "_is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(pr_monitor, "build_credentials", AsyncMock(return_value=Credentials()))

    assert (await pr_monitor.check_prs())["status"] == "no_credentials"


async def test_check_prs_reports_no_open_prs(monkeypatch):
    from home_ops_agent.auth.credentials import Credentials
    from home_ops_agent.workers import pr_monitor

    monkeypatch.setattr(pr_monitor, "_is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        pr_monitor,
        "build_credentials",
        AsyncMock(return_value=Credentials(kimi_api_key="k")),
    )
    monkeypatch.setattr(pr_monitor.registry, "get_all_enabled_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr("home_ops_agent.agent.tools.github.list_prs", AsyncMock(return_value="[]"))

    result = await pr_monitor.check_prs()
    assert result["status"] == "no_open_prs"
    assert result["open_prs"] == 0


async def test_check_prs_counts_failed_reviews(monkeypatch):
    """A model with no credentials fails every review; the count must show it."""
    import json as _json

    from home_ops_agent.auth.credentials import Credentials
    from home_ops_agent.workers import pr_monitor

    monkeypatch.setattr(pr_monitor, "_is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        pr_monitor,
        "build_credentials",
        AsyncMock(return_value=Credentials(kimi_api_key="k")),
    )
    monkeypatch.setattr(pr_monitor.registry, "get_all_enabled_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(pr_monitor, "_get_pr_mode", AsyncMock(return_value="comment_only"))
    monkeypatch.setattr(
        "home_ops_agent.agent.tools.github.list_prs",
        AsyncMock(return_value=_json.dumps([{"number": 1, "title": "t", "author": "a"}])),
    )
    # _review_pr swallows its own exceptions and returns None.
    monkeypatch.setattr(pr_monitor, "_review_pr", AsyncMock(return_value=None))

    result = await pr_monitor.check_prs()

    assert result["status"] == "completed"
    assert result["open_prs"] == 1
    assert result["reviewed"] == 0
    assert result["failed"] == 1


async def test_scheduled_cycle_publishes_its_result(monkeypatch):
    """The dashboard should reflect the cycle that runs all day, not only Run now."""
    from home_ops_agent.api import status as status_api
    from home_ops_agent.workers import pr_monitor

    status_api._pr_check_last_result = None
    pr_monitor._record_cycle_result({"status": "completed", "reviewed": 2, "failed": 0})

    assert status_api._pr_check_last_result["status"] == "completed"
    assert status_api._pr_check_last_result["reviewed"] == 2
    assert "at" in status_api._pr_check_last_result


# --- head_sha: the field that was never there ------------------------------


def test_list_prs_returns_what_the_monitor_needs():
    """`check_prs` feeds these dicts straight into the review without
    re-fetching, so a field missing here is missing for the whole cycle.

    `head_sha` was absent, which meant every PR compared as "already reviewed at
    the commit I do not know about" and was skipped forever, whatever was pushed
    to it. `head_ref` was absent too, so a code fix could never open a checkout —
    `can_use_workspace` rejects the branch "unknown".
    """
    import inspect

    from home_ops_agent.agent.tools import github

    source = inspect.getsource(github.list_prs)
    for field in ('"head_sha"', '"head_ref"'):
        assert field in source, field


@pytest.mark.asyncio
async def test_an_unknown_sha_is_never_already_reviewed():
    """Empty compares equal to the empty string an earlier run stored, so the
    answer was yes — and the PR was never reviewed again."""
    from home_ops_agent.workers.pr_monitor import _already_reviewed

    assert await _already_reviewed(1057, "") is False


@pytest.mark.asyncio
async def test_an_unknown_sha_has_no_review_summary():
    """This is what auto_merge_reviewed_prs merges on. Matching a review of some
    other commit means a stale SAFE_TO_MERGE can approve code nothing read."""
    from home_ops_agent.workers.pr_monitor import _get_review_summary

    assert await _get_review_summary(1057, "") is None
