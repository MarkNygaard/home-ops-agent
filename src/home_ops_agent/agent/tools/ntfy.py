"""ntfy notification tools for the agent."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx

from home_ops_agent.agent.core import ToolDefinition
from home_ops_agent.config import settings

if TYPE_CHECKING:
    from home_ops_agent.agent.skills import SkillDefinition

logger = logging.getLogger(__name__)


async def publish(params: dict) -> str:
    """Publish a notification to an ntfy topic."""
    topic = params.get("topic", settings.ntfy_agent_topic)
    title = params.get("title", "Home-Ops Agent")
    message = params["message"]
    priority = params.get("priority", 3)
    tags = params.get("tags", [])
    click_url = params.get("click_url")

    url = f"{settings.ntfy_url}/{topic}"

    headers = {
        "Title": title,
        "Priority": str(priority),
    }
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"
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
                            f"ntfy topic (default: '{settings.ntfy_agent_topic}')."
                            " Use 'home-ops-agent' for agent reports."
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
