"""Tests for workers/pr_monitor.py — PR review logic."""

from unittest.mock import AsyncMock, patch

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


def test_extract_verdict_priority_safe_over_review():
    """safe_to_merge takes priority since it's checked first."""
    result = _extract_verdict("safe_to_merge and also needs_review")
    assert result == "[SAFE_TO_MERGE] "


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
