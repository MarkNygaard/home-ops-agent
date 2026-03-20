"""ntfy SSE subscriber — listens for alerts and triggers agent investigation."""

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from home_ops_agent.agent.core import Agent
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.agent.tools.kubernetes import get_kubernetes_tools
from home_ops_agent.agent.tools.ntfy import get_ntfy_tools
from home_ops_agent.auth.oauth import get_claude_credentials
from home_ops_agent.config import settings
from home_ops_agent.database import AgentTask, Conversation, Message, Setting, async_session

logger = logging.getLogger(__name__)

# Alert cooldown tracking: alert_key -> last_investigated_time
_cooldowns: dict[str, datetime] = {}


async def _get_cooldown_seconds() -> int:
    """Get alert cooldown from DB settings, falling back to env config."""
    async with async_session() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "alert_cooldown_seconds")
        )
        setting = result.scalar_one_or_none()
        if setting:
            return int(setting.value)
    return settings.alert_cooldown_seconds


async def _is_on_cooldown(alert_key: str) -> bool:
    """Check if an alert is still in cooldown period."""
    last_time = _cooldowns.get(alert_key)
    if last_time is None:
        return False
    elapsed = (datetime.now(UTC) - last_time).total_seconds()
    cooldown = await _get_cooldown_seconds()
    return elapsed < cooldown


async def _is_enabled() -> bool:
    """Check if the agent is enabled via settings."""
    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == "agent_enabled"))
        setting = result.scalar_one_or_none()
        if setting is None:
            return True
        return setting.value.lower() in ("true", "1", "yes")


async def _investigate_alert(alert: dict, mcp_tools: list | None = None):
    """Run the agent to investigate an alert."""
    if not await _is_enabled():
        logger.debug("Agent is disabled, skipping alert investigation")
        return

    alert_key = f"{alert.get('topic', '')}:{alert.get('title', '')}:{alert.get('message', '')[:50]}"

    if await _is_on_cooldown(alert_key):
        logger.debug("Alert on cooldown, skipping: %s", alert_key)
        return

    _cooldowns[alert_key] = datetime.now(UTC)

    api_key, oauth_token = await get_claude_credentials()
    if not api_key and not oauth_token:
        logger.warning("No Claude credentials, forwarding raw alert")
        return

    agent = Agent(api_key=api_key, oauth_token=oauth_token)
    agent.register_tools(get_kubernetes_tools())
    agent.register_tools(get_ntfy_tools())

    # Register MCP tools if available (Grafana, Flux)
    if mcp_tools:
        agent.register_tools(mcp_tools)

    messages = [
        {
            "role": "user",
            "content": (
                f"An alert has fired. Investigate and take appropriate action.\n\n"
                f"**Topic:** {alert.get('topic', 'unknown')}\n"
                f"**Title:** {alert.get('title', 'No title')}\n"
                f"**Message:** {alert.get('message', 'No message')}\n"
                f"**Priority:** {alert.get('priority', 3)}\n"
                f"**Tags:** {', '.join(alert.get('tags', []))}\n"
                f"**Time:** {alert.get('time', 'unknown')}\n\n"
                "Investigate this alert using the available tools. Check relevant pods, "
                "logs, metrics, and Flux status. If you can fix the issue, do so. "
                "Then send an ntfy notification to the 'home-ops-agent' topic reporting "
                "your findings and any actions taken."
            ),
        }
    ]

    try:
        # Use alert_fix model since the agent may take corrective actions
        model = await get_model_for_task("alert_fix")
        prompt = await get_prompt("alert_response")
        result = await agent.run(
            system_prompt=prompt,
            messages=messages,
            model=model,
            max_turns=15,
        )

        # Save to database
        async with async_session() as session:
            conversation = Conversation(
                title=f"Alert: {alert.get('title', 'Unknown')}",
                source="alert",
                status="completed",
            )
            session.add(conversation)
            await session.flush()

            msg = Message(
                conversation_id=conversation.id,
                role="assistant",
                content={"text": result.response, "tool_calls": result.tool_calls},
            )
            session.add(msg)

            task = AgentTask(
                task_type="alert_response",
                trigger=f"{alert.get('topic', '')}:{alert.get('title', '')}",
                status="completed",
                conversation_id=conversation.id,
                summary=result.response[:500],
                actions_taken={"tool_calls": result.tool_calls, "tokens": result.total_tokens},
                completed_at=datetime.now(UTC),
            )
            session.add(task)
            await session.commit()

        logger.info("Alert investigated: %s — %s", alert.get("title"), result.response[:100])
    except Exception:
        logger.exception("Failed to investigate alert: %s", alert.get("title"))


async def _subscribe_topic(topic: str, mcp_tools: list | None = None):
    """Subscribe to a single ntfy topic via JSON stream."""
    url = f"{settings.ntfy_url}/{topic}/json"
    logger.info("Subscribing to ntfy topic: %s", url)

    headers = {}
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"

    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            alert = json.loads(line)
                            if alert.get("event") == "message":
                                logger.info(
                                    "Received alert on %s: %s",
                                    topic,
                                    alert.get("title", alert.get("message", "")[:50]),
                                )
                                await _investigate_alert(alert, mcp_tools)
                        except json.JSONDecodeError:
                            logger.warning("Invalid JSON from ntfy: %s", line[:100])
        except httpx.HTTPError as e:
            logger.warning("ntfy subscription error on %s: %s, reconnecting...", topic, e)
        except Exception:
            logger.exception("ntfy subscriber crashed on %s, reconnecting...", topic)

        await asyncio.sleep(5)  # Brief pause before reconnect


async def run_alert_subscriber(mcp_tools: list | None = None):
    """Background task: subscribe to ntfy alert topics."""
    topics = [settings.ntfy_alertmanager_topic, settings.ntfy_gatus_topic]
    logger.info("Alert subscriber started for topics: %s", topics)

    tasks = [asyncio.create_task(_subscribe_topic(topic, mcp_tools)) for topic in topics]
    await asyncio.gather(*tasks)
