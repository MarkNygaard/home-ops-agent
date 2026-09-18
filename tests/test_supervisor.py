"""Tests for the worker supervisor.

The failure it exists for is silent: an exception ends the task, the HTTP
server keeps answering, the pod stays Ready, and Gatus — which only checks
HTTP — reports green while the agent has stopped reviewing PRs.
"""

from __future__ import annotations

import asyncio

import pytest

from home_ops_agent.workers import supervisor


@pytest.fixture(autouse=True)
def _clean():
    supervisor.reset()
    yield
    supervisor.reset()


@pytest.mark.asyncio
async def test_a_crashed_worker_is_restarted(monkeypatch):
    monkeypatch.setattr(supervisor, "FIRST_DELAY_SECONDS", 0)
    monkeypatch.setattr(supervisor, "MAX_DELAY_SECONDS", 0)
    monkeypatch.setattr(supervisor, "_notify_death", _silent)

    runs = 0

    async def _flaky():
        nonlocal runs
        runs += 1
        if runs < 3:
            raise RuntimeError("postgres went away")
        await asyncio.sleep(3600)

    task = asyncio.create_task(supervisor.supervise("flaky", _flaky))
    await _settle()
    task.cancel()

    assert runs == 3
    state = supervisor.snapshot()[0]
    assert state["restarts"] == 2
    assert "postgres went away" in state["last_error"]


@pytest.mark.asyncio
async def test_a_worker_that_returns_cleanly_counts_as_dead(monkeypatch):
    """These loops are not supposed to finish. One that quietly ran out of loop
    is exactly as broken as one that raised, and Python calls it a success."""
    monkeypatch.setattr(supervisor, "FIRST_DELAY_SECONDS", 0)
    monkeypatch.setattr(supervisor, "MAX_DELAY_SECONDS", 0)
    monkeypatch.setattr(supervisor, "_notify_death", _silent)

    calls = 0

    async def _returns():
        nonlocal calls
        calls += 1
        if calls > 2:
            await asyncio.sleep(3600)

    task = asyncio.create_task(supervisor.supervise("quitter", _returns))
    await _settle()
    task.cancel()

    assert calls > 1, "a returning worker was never restarted"
    assert "not supposed to end" in supervisor.snapshot()[0]["last_error"]


@pytest.mark.asyncio
async def test_only_the_first_death_notifies(monkeypatch):
    """A worker failing every few seconds must not become a notification
    flood — the count is on the status page, which is where you look after
    being told once."""
    monkeypatch.setattr(supervisor, "FIRST_DELAY_SECONDS", 0)
    monkeypatch.setattr(supervisor, "MAX_DELAY_SECONDS", 0)

    sent = []

    async def _record(name, error):
        sent.append(name)

    monkeypatch.setattr(supervisor, "_notify_death", _record)

    async def _always_fails():
        raise RuntimeError("nope")

    task = asyncio.create_task(supervisor.supervise("doomed", _always_fails))
    await _settle()
    task.cancel()

    assert len(sent) == 1
    assert supervisor.snapshot()[0]["restarts"] > 2


@pytest.mark.asyncio
async def test_cancellation_is_not_treated_as_a_fault(monkeypatch):
    """Shutdown is the one exit that is not a failure. Restarting through it
    would keep the process alive past its own shutdown."""
    monkeypatch.setattr(supervisor, "_notify_death", _silent)

    async def _worker():
        await asyncio.sleep(3600)

    task = asyncio.create_task(supervisor.supervise("normal", _worker))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert supervisor.snapshot()[0]["restarts"] == 0
    assert supervisor.snapshot()[0]["alive"] is False


@pytest.mark.asyncio
async def test_a_failed_notification_does_not_kill_the_supervisor(monkeypatch):
    """The thing that exists to survive failures must survive this one."""
    monkeypatch.setattr(supervisor, "FIRST_DELAY_SECONDS", 0)
    monkeypatch.setattr(supervisor, "MAX_DELAY_SECONDS", 0)

    async def _broken_notify(*_a, **_k):
        raise RuntimeError("ntfy is down")

    from home_ops_agent.workers import notifications

    monkeypatch.setattr(notifications, "notify", _broken_notify)

    calls = 0

    async def _flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first")
        await asyncio.sleep(3600)

    task = asyncio.create_task(supervisor.supervise("resilient", _flaky))
    await _settle()
    task.cancel()

    assert calls == 2


def test_the_status_endpoint_reports_worker_health():
    """Without this the supervisor would restart things and still leave you
    with no way to see that it had."""
    import inspect

    from home_ops_agent.api import status

    assert "supervisor.snapshot()" in inspect.getsource(status.agent_status)


def test_every_background_worker_is_supervised():
    """A worker started with a bare create_task is the exact hole this closes."""
    import inspect

    from home_ops_agent import main

    src = inspect.getsource(main.lifespan)
    for name in ("pr_monitor", "alert_subscriber", "health_monitor"):
        assert f'supervise("{name}"' in src, name
    assert "create_task(run_" not in src


async def _silent(*_args, **_kwargs):
    return None


async def _settle():
    """Let the supervisor's restart loop run to a resting state."""
    for _ in range(50):
        await asyncio.sleep(0)
