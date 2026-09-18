"""Keeps the background workers alive, and says when one dies.

Three workers run for the life of the process: the PR monitor, the alert
subscriber and the health monitor. They were started with
``asyncio.create_task`` and never looked at again. An unhandled exception in
any of them ends that task silently — the event loop logs "Task exception was
never retrieved" at most, the HTTP server keeps answering, the pod stays Ready,
and Gatus, which only checks HTTP, sees nothing wrong.

The failure mode that produces is the worst kind this system has: the agent
stops reviewing PRs, or stops receiving alerts, and everything that reports on
it says it is fine. You would find out by noticing an absence.

So: each worker is wrapped, restarted with backoff when it dies, and its state
is reported in ``/api/status``. A clean return counts as a death too — these
loops are not supposed to finish, and a worker that quietly ran out of loop is
exactly as broken as one that raised.

The first failure notifies. Subsequent restarts of the same worker do not, so a
worker failing every 30 seconds cannot become a notification flood — the count
is on the status page, and the page is where you look once you have been told
once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Backoff between restarts. A worker that fails instantly and forever should not
# spin the CPU, and one that fails because Postgres was briefly away should come
# back quickly.
FIRST_DELAY_SECONDS = 5
MAX_DELAY_SECONDS = 300


@dataclass
class WorkerState:
    name: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    restarts: int = 0
    last_error: str | None = None
    last_death_at: datetime | None = None
    alive: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "alive": self.alive,
            "restarts": self.restarts,
            "started_at": self.started_at.isoformat(),
            "last_error": self.last_error,
            "last_death_at": self.last_death_at.isoformat() if self.last_death_at else None,
        }


_states: dict[str, WorkerState] = {}


def snapshot() -> list[dict[str, Any]]:
    """Worker health for the status endpoint."""
    return [s.as_dict() for s in _states.values()]


def reset() -> None:
    """For tests."""
    _states.clear()


async def _notify_death(name: str, error: str) -> None:
    """Tell someone, once per worker. Never raises — a failed notification must
    not take down the supervisor that exists to survive failures."""
    try:
        from home_ops_agent.workers import notifications

        await notifications.notify(
            notifications.FAILURE,
            {
                "title": f"Worker stopped: {name}",
                "message": (
                    f"{error}\n\nIt is being restarted. Nothing else would have told you: "
                    "the HTTP server is still answering, so the pod stays Ready."
                )[:300],
                "priority": "high",
                "tags": "rotating_light",
            },
        )
    except Exception:
        logger.exception("Could not send the worker-death notification for %s", name)


async def supervise(name: str, factory: Callable[[], Awaitable[None]]) -> None:
    """Run ``factory()`` forever, restarting it when it stops.

    ``factory`` is a callable rather than a coroutine because a coroutine can
    only be awaited once — restarting needs a fresh one each time.
    """
    state = WorkerState(name=name)
    _states[name] = state
    delay = FIRST_DELAY_SECONDS

    while True:
        try:
            await factory()
            # Reached only if the worker's own loop ended. Not an error in
            # Python's eyes, and still a failure in ours.
            reason = "the worker returned; its loop is not supposed to end"
            logger.error("Worker %s stopped: %s", name, reason)
        except asyncio.CancelledError:
            # Shutdown. The only exit that is not a fault.
            logger.info("Worker %s cancelled", name)
            state.alive = False
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.exception("Worker %s crashed", name)

        first_death = state.restarts == 0
        state.alive = False
        state.restarts += 1
        state.last_error = reason[:500]
        state.last_death_at = datetime.now(UTC)

        if first_death:
            await _notify_death(name, reason)

        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_DELAY_SECONDS)
        state.alive = True
        state.started_at = datetime.now(UTC)
        logger.info("Restarting worker %s (restart #%d)", name, state.restarts)
