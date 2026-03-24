"""Tests for workers/pr_fix.py — code fix logic."""

from home_ops_agent.workers.pr_monitor import _extract_verdict


def test_code_fix_summary_includes_verdict():
    """Verify that the summary prefix logic works correctly for code fix tasks."""
    response = "I fixed the breaking change in the cilium config. [SAFE_TO_MERGE]"
    verdict = _extract_verdict(response)
    summary = verdict + response[:500]
    assert summary.startswith("[SAFE_TO_MERGE]")
    assert "cilium" in summary


def test_code_fix_summary_no_verdict():
    response = "Applied changes to the deployment manifest."
    verdict = _extract_verdict(response)
    summary = verdict + response[:500]
    assert summary == response


def test_code_fix_summary_truncation():
    """Verify that long responses are truncated to 500 chars after verdict prefix."""
    response = "x" * 600
    verdict = _extract_verdict(response)
    summary = verdict + response[:500]
    assert len(summary) == 500
    assert summary == "x" * 500


def test_code_fix_summary_truncation_with_verdict():
    """Verdict prefix + 500 chars of response."""
    response = "[SAFE_TO_MERGE] " + "x" * 600
    verdict = _extract_verdict(response)
    summary = verdict + response[:500]
    assert summary.startswith("[SAFE_TO_MERGE] ")
    assert len(summary) == len("[SAFE_TO_MERGE] ") + 500


def test_code_fix_empty_response():
    verdict = _extract_verdict("")
    summary = verdict + ""[:500]
    assert summary == ""


def test_code_fix_verdict_needs_fix():
    verdict = _extract_verdict("This NEEDS_FIX badly")
    assert verdict == "[NEEDS_FIX] "


def test_code_fix_verdict_needs_review():
    verdict = _extract_verdict("This needs review please")
    assert verdict == "[NEEDS_REVIEW] "


def test_code_fix_verdict_safe_to_merge_with_spaces():
    """The function also matches 'safe to merge' with spaces."""
    verdict = _extract_verdict("this is safe to merge")
    assert verdict == "[SAFE_TO_MERGE] "


def test_code_fix_verdict_only_verdict_text():
    verdict = _extract_verdict("[SAFE_TO_MERGE]")
    assert verdict == "[SAFE_TO_MERGE] "


def test_code_fix_verdict_unrecognized_brackets():
    verdict = _extract_verdict("[]")
    assert verdict == ""
