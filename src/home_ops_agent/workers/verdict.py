"""One reading of a review's conclusion, used by everything that branches on it.

Before this there were three readers of the same text, with three different
precedences:

- ``_extract_verdict`` checked ``safe_to_merge`` first, then ``needs_fix``
- the dispatch in ``check_prs`` checked ``needs_fix`` first, and never looked
  for ``safe_to_merge`` at all
- ``_is_safe_to_auto_merge`` treated six refusal markers as a veto, then looked
  for approval

A review containing both tokens -- easy to produce, for example "would be
SAFE_TO_MERGE once CI passes; verdict NEEDS_FIX" -- was therefore filed in the
history as ``[SAFE_TO_MERGE]`` while a code fix actually ran. And because the
dispatch matched a bare substring anywhere in the response, "I considered
NEEDS_FIX but the change is cosmetic" started one too.

**Two orthogonal questions, not three overlapping verdicts.** The old set asked
the model to choose between "requires manifest modifications" and "notable
changes the user should verify", which are both true of essentially every
breaking change -- so it was picking a phrase rather than making a decision.
These two are independent:

    SAFE_TO_MERGE: yes|no   -- can this merge as it stands?
    FIXABLE: yes|no         -- do I know a concrete change that resolves it?

Routing is then a truth table rather than a coin flip.

**The legacy fallback is not optional.** Reviews stored before this shipped are
plain prose, and ``auto_merge_reviewed_prs`` reads those summaries out of the
database on a later cycle. A parser that only understood the new block would
silently treat every one of them as "not safe, not fixable". So an unparseable
response falls back to the old markers -- but using the *careful* precedence,
the one ``_is_safe_to_auto_merge`` had, where any refusal marker vetoes
approval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Any of these, anywhere in a legacy response, means the reviewer declined --
# whatever else the text says. Kept from the original _is_safe_to_auto_merge,
# which was the only one of the three readers that got this right.
REFUSAL_MARKERS = (
    "needs_review",
    "needs review",
    "needs_fix",
    "needs fix",
    "cannot auto-merge",
    "do not merge",
)

_FIX_MARKERS = ("needs_fix", "needs fix")
_SAFE_MARKERS = ("safe_to_merge", "safe to merge")

# `**SAFE_TO_MERGE:** yes`, `SAFE_TO_MERGE: YES`, `- safe_to_merge: yes` all
# count: the model is writing markdown, and insisting on one exact spelling
# would fail closed in a way nobody would notice until a fix silently stopped
# happening.
_FIELD = r"^[\s>*_\-]*{name}[\s*_]*:[\s*_]*(yes|no|true|false)\b"


def _field(text: str, name: str) -> bool | None:
    match = re.search(_FIELD.format(name=name), text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    return match.group(1).lower() in ("yes", "true")


@dataclass(frozen=True)
class Verdict:
    """What a review concluded, and how confidently that was read."""

    safe_to_merge: bool
    fixable: bool
    #: False when the structured block was absent and the markers were used.
    #: Worth surfacing: it means the model did not follow the output format,
    #: which is a prompt problem rather than a PR problem.
    structured: bool
    #: False when the text carried no recognisable verdict signal at all. Kept
    #: distinct from "not safe" so the history can show no verdict rather than
    #: inventing one -- a run that produced nothing readable is a different
    #: thing from a review that declined, and they want different attention.
    stated: bool = True

    @property
    def label(self) -> str:
        """The prefix shown in the task history, so it survives truncation."""
        if not self.stated:
            return ""
        if self.safe_to_merge:
            return "[SAFE_TO_MERGE] "
        if self.fixable:
            return "[NEEDS_FIX] "
        return "[NEEDS_REVIEW] "


def parse(response: str) -> Verdict:
    """Read a review's conclusion. Never raises; an empty response is 'not safe'."""
    text = response or ""

    safe = _field(text, "SAFE_TO_MERGE")
    fixable = _field(text, "FIXABLE")
    if safe is not None and fixable is not None:
        # Both stated: take them at their word, including the combination
        # "safe and fixable", which simply means safe — there is nothing to fix.
        return Verdict(safe_to_merge=safe, fixable=fixable and not safe, structured=True)

    lower = text.lower()
    refused = any(marker in lower for marker in REFUSAL_MARKERS)
    approved = any(m in lower for m in _SAFE_MARKERS)
    return Verdict(
        safe_to_merge=approved and not refused,
        fixable=any(m in lower for m in _FIX_MARKERS),
        structured=False,
        stated=refused or approved,
    )


# The verdict lives in the comment the run posted on the PR, not in whatever it
# said to the terminal afterwards. The prompt asks for the block at the end of
# the review, and the model does exactly that -- then writes a human-facing
# summary as its final message, which is the text everything here used to read.
#
# On PR #1072 that cost a correct call: the deep review posted
# "SAFE_TO_MERGE: yes" on the PR and closed with "I disagree with the
# `NEEDS_REVIEW` flag". No structured block in the final message, so the legacy
# fallback matched the word NEEDS_REVIEW inside the sentence disputing it, and
# the PR was filed as needing attention by the review that had just cleared it.
REVIEW_COMMENT_TOOLS = ("github_create_pr_comment",)


def posted_review(tool_calls: list[dict] | None) -> str:
    """The body of the last review comment this run posted, if any."""
    for call in reversed(tool_calls or []):
        if call.get("tool") in REVIEW_COMMENT_TOOLS:
            body = (call.get("input") or {}).get("body")
            if isinstance(body, str) and body.strip():
                return body
    return ""


def parse_result(result) -> Verdict:
    """Read a run's conclusion, preferring what it published over what it said.

    Falls back to the response text when nothing was posted (a review that
    failed before commenting) or when the comment carried no structured block.
    """
    posted = posted_review(getattr(result, "tool_calls", None))
    if posted:
        verdict = parse(posted)
        if verdict.structured:
            return verdict
    return parse(getattr(result, "response", "") or "")
