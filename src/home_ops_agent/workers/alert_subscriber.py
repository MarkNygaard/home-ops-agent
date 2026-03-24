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
from home_ops_agent.agent.skills import registry
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


def _format_alert_context(alert: dict) -> str:
    """Format alert details as a text block for the agent prompt."""
    return (
        f"**Topic:** {alert.get('topic', 'unknown')}\n"
        f"**Title:** {alert.get('title', 'No title')}\n"
        f"**Message:** {alert.get('message', 'No message')}\n"
        f"**Priority:** {alert.get('priority', 3)}\n"
        f"**Tags:** {', '.join(alert.get('tags', []))}\n"
        f"**Time:** {alert.get('time', 'unknown')}"
    )


def _parse_triage_action(response: str) -> str:
    """Parse the action from a triage response.

    Returns one of: "fix", "ignore", "notify" (default).
    """
    response_lower = response.lower()
    if "action: fix" in response_lower:
        return "fix"
    elif "action: ignore" in response_lower:
        return "ignore"
    return "notify"


async def _triage_alert(alert: dict, agent: Agent) -> tuple[str, str]:
    """Stage 1: Quick triage with Haiku — diagnose severity and determine if fixable.

    Returns (triage_summary, action) where action is one of:
    - "fix" — the issue is fixable, escalate to Alert Fix agent
    - "notify" — not fixable, just notify the user with diagnosis
    - "ignore" — transient/resolved, no action needed
    """
    messages = [
        {
            "role": "user",
            "content": (
                "An alert has fired. Quickly triage it: check the affected component, "
                "read recent logs, and determine the severity.\n\n"
                f"{_format_alert_context(alert)}\n\n"
                "After investigating, respond with your diagnosis and end with exactly "
                "one of these action lines:\n"
                "ACTION: fix — if you found a fixable issue "
                "(stuck pod, failed reconciliation, etc.)\n"
                "ACTION: notify — if the issue needs human attention (not auto-fixable)\n"
                "ACTION: ignore — if the alert is transient or already resolved"
            ),
        }
    ]

    model = await get_model_for_task("alert_triage")
    prompt = await get_prompt("alert_response")
    result = await agent.run(
        system_prompt=prompt,
        messages=messages,
        model=model,
        max_turns=8,
    )

    response = result.response
    action = _parse_triage_action(response)

    # Save triage task
    async with async_session() as session:
        conversation = Conversation(
            title=f"Alert Triage: {alert.get('title', 'Unknown')}",
            source="alert_triage",
            status="completed",
        )
        session.add(conversation)
        await session.flush()

        msg = Message(
            conversation_id=conversation.id,
            role="assistant",
            content={"text": response, "tool_calls": result.tool_calls},
        )
        session.add(msg)

        task = AgentTask(
            task_type="alert_triage",
            trigger=f"{alert.get('topic', '')}:{alert.get('title', '')}",
            status="completed",
            conversation_id=conversation.id,
            summary=response[:500],
            actions_taken={
                "tool_calls": result.tool_calls,
                "tokens": result.total_tokens,
                "action": action,
            },
            completed_at=datetime.now(UTC),
        )
        session.add(task)
        await session.commit()

    return response, action


async def _fix_alert(alert: dict, triage_summary: str, agent: Agent):
    """Stage 2: Fix the issue with Sonnet — take corrective action."""
    messages = [
        {
            "role": "user",
            "content": (
                "An alert was triaged and determined to be fixable. "
                "Take corrective action to resolve the issue.\n\n"
                f"Alert:\n{_format_alert_context(alert)}\n\n"
                f"Triage diagnosis:\n{triage_summary}\n\n"
                "Actions you can take:\n"
                "- Restart a stuck pod (delete it to force recreation)\n"
                "- Trigger Flux reconciliation for a stuck HelmRelease or Kustomization\n"
                "- Resume a suspended Flux resource\n\n"
                "After taking action, verify the fix worked, then send an ntfy notification "
                "to the 'home-ops-agent' topic explaining what was wrong and what you did."
            ),
        }
    ]

    model = await get_model_for_task("alert_fix")
    prompt = await get_prompt("alert_response")
    result = await agent.run(
        system_prompt=prompt,
        messages=messages,
        model=model,
        max_turns=15,
    )

    # Save fix task
    async with async_session() as session:
        conversation = Conversation(
            title=f"Alert Fix: {alert.get('title', 'Unknown')}",
            source="alert_fix",
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
            task_type="alert_fix",
            trigger=f"{alert.get('topic', '')}:{alert.get('title', '')}",
            status="completed",
            conversation_id=conversation.id,
            summary=result.response[:500],
            actions_taken={
                "tool_calls": result.tool_calls,
                "tokens": result.total_tokens,
            },
            completed_at=datetime.now(UTC),
        )
        session.add(task)
        await session.commit()

    logger.info("Alert fix completed: %s", alert.get("title"))


async def _investigate_alert(alert: dict, mcp_tools: list | None = None):
    """Two-stage alert pipeline: Triage (Haiku) → Fix (Sonnet) if needed."""
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
    skill_tools = await registry.get_all_enabled_tools()
    agent.register_tools(skill_tools)
    if mcp_tools:
        agent.register_tools(mcp_tools)

    try:
        # Stage 1: Triage (cheap, fast — Haiku)
        logger.info("Triaging alert: %s", alert.get("title"))
        triage_summary, action = await _triage_alert(alert, agent)
        logger.info("Alert triaged: %s — action: %s", alert.get("title"), action)

        if action == "ignore":
            logger.info("Alert ignored (transient/resolved): %s", alert.get("title"))
            return

        if action == "fix":
            # Stage 2: Fix (capable — Sonnet)
            logger.info("Escalating to fix agent: %s", alert.get("title"))
            await _fix_alert(alert, triage_summary, agent)
        else:
            # Notify only — send the triage summary via ntfy
            from home_ops_agent.agent.tools.ntfy import publish_notification

            try:
                await publish_notification(
                    {
                        "title": f"Alert: {alert.get('title', 'Unknown')}",
                        "message": triage_summary[:300],
                        "priority": "high",
                        "tags": "warning",
                    }
                )
            except Exception:
                logger.exception("Failed to send alert notification")

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
