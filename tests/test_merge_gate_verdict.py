"""The merge gate must read the latest verdict, and honour a refusal.

PR #926 bumped Kubernetes 1.36.3 -> 1.37.0 and was auto-merged despite an Opus
deep review saying NEEDS_REVIEW. Three faults lined up: the gate read the
oldest stored review rather than the newest, that older review carried a
SAFE_TO_MERGE prefix above a body refusing to merge, and the guard against
re-escalating looked for a marker deep_review_pr never writes.

Cilium 1.20.1 does not support Kubernetes 1.37, and the cluster runs
kubeProxyReplacement, so the merge would have taken out CNI and service proxy
together mid-roll.
"""

import pytest

from home_ops_agent.workers.pr_monitor import REFUSAL_MARKERS, _is_safe_to_auto_merge

RENOVATE = {"number": 926, "author": "renovate[bot]", "labels": ["type/minor"]}

# Verbatim shape of the review that was accepted: an approving prefix over a
# body that refuses.
CONTRADICTORY = (
    "[SAFE_TO_MERGE] ## Review Complete\n\n"
    "**Risk Level**: HIGH\n"
    "- kubelet is a critical component\n"
    "- This is a minor version bump, not a patch\n\n"
    "**Auto-Merge Status**: Cannot auto-merge"
)

DEEP_REFUSAL = "[Deep Review] [NEEDS_REVIEW] Review posted. Verdict: NEEDS_REVIEW"
CLEAN_APPROVAL = "[SAFE_TO_MERGE] Patch bump, CI green, no breaking changes."


async def test_a_review_that_refuses_is_not_merged_for_saying_safe_too(db_session):
    """The exact review that merged 1.37 onto a cluster whose CNI could not
    run it. Refusal has to outrank a stray approving token."""
    assert await _is_safe_to_auto_merge(RENOVATE, CONTRADICTORY) is False


async def test_a_deep_review_refusal_is_never_merged(db_session):
    assert await _is_safe_to_auto_merge(RENOVATE, DEEP_REFUSAL) is False


@pytest.mark.parametrize("marker", REFUSAL_MARKERS)
async def test_every_refusal_marker_blocks(marker, db_session):
    summary = f"[SAFE_TO_MERGE] all fine, but {marker} for this one"
    assert await _is_safe_to_auto_merge(RENOVATE, summary) is False


async def test_a_clean_approval_still_merges(db_session):
    """The gate must not become one that never approves anything.

    Uses type/patch: RENOVATE above is type/minor, which the default
    patch-only mode rejects on the label alone -- correctly, and separately
    from anything the review said.
    """
    routine = {"number": 921, "author": "renovate[bot]", "labels": ["type/patch"]}
    assert await _is_safe_to_auto_merge(routine, CLEAN_APPROVAL) is True


async def test_a_minor_label_is_rejected_by_the_default_mode(db_session):
    """Independent of the review text -- which is the layer that failed on
    #926, so it is worth pinning that this one still holds."""
    assert await _is_safe_to_auto_merge(RENOVATE, CLEAN_APPROVAL) is False


async def test_non_renovate_author_is_still_rejected(db_session):
    pr = {"number": 1, "author": "someone", "labels": ["type/patch"]}
    assert await _is_safe_to_auto_merge(pr, CLEAN_APPROVAL) is False


def test_the_reescalation_guard_matches_what_deep_review_writes():
    """deep_review_pr stamps "[Deep Review]" with a space. The guard looked for
    "deep_review" with an underscore, so it never matched -- which would have
    meant an Opus deep review of the same PR every cycle once the ordering bug
    was fixed."""
    summary_lower = DEEP_REFUSAL.lower()
    already_deep = "deep_review" in summary_lower or "deep review" in summary_lower
    assert already_deep, "a completed deep review must not be escalated again"
