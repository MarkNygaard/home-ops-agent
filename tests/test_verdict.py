"""Tests for the shared verdict parser.

This is now the single thing that decides whether a PR merges, gets fixed, or
waits for a human. The cases that matter are the ones where the old readers
disagreed with each other, and the ones where a passing mention used to be
mistaken for a decision.
"""

from __future__ import annotations

import pytest

from home_ops_agent.workers import verdict as verdict_mod


def test_the_structured_block_is_read():
    v = verdict_mod.parse("Looks fine to me.\n\nSAFE_TO_MERGE: yes\nFIXABLE: no")
    assert v.safe_to_merge is True
    assert v.structured is True


def test_markdown_decoration_does_not_defeat_it():
    """The model is writing a PR comment, not filling in a form.

    Insisting on one exact spelling would fail closed in a way nobody notices
    until fixes silently stop happening.
    """
    for text in (
        "**SAFE_TO_MERGE:** no\n**FIXABLE:** yes",
        "- safe_to_merge: NO\n- fixable: YES",
        "> SAFE_TO_MERGE: false\n> FIXABLE: true",
    ):
        v = verdict_mod.parse(text)
        assert v.structured is True, text
        assert v.safe_to_merge is False, text
        assert v.fixable is True, text


def test_a_mention_is_not_a_decision():
    """The old dispatch matched a bare substring anywhere in the response, so
    "I considered NEEDS_FIX but the change is cosmetic" started a code fix."""
    text = "I considered NEEDS_FIX but the change is cosmetic.\n\nSAFE_TO_MERGE: yes\nFIXABLE: no"
    v = verdict_mod.parse(text)
    assert v.safe_to_merge is True
    assert v.fixable is False


def test_safe_and_fixable_together_just_means_safe():
    """Nothing to fix on a PR that can merge as it stands, and dispatching a
    code fix for one would be an expensive no-op."""
    v = verdict_mod.parse("SAFE_TO_MERGE: yes\nFIXABLE: yes")
    assert v.safe_to_merge is True
    assert v.fixable is False


# --- legacy responses -------------------------------------------------------


def test_a_self_contradicting_legacy_review_is_not_safe():
    """The bug this module exists to remove.

    `_extract_verdict` checked safe_to_merge first and labelled this
    [SAFE_TO_MERGE]; `_is_safe_to_auto_merge` treated the refusal as a veto and
    refused to merge it. The label and the behaviour disagreed. The veto wins.
    """
    v = verdict_mod.parse("This would be safe_to_merge, but needs_review first.")
    assert v.safe_to_merge is False
    assert v.structured is False


def test_stored_prose_reviews_still_parse():
    """auto_merge_reviewed_prs reads summaries written before this existed, out
    of the database, on a later cycle. A parser that only understood the block
    would treat every one of them as 'not safe' and quietly stop merging."""
    assert verdict_mod.parse("Verdict: SAFE_TO_MERGE").safe_to_merge is True
    assert verdict_mod.parse("NEEDS_FIX: the values key was renamed").fixable is True


def test_no_verdict_at_all_is_distinct_from_declining():
    """A run that produced nothing readable is not the same as a review that
    said no, and the history should not invent one."""
    for text in ("", "This is just a regular comment about the PR."):
        v = verdict_mod.parse(text)
        assert v.stated is False
        assert v.label == ""
        assert v.safe_to_merge is False
        assert v.fixable is False


def test_labels_match_what_the_history_showed_before():
    assert verdict_mod.parse("SAFE_TO_MERGE: yes\nFIXABLE: no").label == "[SAFE_TO_MERGE] "
    assert verdict_mod.parse("SAFE_TO_MERGE: no\nFIXABLE: yes").label == "[NEEDS_FIX] "
    assert verdict_mod.parse("SAFE_TO_MERGE: no\nFIXABLE: no").label == "[NEEDS_REVIEW] "


@pytest.mark.parametrize("marker", verdict_mod.REFUSAL_MARKERS)
def test_every_refusal_marker_vetoes_approval(marker):
    v = verdict_mod.parse(f"safe to merge. {marker}.")
    assert v.safe_to_merge is False, marker
