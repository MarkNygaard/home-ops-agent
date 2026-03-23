"""Tests for workers/pr_merge.py — auto-merge and CI gating."""

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
