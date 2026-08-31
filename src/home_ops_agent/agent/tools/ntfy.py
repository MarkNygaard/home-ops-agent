"""ntfy notification tools for the agent."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

import httpx

from home_ops_agent.agent.core import ToolDefinition
from home_ops_agent.config import settings

if TYPE_CHECKING:
    from home_ops_agent.agent.skills import SkillDefinition

logger = logging.getLogger(__name__)


# Setting keys, matching the environment variable names they fall back to.
NTFY_URL_KEY = "ntfy_url"
NTFY_TOPIC_KEY = "ntfy_agent_topic"
NTFY_TOKEN_KEY = "ntfy_token"


# Resolved destination, cached briefly. A notification must not wait on a
# database round trip -- and when the database is unreachable the connect
# attempt times out, which is exactly the moment an alert matters most. The
# settings writer calls reset_config_cache() so a change from the UI applies at
# once rather than after the TTL.
CONFIG_TTL_SECONDS = 30.0
_config_cache: dict[str, str] | None = None
_config_cached_at: float = 0.0


def reset_config_cache() -> None:
    """Forget the cached destination, so the next publish re-reads it."""
    global _config_cache, _config_cached_at
    _config_cache = None
    _config_cached_at = 0.0


async def resolve_config() -> dict[str, str]:
    """Resolve where notifications go: DB settings first, then env.

    Same precedence as every other setting in this app, so the destination can
    be changed from the UI without editing the SOPS secret and rolling the pod.
    """
    global _config_cache, _config_cached_at

    now = time.monotonic()
    if _config_cache is not None and now - _config_cached_at < CONFIG_TTL_SECONDS:
        return _config_cache

    from sqlalchemy import select

    from home_ops_agent.database import Setting, async_session

    keys = (NTFY_URL_KEY, NTFY_TOPIC_KEY, NTFY_TOKEN_KEY)
    db: dict[str, str] = {}
    try:
        async with async_session() as session:
            result = await session.execute(select(Setting).where(Setting.key.in_(keys)))
            db = {row.key: row.value for row in result.scalars().all() if row.value}
    except Exception:
        # Fall back to env rather than dropping the notification.
        logger.warning("Could not read ntfy settings from the DB; using env config")

    resolved = {
        "url": db.get(NTFY_URL_KEY) or settings.ntfy_url,
        "topic": db.get(NTFY_TOPIC_KEY) or settings.ntfy_agent_topic,
        "token": db.get(NTFY_TOKEN_KEY) or settings.ntfy_token,
    }
    _config_cache = resolved
    _config_cached_at = now
    return resolved


async def publish(params: dict) -> str:
    """Publish a notification to an ntfy topic."""
    config = await resolve_config()

    topic = params.get("topic") or config["topic"]
    title = params.get("title", "Home-Ops Agent")
    message = params["message"]
    priority = params.get("priority", 3)
    tags = params.get("tags", [])
    click_url = params.get("click_url")

    url = f"{config['url'].rstrip('/')}/{topic}"

    headers = {
        "Title": title,
        "Priority": str(priority),
    }
    if config["token"]:
        headers["Authorization"] = f"Bearer {config['token']}"
    if tags:
        if isinstance(tags, list):
            headers["Tags"] = ",".join(tags)
        else:
            headers["Tags"] = str(tags)
    if click_url:
        headers["Click"] = click_url

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, content=message)
            resp.raise_for_status()
            return json.dumps({"status": "ok", "topic": topic})
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Failed to publish to ntfy: {e}"})


async def publish_notification(params: dict) -> str:
    """Convenience wrapper for internal use (PR monitor, alert subscriber)."""
    return await publish(params)


def _get_tools(config: dict) -> list[ToolDefinition]:
    """Return ntfy tool definitions."""
    return get_ntfy_tools()


def get_ntfy_tools() -> list[ToolDefinition]:
    """Return ntfy tool definitions."""
    return [
        ToolDefinition(
            name="ntfy_publish",
            description=(
                "Send a notification to the user via ntfy."
                " Use this to report actions taken, alert investigations,"
                " or anything the user should know about."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "ntfy topic. Omit to use the configured default"
                            " (Settings -> Notifications)."
                        ),
                    },
                    "title": {"type": "string", "description": "Notification title"},
                    "message": {"type": "string", "description": "Notification body text"},
                    "priority": {
                        "type": "integer",
                        "description": "Priority: 1=min, 2=low, 3=default, 4=high, 5=urgent",
                        "enum": [1, 2, 3, 4, 5],
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Emoji tags (e.g., ['white_check_mark', 'robot'])",
                    },
                },
                "required": ["message"],
            },
            handler=publish,
        ),
    ]


def _make_skill() -> SkillDefinition:
    from home_ops_agent.agent.skills import SkillDefinition

    return SkillDefinition(
        id="ntfy",
        name="ntfy",
        description=(
            "Send push notifications via ntfy. Used to report actions taken,"
            " alert investigations, and diagnostics."
        ),
        builtin=True,
        get_tools=_get_tools,
    )


SKILL: SkillDefinition = _make_skill()
