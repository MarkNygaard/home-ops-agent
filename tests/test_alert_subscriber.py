"""Tests for workers/alert_subscriber.py — alert pipeline logic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from home_ops_agent.workers.alert_subscriber import (
    _cooldowns,
    _format_alert_context,
    _is_on_cooldown,
    _parse_triage_action,
)

# --- _format_alert_context() pure function tests ---


def test_format_alert_context_full():
    alert = {
        "topic": "alertmanager",
        "title": "Pod CrashLooping",
        "message": "sonarr-0 has restarted 5 times",
        "priority": 4,
        "tags": ["warning", "k8s"],
        "time": "2026-01-01T00:00:00Z",
    }
    result = _format_alert_context(alert)
    assert "alertmanager" in result
    assert "Pod CrashLooping" in result
    assert "sonarr-0 has restarted 5 times" in result
    assert "4" in result
    assert "warning, k8s" in result


def test_format_alert_context_missing_fields():
    alert = {}
    result = _format_alert_context(alert)
    assert "unknown" in result
    assert "No title" in result
    assert "No message" in result
    assert "3" in result  # default priority


def test_format_alert_context_empty_tags():
    alert = {"tags": []}
    result = _format_alert_context(alert)
    assert "**Tags:** " in result


# --- _is_on_cooldown() tests ---


async def test_is_on_cooldown_no_previous():
    _cooldowns.clear()
    result = await _is_on_cooldown("test:alert:message")
    assert result is False


async def test_is_on_cooldown_within_window():
    _cooldowns.clear()
    _cooldowns["test:alert:msg"] = datetime.now(UTC) - timedelta(seconds=10)
    with patch(
        "home_ops_agent.workers.alert_subscriber._get_cooldown_seconds",
        new_callable=AsyncMock,
        return_value=900,
    ):
        result = await _is_on_cooldown("test:alert:msg")
    assert result is True


async def test_is_on_cooldown_expired():
    _cooldowns.clear()
    _cooldowns["test:alert:msg"] = datetime.now(UTC) - timedelta(seconds=1000)
    with patch(
        "home_ops_agent.workers.alert_subscriber._get_cooldown_seconds",
        new_callable=AsyncMock,
        return_value=900,
    ):
        result = await _is_on_cooldown("test:alert:msg")
    assert result is False


# --- _parse_triage_action() tests ---


def test_parse_triage_action_fix():
    assert _parse_triage_action("the pod is stuck. ACTION: fix") == "fix"


def test_parse_triage_action_ignore():
    assert _parse_triage_action("transient alert, resolved. action: ignore") == "ignore"


def test_parse_triage_action_notify_default():
    assert _parse_triage_action("this needs human attention, cannot auto-fix.") == "notify"


def test_parse_triage_action_case_insensitive():
    assert _parse_triage_action("ACTION: FIX") == "fix"
    assert _parse_triage_action("Action: Ignore") == "ignore"


# --- resolved alerts must not cost a triage run ---


def test_resolved_alerts_are_recognised():
    from home_ops_agent.workers.alert_subscriber import is_resolved

    assert is_resolved({"title": "[RESOLVED] OomKilled OomKilled media"}) is True
    assert is_resolved({"title": "[FIRING] OomKilled OomKilled media"}) is False
    assert is_resolved({"title": "KubePodNotReady"}) is False
    assert is_resolved({}) is False


def test_firing_and_resolved_share_a_cooldown_key():
    """A fire/clear pair must collapse onto one key.

    With the marker left in the key they were two different alerts, so the
    clearing notification never saw the cooldown its own firing had set -- and
    every alert cost two full triage runs minutes apart.
    """
    from home_ops_agent.workers.alert_subscriber import alert_identity

    firing = {
        "topic": "alertmanager",
        "title": "[FIRING] OomKilled OomKilled media",
        "message": "flaresolverr was OOMKilled",
    }
    resolved = {**firing, "title": "[RESOLVED] OomKilled OomKilled media"}

    assert alert_identity(firing) == alert_identity(resolved)


def test_different_alerts_keep_different_keys():
    from home_ops_agent.workers.alert_subscriber import alert_identity

    a = {"topic": "alertmanager", "title": "[FIRING] OomKilled media", "message": "x"}
    b = {"topic": "alertmanager", "title": "[FIRING] KubePodNotReady media", "message": "x"}

    assert alert_identity(a) != alert_identity(b)


async def test_a_resolved_alert_is_never_triaged(monkeypatch):
    """The whole point: no model call, no tool calls, no agent_tasks row."""
    from unittest.mock import AsyncMock

    from home_ops_agent.workers import alert_subscriber

    monkeypatch.setattr(alert_subscriber, "_is_enabled", AsyncMock(return_value=True))
    triage = AsyncMock()
    monkeypatch.setattr(alert_subscriber, "_triage_alert", triage)
    build_credentials = AsyncMock()
    monkeypatch.setattr(alert_subscriber, "build_credentials", build_credentials)

    await alert_subscriber._investigate_alert(
        {
            "topic": "alertmanager",
            "title": "[RESOLVED] OomKilled OomKilled media",
            "message": "recovered",
        }
    )

    triage.assert_not_called()
    # It should not even reach the point of loading credentials.
    build_credentials.assert_not_called()


async def test_a_firing_alert_is_still_triaged(monkeypatch):
    from unittest.mock import AsyncMock

    from home_ops_agent.auth.credentials import Credentials
    from home_ops_agent.workers import alert_subscriber

    alert_subscriber._cooldowns.clear()
    monkeypatch.setattr(alert_subscriber, "_is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        alert_subscriber,
        "build_credentials",
        AsyncMock(return_value=Credentials(kimi_api_key="k")),
    )
    monkeypatch.setattr(
        alert_subscriber.registry, "get_all_enabled_tools", AsyncMock(return_value=[])
    )
    triage = AsyncMock(return_value=("summary", "ignore"))
    monkeypatch.setattr(alert_subscriber, "_triage_alert", triage)

    await alert_subscriber._investigate_alert(
        {
            "topic": "alertmanager",
            "title": "[FIRING] OomKilled OomKilled media",
            "message": "pod died",
        }
    )

    triage.assert_called_once()


# --- the boundary between triage and fix ------------------------------------


def test_triage_cannot_change_anything():
    """Triage is the cheap read-only stage, and that is now enforced.

    It ran on Haiku with every write tool registered, under a prompt telling it
    to "attempt a fix if possible" — so "diagnose, then hand on" was advice
    rather than a boundary. Across 84 alerts, ACTION: fix was chosen zero times,
    which is what a model told to fix things reports when it believes the matter
    is handled.
    """
    from home_ops_agent.workers.alert_subscriber import WITHHELD_FROM_TRIAGE

    for name in (
        "k8s_delete_pod",
        "k8s_restart_workload",
        "flux_reconcile",
        "flux_suspend",
        "flux_resume",
        "code_fix",
    ):
        assert name in WITHHELD_FROM_TRIAGE, name


def test_the_fix_stage_keeps_the_tools_it_needs():
    """Withholding from triage must not disarm the stage that exists to act."""
    from home_ops_agent.workers.alert_subscriber import WITHHELD_FROM_ALERT_AGENT

    for name in ("k8s_delete_pod", "flux_reconcile", "k8s_restart_workload"):
        assert name not in WITHHELD_FROM_ALERT_AGENT, name


def test_neither_stage_sends_its_own_notification():
    """Three of the last eight triages sent two notifications each, on top of
    the one this module sends afterwards.

    The PR agent has had `ntfy_publish` withheld for exactly this reason; the
    alert path never got the same treatment.
    """
    from home_ops_agent.workers.alert_subscriber import (
        WITHHELD_FROM_ALERT_AGENT,
        WITHHELD_FROM_TRIAGE,
    )

    assert "ntfy_publish" in WITHHELD_FROM_ALERT_AGENT
    assert "ntfy_publish" in WITHHELD_FROM_TRIAGE


def test_a_completed_fix_is_still_announced():
    """Withholding the tool removes the model's way of telling you. If the code
    did not send one, a successful fix would happen in silence."""
    import inspect

    from home_ops_agent.workers import alert_subscriber

    source = inspect.getsource(alert_subscriber._fix_alert)
    assert "notifications.notify" in source
    assert "Alert fixed" in source


def test_triage_has_its_own_prompt():
    """It ran on the fix agent's prompt, which opens "attempt a fix if possible"
    and then lists the corrective actions available."""
    import inspect

    from home_ops_agent.agent.prompts import DEFAULTS
    from home_ops_agent.workers import alert_subscriber

    assert "alert_triage" in DEFAULTS
    assert DEFAULTS["alert_triage"] != DEFAULTS["alert_response"]
    assert 'get_prompt("alert_triage")' in inspect.getsource(alert_subscriber._triage_alert)


def test_the_triage_prompt_forbids_acting_and_names_the_three_actions():
    from home_ops_agent.agent.prompts import DEFAULTS

    text = DEFAULTS["alert_triage"]
    assert "do not fix anything here" in text.lower()
    for action in ("ACTION: fix", "ACTION: notify", "ACTION: ignore"):
        assert action in text, action


def test_the_triage_prompt_says_unsure_is_not_ignore():
    """`ignore` sends nothing to anyone — it is the one outcome nobody hears
    about, and it was chosen for 36 of the last 40 alerts."""
    from home_ops_agent.agent.prompts import DEFAULTS

    assert "Being unsure is not `ignore`" in DEFAULTS["alert_triage"]


# --- the subscription itself ------------------------------------------------


@pytest.mark.asyncio
async def test_the_stream_does_not_wait_for_the_investigation():
    """It used to await _investigate_alert inline, so nothing read the stream
    for however long a fix took — minutes, on Sonnet."""
    import inspect

    from home_ops_agent.workers import alert_subscriber

    source = inspect.getsource(alert_subscriber._subscribe_topic)
    assert "_investigate_alert" not in source
    assert "put_nowait" in source


@pytest.mark.asyncio
async def test_a_full_queue_drops_rather_than_blocking():
    """Blocking would undo the point of the queue, and a repeat of a dropped
    alert is dropped by the cooldown anyway."""
    import inspect

    from home_ops_agent.workers import alert_subscriber

    source = inspect.getsource(alert_subscriber._subscribe_topic)
    assert "QueueFull" in source
    assert alert_subscriber.ALERT_QUEUE_SIZE > 0


def test_one_worker_investigates_at_a_time():
    """Two investigations at once on the same cluster is a race nobody asked
    for, and doubles the model spend on an alert storm."""
    import inspect

    from home_ops_agent.workers import alert_subscriber

    source = inspect.getsource(alert_subscriber.run_alert_subscriber)
    assert source.count("_alert_worker") == 1


def test_reconnects_ask_for_what_was_missed():
    """Without `since`, ntfy sends only what arrives after the connection opens
    — so anything published while the agent was away, including during every
    deploy, was lost outright. ntfy keeps messages for 48h."""
    import inspect

    from home_ops_agent.workers import alert_subscriber

    source = inspect.getsource(alert_subscriber._subscribe_topic)
    assert '"since": since' in source
    assert alert_subscriber.COLD_START_REPLAY.endswith("m")


@pytest.mark.asyncio
async def test_the_cooldown_fails_open_when_the_database_is_unreachable(monkeypatch):
    """The direction matters. Investigating twice costs a model call; treating
    the failure as "on cooldown" drops the alert, which is the outcome this
    whole path exists to avoid.
    """
    from home_ops_agent.workers import alert_subscriber

    def _boom(*_args, **_kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(alert_subscriber, "async_session", _boom)
    alert_subscriber._cooldowns.clear()

    assert await alert_subscriber._is_on_cooldown("anything") is False


@pytest.mark.asyncio
async def test_the_cooldown_setting_falls_back_when_the_database_is_unreachable(monkeypatch):
    from home_ops_agent.config import settings
    from home_ops_agent.workers import alert_subscriber

    def _boom(*_args, **_kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(alert_subscriber, "async_session", _boom)
    assert await alert_subscriber._get_cooldown_seconds() == settings.alert_cooldown_seconds


def test_triage_records_the_identity_the_cooldown_matches_on():
    """The task's `trigger` is topic:title, which is deliberately not the
    identity — that strips the FIRING/RESOLVED marker so a fire/clear pair
    collapses onto one key. Matching on trigger would miss."""
    import inspect

    from home_ops_agent.workers import alert_subscriber

    source = inspect.getsource(alert_subscriber._triage_alert)
    assert '"identity": alert_identity(alert)' in source


# --- an alert that needs a manifest change ----------------------------------


def test_the_fix_agent_may_open_a_pr():
    """A restart clears a stuck state. It does nothing about a limit that is too
    low — and restarting there buys minutes while hiding a recurring alert
    behind an apparently successful fix."""
    from home_ops_agent.agent.prompts import DEFAULTS

    text = DEFAULTS["alert_response"]
    assert "Open a pull request" in text
    assert "When a restart is not the answer" in text


def test_the_prompt_does_not_forbid_the_thing_it_now_asks_for():
    """The CANNOT list said "Apply raw manifests", which a model can reasonably
    read as covering a commit — though committing to git is the opposite of
    applying a manifest to the API server."""
    from home_ops_agent.agent.prompts import DEFAULTS

    text = DEFAULTS["alert_response"]
    assert "Apply raw manifests" not in text
    assert "never through the API server" in text


def test_a_pr_is_reviewed_immediately_rather_than_on_the_hour():
    """The scheduled check runs every 3600s, so a PR opened just after one would
    wait most of an hour for its first look."""
    import inspect

    from home_ops_agent.workers import alert_subscriber

    source = inspect.getsource(alert_subscriber._fix_alert)
    assert "_review_the_new_pr" in source
    assert 'c.get("tool") == "github_create_pr"' in source


def test_the_trigger_is_never_fatal():
    """The PR exists either way and the scheduled check will reach it. Failing
    the whole fix because the review could not be hurried would be worse than
    the wait it was avoiding."""
    import inspect

    from home_ops_agent.workers import alert_subscriber

    assert "except Exception" in inspect.getsource(alert_subscriber._review_the_new_pr)


def test_an_agent_opened_pr_still_needs_a_person():
    """Auto-merge requires renovate[bot] as the author, and the agent is not it.

    That is the property that keeps this loop open: an alert can propose a
    change to the cluster, and cannot land one.
    """
    import inspect

    from home_ops_agent.workers import pr_monitor

    source = inspect.getsource(pr_monitor._is_safe_to_auto_merge)
    assert 'pr.get("author") != "renovate[bot]"' in source


def test_the_notification_says_a_pr_is_waiting():
    """A completed restart needs no one; a PR does."""
    import inspect

    from home_ops_agent.workers import alert_subscriber

    source = inspect.getsource(alert_subscriber._fix_alert)
    assert "PR opened for" in source
    assert '"high" if opened_pr else "default"' in source
