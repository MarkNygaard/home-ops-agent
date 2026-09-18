"""Where a run has got to, so the dashboard can show it moving.

The workflow diagram was a drawing of what *could* happen. This makes it a
picture of what *is* happening: whichever node the run is currently in lights
up, and the operator can watch a PR travel the flow after pressing Run now.

**Polled, not pushed.** A WebSocket was the obvious design and is the wrong one
here. The dashboard already polls ``/api/status``, the only socket in the app is
``/ws/chat`` (per-connection, chat-shaped), and background workers have no
broadcast channel -- so pushing would mean inventing a hub, an endpoint, a
reconnect policy and a fan-out, to move a single short string. The status poll
simply speeds up while a run is in flight and slows down again after.

**One run at a time.** The PR monitor is a single task and refuses to start a
second (``_pr_check_in_flight``), so a module-level record is not a
simplification that will bite later -- it matches what the worker actually
allows. If concurrent runs ever arrive this becomes a dict keyed by run id, and
the snapshot grows a list.

**Steps are named by what happened, not by which node to light.** The mapping
from a step to a circle belongs in the diagram, which is the thing that knows
about circles. That way a step can be reported before any node exists for it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# A run that stops reporting is assumed dead rather than left lit forever. The
# PR monitor's own stale threshold is 15 minutes; this is deliberately a little
# longer, so the UI never contradicts the worker about whether a run is alive.
STALE_AFTER = timedelta(minutes=20)

# Tool calls the review makes, mapped onto the steps the diagram draws. The
# worker cannot report these itself: they happen inside a single model call, and
# which tool is used when is the model's decision, not the worker's.
#
# Unmapped tools leave the step alone. Most of them are detail the diagram does
# not draw, and flickering through every tool call would be noise rather than
# progress.
# The order steps occur in. Used only to stop the indicator going backwards:
# a model that reads the diff and then re-checks CI is doing something
# reasonable, but a progress display that jumps back two circles reads as a bug.
# Observed exactly that on the first live run -- check_pr, read_diff, check_pr.
#
# Anything not listed always applies, so a new step does not need adding here to
# work; it just will not be ordered against the others.
STEP_ORDER: tuple[str, ...] = (
    "start",
    "check_pr",
    "read_diff",
    "release_notes",
    "decide",
    "in_scope",
    "deep_review",
    "code_fix",
    "re_review",
    "merge_safe",
    "merge_after_fix",
    "merge_after_deep",
    "notify_scope",
    "notify_rereview",
    "notify_deep",
)

TOOL_STEPS: dict[str, str] = {
    "github_get_pr": "check_pr",
    "github_list_prs": "check_pr",
    "github_get_check_runs": "check_pr",
    "github_get_pr_files": "read_diff",
    "github_get_file_content": "read_diff",
    "github_get_release": "release_notes",
    "web_search": "release_notes",
}


@dataclass
class _Run:
    run_id: str
    agent: str
    step: str
    detail: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agent": self.agent,
            "step": self.step,
            "detail": self.detail,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


_current: _Run | None = None


def begin(agent: str, detail: str = "", step: str = "start") -> str:
    """Start reporting a run, replacing any previous one."""
    global _current
    _current = _Run(run_id=uuid.uuid4().hex[:12], agent=agent, step=step, detail=detail)
    logger.debug("progress: %s started (%s)", agent, detail)
    return _current.run_id


def _ranks_below(candidate: str, current: str) -> bool:
    """True when `candidate` comes earlier than `current` in the known order.

    Unknown steps rank nowhere and are always applied, so the ordering is a
    refinement rather than a gate.
    """
    if candidate not in STEP_ORDER or current not in STEP_ORDER:
        return False
    return STEP_ORDER.index(candidate) < STEP_ORDER.index(current)


def step(name: str, detail: str | None = None) -> None:
    """Move the current run to a step. A no-op when nothing is running.

    Deliberately silent rather than raising: every call site is inside a worker
    doing real work, and a progress report must never be the thing that fails a
    PR review.
    """
    if _current is None:
        return

    # A new subject -- the next PR in the cycle -- starts over. Within one
    # subject the indicator only moves forward.
    same_subject = detail is None or detail == _current.detail
    if same_subject and _ranks_below(name, _current.step):
        _current.updated_at = datetime.now(UTC)
        return

    _current.step = name
    if detail is not None:
        _current.detail = detail
    _current.updated_at = datetime.now(UTC)


def note_tool(tool_name: str) -> None:
    """Report a step for a tool call, when that tool maps to one."""
    mapped = TOOL_STEPS.get(tool_name)
    if mapped:
        step(mapped)


def finish() -> None:
    """Stop reporting. The diagram goes back to showing the shape of the flow."""
    global _current
    _current = None


def snapshot() -> dict[str, Any] | None:
    """The current run, or None. Expires a run that stopped reporting."""
    if _current is None:
        return None
    if datetime.now(UTC) - _current.updated_at > STALE_AFTER:
        logger.info("progress: dropping a run that stopped reporting at %s", _current.updated_at)
        finish()
        return None
    return _current.as_dict()
