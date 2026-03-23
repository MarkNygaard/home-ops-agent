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
