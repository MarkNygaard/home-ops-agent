"""Tests for workers/alert_subscriber.py — alert pipeline logic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

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
