"""Notification policy — decides *whether* to notify, not just how.

Notifications were emitted at every step of a PR's life, from eight scattered
call sites, with no shared idea of what deserves a push. One routine Renovate
patch produced two: "PR #856 reviewed - safe to merge", then sixteen minutes
later "Auto-merged PR #856". A PR that needed a code fix produced three. And a
*failed* merge produced none — it only logged a warning, so the one outcome
worth interrupting someone for was the silent one.

The fix is to classify each notification by how much it needs a human, and let
one setting decide where the line falls:

- ``ROUTINE``   — a step finished as expected. "Reviewed, looks safe", "fix
                  pushed, waiting for CI". Real information, but a later
                  notification will report the outcome, so on the default level
                  these are dropped as redundant.
- ``OUTCOME``   — a terminal state for work the agent did on its own. "Merged".
                  Worth one push; nothing further is coming.
- ``ATTENTION`` — needs a human to look. A PR flagged NEEDS_REVIEW, an alert
                  that could not be auto-fixed.
- ``FAILURE``   — something went wrong. Always sent.

``notify_level`` picks the threshold. The default drops ROUTINE, which halves
the routine case without silencing anything terminal.
"""

import logging
from typing import Any

from sqlalchemy import select

from home_ops_agent.database import Setting, async_session

logger = logging.getLogger(__name__)

ROUTINE = "routine"
OUTCOME = "outcome"
ATTENTION = "attention"
FAILURE = "failure"

# What each level lets through.
LEVELS: dict[str, set[str]] = {
    # Every step, as it was before this module existed.
    "all": {ROUTINE, OUTCOME, ATTENTION, FAILURE},
    # Terminal states and anything needing a human. The default.
    "outcomes": {OUTCOME, ATTENTION, FAILURE},
    # Only what you have to act on — routine merges pass silently.
    "actionable": {ATTENTION, FAILURE},
}

DEFAULT_LEVEL = "outcomes"

NOTIFY_LEVEL_KEY = "notify_level"


async def get_level() -> str:
    """Read the configured notification level, falling back to the default."""
    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == NOTIFY_LEVEL_KEY))
        setting = result.scalar_one_or_none()
    if setting and setting.value in LEVELS:
        return setting.value
    return DEFAULT_LEVEL


def should_send(kind: str, level: str) -> bool:
    """Does ``level`` allow a notification of this kind through?"""
    return kind in LEVELS.get(level, LEVELS[DEFAULT_LEVEL])


async def notify(kind: str, params: dict[str, Any]) -> bool:
    """Send a notification if the configured level allows this kind.

    Returns whether it was sent. Never raises: a notification failing must not
    abort the work that produced it — that is why every previous call site was
    individually wrapped in try/except.
    """
    level = await get_level()
    if not should_send(kind, level):
        logger.debug("Suppressed %s notification at level %s: %s", kind, level, params.get("title"))
        return False

    from home_ops_agent.agent.tools.ntfy import publish_notification

    try:
        await publish_notification(params)
        return True
    except Exception:
        logger.exception("Failed to send notification: %s", params.get("title"))
        return False
