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
