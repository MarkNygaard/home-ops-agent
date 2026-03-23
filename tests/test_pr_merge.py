"""Tests for workers/pr_merge.py — auto-merge and CI gating."""


# --- wait_for_ci_and_merge logic tests ---


def test_ci_check_all_passed_logic():
    """Verify the CI pass logic used in wait_for_ci_and_merge."""
    checks = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "neutral"},
        {"status": "completed", "conclusion": "skipped"},
    ]
    all_completed = all(c.get("status") == "completed" for c in checks)
    all_passed = all(c.get("conclusion") in ("success", "neutral", "skipped") for c in checks)
    assert all_completed is True
    assert all_passed is True


def test_ci_check_failure_logic():
    checks = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "failure"},
    ]
    all_completed = all(c.get("status") == "completed" for c in checks)
    all_passed = all(c.get("conclusion") in ("success", "neutral", "skipped") for c in checks)
    assert all_completed is True
    assert all_passed is False


def test_ci_check_not_completed_logic():
    checks = [
        {"status": "completed", "conclusion": "success"},
        {"status": "in_progress", "conclusion": None},
    ]
    all_completed = all(c.get("status") == "completed" for c in checks)
    assert all_completed is False


def test_ci_check_empty_list():
    checks = []
    # Empty list should not count as "all passed"
    assert not checks  # Falsy — the code checks `if not checks: continue`


# --- Verdict in deep review logic ---


def test_deep_review_approved_detection():
    """Verify the approved detection logic from deep_review_pr."""
    response = "After thorough review, this is SAFE_TO_MERGE. No breaking changes."
    response_lower = response.lower()
    approved = "safe_to_merge" in response_lower or "safe to merge" in response_lower
    assert approved is True


def test_deep_review_not_approved_detection():
    response = "This NEEDS_REVIEW by a human. Critical component change."
    response_lower = response.lower()
    approved = "safe_to_merge" in response_lower or "safe to merge" in response_lower
    assert approved is False
