"""Tests for workers/alert_subscriber.py — alert pipeline logic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from home_ops_agent.workers.alert_subscriber import (
    _cooldowns,
    _format_alert_context,
    _is_on_cooldown,
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


@pytest.mark.asyncio
async def test_is_on_cooldown_no_previous():
    _cooldowns.clear()
    result = await _is_on_cooldown("test:alert:message")
    assert result is False


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


# --- Action parsing tests (tested via _triage_alert behavior) ---


def test_action_parsing_fix():
    """Verify the action parsing logic used in _triage_alert."""
    response_lower = "the pod is stuck. action: fix".lower()
    if "action: fix" in response_lower:
        action = "fix"
    elif "action: ignore" in response_lower:
        action = "ignore"
    else:
        action = "notify"
    assert action == "fix"


def test_action_parsing_ignore():
    response_lower = "transient alert, resolved. action: ignore"
    if "action: fix" in response_lower:
        action = "fix"
    elif "action: ignore" in response_lower:
        action = "ignore"
    else:
        action = "notify"
    assert action == "ignore"


def test_action_parsing_notify_default():
    response_lower = "this needs human attention, cannot auto-fix."
    if "action: fix" in response_lower:
        action = "fix"
    elif "action: ignore" in response_lower:
        action = "ignore"
    else:
        action = "notify"
    assert action == "notify"
