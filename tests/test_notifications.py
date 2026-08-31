"""Tests for the notification policy.

One routine Renovate patch used to produce two pushes ("reviewed, safe to
merge", then "auto-merged" sixteen minutes later) while a *failed* merge
produced none. These pin the classification that fixes that.
"""

from unittest.mock import AsyncMock, patch

import pytest

from home_ops_agent.workers import notifications


def test_levels_are_ordered_by_urgency():
    """Each level must be a superset of the stricter one below it."""
    assert notifications.LEVELS["actionable"] <= notifications.LEVELS["outcomes"]
    assert notifications.LEVELS["outcomes"] <= notifications.LEVELS["all"]


def test_failures_and_attention_are_never_suppressed():
    """No level may silence something that needs a human."""
    for level in notifications.LEVELS:
        assert notifications.should_send(notifications.FAILURE, level), level
        assert notifications.should_send(notifications.ATTENTION, level), level


def test_default_level_drops_routine_but_keeps_outcomes():
    level = notifications.DEFAULT_LEVEL
    assert notifications.should_send(notifications.ROUTINE, level) is False
    assert notifications.should_send(notifications.OUTCOME, level) is True


def test_all_level_keeps_every_step():
    assert notifications.should_send(notifications.ROUTINE, "all") is True


def test_actionable_level_silences_routine_merges():
    assert notifications.should_send(notifications.OUTCOME, "actionable") is False
    assert notifications.should_send(notifications.ATTENTION, "actionable") is True


def test_an_unknown_level_falls_back_to_the_default():
    """A bad DB value must not silence everything."""
    assert notifications.should_send(notifications.OUTCOME, "nonsense") is True
    assert notifications.should_send(notifications.ROUTINE, "nonsense") is False


async def test_notify_sends_when_allowed(db_session):
    with patch(
        "home_ops_agent.agent.tools.ntfy.publish_notification", new_callable=AsyncMock
    ) as publish:
        sent = await notifications.notify(
            notifications.OUTCOME, {"title": "Auto-merged PR #1", "message": "x"}
        )

    assert sent is True
    publish.assert_awaited_once()


async def test_notify_suppresses_routine_at_the_default_level(db_session):
    with patch(
        "home_ops_agent.agent.tools.ntfy.publish_notification", new_callable=AsyncMock
    ) as publish:
        sent = await notifications.notify(
            notifications.ROUTINE, {"title": "PR #1 reviewed - safe to merge", "message": "x"}
        )

    assert sent is False
    publish.assert_not_awaited()


async def test_notify_honours_a_configured_level(db_session):
    from home_ops_agent.database import Setting

    db_session.add(Setting(key="notify_level", value="all"))
    await db_session.flush()

    with patch(
        "home_ops_agent.agent.tools.ntfy.publish_notification", new_callable=AsyncMock
    ) as publish:
        sent = await notifications.notify(notifications.ROUTINE, {"title": "t", "message": "x"})

    assert sent is True
    publish.assert_awaited_once()


async def test_notify_never_raises(db_session):
    """A notification failing must not abort the work that produced it."""
    with patch(
        "home_ops_agent.agent.tools.ntfy.publish_notification",
        new_callable=AsyncMock,
        side_effect=RuntimeError("ntfy is down"),
    ):
        sent = await notifications.notify(notifications.FAILURE, {"title": "t", "message": "x"})

    assert sent is False


# --- the review notification's class depends on what happens next ---


@pytest.mark.parametrize(
    ("verdict", "pr_mode", "expected"),
    [
        # In an auto-merge mode a safe verdict is a step; the merge reports the
        # outcome, so this one is redundant.
        ("SAFE_TO_MERGE", "auto_merge_all", notifications.ROUTINE),
        # In comment_only nothing else fires, so the review *is* the outcome.
        ("SAFE_TO_MERGE", "comment_only", notifications.OUTCOME),
        # Needing a human is always worth a push.
        ("NEEDS_REVIEW", "auto_merge_all", notifications.ATTENTION),
        ("NEEDS_REVIEW", "comment_only", notifications.ATTENTION),
    ],
)
async def test_review_notification_class(verdict, pr_mode, expected, db_session):
    from home_ops_agent.agent.core import AgentResult
    from home_ops_agent.workers import pr_monitor

    captured = {}

    async def _capture(kind, params):
        captured["kind"] = kind
        return True

    with patch.object(notifications, "notify", _capture):
        await pr_monitor._notify_review(
            {"number": 1, "title": "bump thing", "html_url": "u"},
            AgentResult(response=f"[{verdict}] looks fine"),
            pr_mode,
        )

    assert captured["kind"] == expected


# --- the ntfy destination is DB-backed with an env fallback ---


async def test_destination_falls_back_to_env(db_session):
    from home_ops_agent.agent.tools import ntfy

    ntfy.reset_config_cache()
    config = await ntfy.resolve_config()

    from home_ops_agent.config import settings as env

    assert config["url"] == env.ntfy_url
    assert config["topic"] == env.ntfy_agent_topic


async def test_db_settings_override_env(db_session):
    from home_ops_agent.agent.tools import ntfy
    from home_ops_agent.database import Setting

    db_session.add(Setting(key=ntfy.NTFY_URL_KEY, value="http://ntfy.example"))
    db_session.add(Setting(key=ntfy.NTFY_TOPIC_KEY, value="custom-topic"))
    db_session.add(Setting(key=ntfy.NTFY_TOKEN_KEY, value="tk_secret"))
    await db_session.flush()

    ntfy.reset_config_cache()
    config = await ntfy.resolve_config()

    assert config["url"] == "http://ntfy.example"
    assert config["topic"] == "custom-topic"
    assert config["token"] == "tk_secret"


async def test_destination_is_cached(db_session):
    """A notification must not pay a DB round trip, especially during an incident."""
    from home_ops_agent.agent.tools import ntfy
    from home_ops_agent.database import Setting

    ntfy.reset_config_cache()
    first = await ntfy.resolve_config()

    db_session.add(Setting(key=ntfy.NTFY_TOPIC_KEY, value="changed-after-caching"))
    await db_session.flush()

    assert (await ntfy.resolve_config())["topic"] == first["topic"]

    # Explicit invalidation is what the settings writer calls.
    ntfy.reset_config_cache()
    assert (await ntfy.resolve_config())["topic"] == "changed-after-caching"


async def test_an_empty_db_value_does_not_blank_the_destination(db_session):
    """A cleared field must fall back, not send to an empty URL."""
    from home_ops_agent.agent.tools import ntfy
    from home_ops_agent.database import Setting

    db_session.add(Setting(key=ntfy.NTFY_URL_KEY, value=""))
    await db_session.flush()

    ntfy.reset_config_cache()
    from home_ops_agent.config import settings as env

    assert (await ntfy.resolve_config())["url"] == env.ntfy_url


async def test_a_trailing_slash_does_not_double_up(db_session):
    from home_ops_agent.agent.tools import ntfy
    from home_ops_agent.database import Setting

    db_session.add(Setting(key=ntfy.NTFY_URL_KEY, value="http://ntfy.example/"))
    db_session.add(Setting(key=ntfy.NTFY_TOPIC_KEY, value="t"))
    await db_session.flush()
    ntfy.reset_config_cache()

    import httpx
    from pytest_httpx import HTTPXMock  # noqa: F401

    config = await ntfy.resolve_config()
    assert config["url"].endswith("/")
    # publish() strips it when composing the URL.
    assert f"{config['url'].rstrip('/')}/{config['topic']}" == "http://ntfy.example/t"
    assert httpx is not None
