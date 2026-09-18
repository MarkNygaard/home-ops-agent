"""ntfy SSE subscriber — listens for alerts and triggers agent investigation."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from home_ops_agent.agent.core import Agent
from home_ops_agent.agent.costs import record_usage
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.agent.skills import registry
from home_ops_agent.auth.credentials import build_credentials
from home_ops_agent.config import settings
from home_ops_agent.database import AgentTask, Conversation, Message, Setting, async_session
from home_ops_agent.workers import notifications, progress

logger = logging.getLogger(__name__)

# Alert cooldown tracking: alert_key -> last_investigated_time.
#
# A fast path only. It is module state, so it dies with the pod -- which matters
# now that a cold start replays recent messages: without a durable check, a
# crash loop would investigate the same alerts on every restart, at a model call
# each. `_is_on_cooldown` falls back to what the database already recorded.
_cooldowns: dict[str, datetime] = {}

# How far back a cold start asks ntfy to replay. ntfy keeps messages for 48h, so
# the limit here is judgement rather than capability: enough to cover a deploy,
# during which nothing is subscribed and an alert would otherwise be lost
# outright, and not so much that a pod which has been down all night wakes up
# and investigates the entire backlog.
COLD_START_REPLAY = "15m"

# Alerts wait here while one is being investigated. Bounded: an unbounded queue
# turns a flood into memory growth, and dropping with a warning is both visible
# and harmless, since a repeat of a dropped alert is dropped by the cooldown
# anyway.
ALERT_QUEUE_SIZE = 100


# Notifications are built in code, not improvised by a model. The PR agent has
# had `ntfy_publish` withheld for exactly this reason -- "every notification the
# model sent was unprompted", which is why four consecutive merges arrived under
# four different title formats. The alert path never got the same treatment, and
# three of the last eight triages sent two notifications each, on top of the one
# this module sends afterwards.
WITHHELD_FROM_ALERT_AGENT = frozenset({"ntfy_publish"})

# Triage decides; it does not act. Everything that changes cluster state is
# withheld from it, so the two-stage design is enforced rather than requested.
# The fix stage keeps all of these.
WITHHELD_FROM_TRIAGE = WITHHELD_FROM_ALERT_AGENT | {
    "k8s_delete_pod",
    "k8s_restart_workload",
    "flux_reconcile",
    "flux_suspend",
    "flux_resume",
    "github_merge_pr",
    "github_create_commit",
    "github_create_pr",
    "github_create_branch",
    "github_create_pr_comment",
    "code_fix",
}


async def _get_cooldown_seconds() -> int:
    """Get alert cooldown from DB settings, falling back to env config.

    It fell back for a missing row but not for an unreachable database, which
    did not matter while the caller short-circuited before reaching here. It is
    on the hot path of every cooldown check now, so an outage would otherwise
    raise on the way to deciding whether to investigate an alert.
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Setting).where(Setting.key == "alert_cooldown_seconds")
            )
            setting = result.scalar_one_or_none()
            if setting:
                return int(setting.value)
    except Exception:
        logger.warning("Could not read the alert cooldown setting; using the default")
    return settings.alert_cooldown_seconds


async def _is_on_cooldown(alert_key: str) -> bool:
    """Check if an alert is still in cooldown period.

    Memory first, then the database. The in-memory record is lost on restart,
    and a cold start now replays the last few minutes from ntfy -- so without
    the second check a restart loop would re-investigate everything it had just
    finished, at a model call each.

    The database is not queried through a JSON path: the row count inside a
    cooldown window is tiny, and comparing in Python keeps this working on
    SQLite as well as Postgres.
    """
    cooldown = await _get_cooldown_seconds()
    now = datetime.now(UTC)

    last_time = _cooldowns.get(alert_key)
    if last_time is not None and (now - last_time).total_seconds() < cooldown:
        return True

    cutoff = now - timedelta(seconds=cooldown)
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentTask).where(
                    AgentTask.task_type == "alert_triage",
                    AgentTask.completed_at.is_not(None),
                    AgentTask.completed_at >= cutoff,
                )
            )
            tasks = result.scalars().all()
    except Exception:
        # Fails open, and the direction matters. If the database is unreachable
        # the worst case here is investigating an alert twice; treating the
        # failure as "on cooldown" would drop it, and an alert nobody hears
        # about is the outcome this whole path exists to avoid.
        logger.warning("Could not check the alert cooldown in the database", exc_info=True)
        return False

    for task in tasks:
        if (task.actions_taken or {}).get("identity") == alert_key:
            # Warm the fast path so a replay burst costs one query, not one per
            # message.
            _cooldowns[alert_key] = task.completed_at
            return True
    return False


async def _is_enabled() -> bool:
    """Check if the agent is enabled via settings."""
    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == "agent_enabled"))
        setting = result.scalar_one_or_none()
        if setting is None:
            return True
        return setting.value.lower() in ("true", "1", "yes")


# Alertmanager routes both the firing and the clearing notification through
# ntfy, distinguished only by this marker in the title.
_RESOLVED_MARKERS = ("[resolved]", "[ok]")
_FIRING_MARKERS = ("[firing]", "[alert]")


def is_resolved(alert: dict) -> bool:
    """Is this the notification saying an alert has *cleared*?

    A cleared alert has nothing to diagnose: by definition the condition is
    gone. Triaging one anyway costs a full agent run to reach the conclusion
    the title already states -- one such run spent 18k tokens and four tool
    calls before answering "the alert title shows [RESOLVED]".
    """
    title = (alert.get("title") or "").lower()
    return any(marker in title for marker in _RESOLVED_MARKERS)


def alert_identity(alert: dict) -> str:
    """Cooldown key for an alert, independent of firing/resolved state.

    The marker is stripped so a fire/clear pair collapses onto one key. With it
    left in, the two notifications were different keys, so the clearing one
    never saw the cooldown its own firing had set -- and every alert cost two
    full triage runs a few minutes apart.
    """
    title = (alert.get("title") or "").lower()
    for marker in _RESOLVED_MARKERS + _FIRING_MARKERS:
        title = title.replace(marker, "")
    return f"{alert.get('topic', '')}:{title.strip()}:{(alert.get('message') or '')[:50]}"


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
    prompt = await get_prompt("alert_triage")
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
            # Prefixed so the outcome survives truncation. The action was
            # already stored in actions_taken, but the History list shows the
            # summary -- and the ACTION line is the last thing the model writes,
            # so it was always cut off. 36 of the last 40 alerts were ignored
            # and nothing on screen said so.
            summary=f"[{action.upper()}] " + response[:500],
            actions_taken={
                "tool_calls": result.tool_calls,
                "tokens": result.total_tokens,
                "action": action,
                # What _is_on_cooldown matches on after a restart. The task's
                # `trigger` is topic:title, which is deliberately *not* the
                # identity -- that strips the FIRING/RESOLVED marker so a
                # fire/clear pair collapses onto one key.
                "identity": alert_identity(alert),
            },
            completed_at=datetime.now(UTC),
        )
        session.add(task)
        await session.commit()

    await record_usage(
        model=result.model,
        task_type="alert_triage",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )

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
                "After taking action, verify it worked — re-check the thing you "
                "changed rather than assuming.\n\n"
                "Do not try to send a notification. One is sent for you from your "
                "reply, so that every alert is announced in the same format. Say "
                "what was wrong, what you did, and whether it is now healthy."
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

    await record_usage(
        model=result.model,
        task_type="alert_fix",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )

    # Built here rather than left to the model. The fix agent used to be told to
    # send this itself, which is how the PR path ended up with four formats for
    # the same event -- and now that ntfy_publish is withheld, nothing would
    # announce a completed fix at all.
    # A PR opened here would otherwise wait for the next scheduled check --
    # up to a full interval, and the interval is an hour. Reviewing it now costs
    # one cycle and makes the alert's proposed fix land in front of the operator
    # while they are still reading the notification about it.
    #
    # Detected from the tool calls rather than from the model's prose: whether a
    # PR exists is a fact, and asking the model to also tell us is a second
    # source of truth that can disagree with the first.
    opened_pr = any(c.get("tool") == "github_create_pr" for c in result.tool_calls)
    if opened_pr:
        await _review_the_new_pr(alert)

    progress.step("notify_fixed")
    try:
        await notifications.notify(
            notifications.OUTCOME,
            {
                "title": (
                    f"Alert: PR opened for {alert.get('title', 'Unknown')}"
                    if opened_pr
                    else f"Alert fixed: {alert.get('title', 'Unknown')}"
                ),
                "message": result.response[:300],
                # A PR needs a person; a completed restart does not.
                "priority": "high" if opened_pr else "default",
                "tags": "memo" if opened_pr else "wrench",
            },
        )
    except Exception:
        logger.exception("Failed to send the alert fix notification")

    logger.info("Alert fix completed: %s", alert.get("title"))


async def _review_the_new_pr(alert: dict) -> None:
    """Ask the PR monitor to run now, so an alert's proposed fix is reviewed.

    Imported inside the function on purpose: the trigger and its in-flight guard
    live in the status API, which already imports from this package, so a module
    level import would close a cycle.

    Never fatal. The PR exists either way, and the scheduled check will reach it;
    this only shortens the wait.
    """
    try:
        from home_ops_agent.api.status import trigger_pr_check

        result = await trigger_pr_check()
        logger.info(
            "Alert %s opened a PR; PR check %s",
            alert.get("title"),
            result.get("status", "triggered"),
        )
    except Exception:
        logger.exception("Could not trigger a PR check after the alert opened one")


async def _investigate_alert(alert: dict, mcp_tools: list | None = None):
    """Two-stage alert pipeline: Triage (Haiku) → Fix (Sonnet) if needed."""
    if not await _is_enabled():
        logger.debug("Agent is disabled, skipping alert investigation")
        return

    # Cheap checks first: neither costs a model call.
    if is_resolved(alert):
        logger.info("Alert already resolved, not triaging: %s", alert.get("title"))
        return

    alert_key = alert_identity(alert)

    if await _is_on_cooldown(alert_key):
        logger.debug("Alert on cooldown, skipping: %s", alert_key)
        return

    _cooldowns[alert_key] = datetime.now(UTC)
    progress.begin("alert", alert.get("title", "alert"), step="check_pods")

    credentials = await build_credentials()
    if not credentials.has_any():
        logger.warning("No model credentials, forwarding raw alert")
        return

    skill_tools = await registry.get_all_enabled_tools()
    if mcp_tools:
        skill_tools = [*skill_tools, *mcp_tools]

    # Two agents, not one with two prompts. Triage is the cheap read-only stage
    # and had every write tool registered, on Haiku, under a prompt telling it
    # to fix things -- so "diagnose, then hand on" was advice rather than a
    # boundary.
    triage_agent = Agent(credentials)
    triage_agent.register_tools([t for t in skill_tools if t.name not in WITHHELD_FROM_TRIAGE])

    fix_agent = Agent(credentials)
    fix_agent.register_tools([t for t in skill_tools if t.name not in WITHHELD_FROM_ALERT_AGENT])

    try:
        # Stage 1: Triage (cheap, fast — Haiku)
        logger.info("Triaging alert: %s", alert.get("title"))
        triage_summary, action = await _triage_alert(alert, triage_agent)
        progress.step("triage")
        logger.info("Alert triaged: %s — action: %s", alert.get("title"), action)

        if action == "ignore":
            progress.step("ignore")
            logger.info("Alert ignored (transient/resolved): %s", alert.get("title"))
            return

        if action == "fix":
            # Stage 2: Fix (capable — Sonnet)
            logger.info("Escalating to fix agent: %s", alert.get("title"))
            progress.step("alert_fix")
            await _fix_alert(alert, triage_summary, fix_agent)
        else:
            # Notify only — send the triage summary via ntfy
            progress.step("notify_user")

            try:
                await notifications.notify(
                    notifications.ATTENTION,
                    {
                        "title": f"Alert: {alert.get('title', 'Unknown')}",
                        "message": triage_summary[:300],
                        "priority": "high",
                        "tags": "warning",
                    },
                )
            except Exception:
                logger.exception("Failed to send alert notification")

    except Exception:
        logger.exception("Failed to investigate alert: %s", alert.get("title"))
    finally:
        # Always, so a failed investigation does not leave a circle pulsing.
        progress.finish()


async def _subscribe_topic(topic: str, queue: asyncio.Queue) -> None:
    """Stream one ntfy topic onto the queue, forever.

    This used to investigate each alert inline, which meant the stream was not
    being read for however long a fix took -- minutes, on Sonnet. Putting the
    alert on a queue keeps the connection drained while a single worker does the
    slow part, so investigations stay serialised without the subscription
    stalling behind them.

    Reconnects ask for what was missed. Without ``since`` ntfy sends only what
    arrives after the connection opens, so anything published while the agent
    was away -- including every deploy, which is a restart -- was simply lost.
    ntfy keeps messages for 48h, so this is a matter of asking.
    """
    url = f"{settings.ntfy_url}/{topic}/json"

    headers = {}
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"

    # On a cold start there is no last message to resume from, so a bounded
    # window stands in for one. After the first message it is that message's id,
    # which is exact and cannot over-replay.
    since: str = COLD_START_REPLAY

    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET", url, headers=headers, params={"since": since}
                ) as response:
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            alert = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning("Invalid JSON from ntfy: %s", line[:100])
                            continue

                        # Advance on every event, not only messages: keepalives
                        # carry ids too, and resuming from the newest id we have
                        # seen is what stops a reconnect replaying the gap twice.
                        if alert.get("id"):
                            since = alert["id"]

                        if alert.get("event") != "message":
                            continue

                        logger.info(
                            "Received alert on %s: %s",
                            topic,
                            alert.get("title", alert.get("message", "")[:50]),
                        )
                        try:
                            queue.put_nowait(alert)
                        except asyncio.QueueFull:
                            # Visible rather than silent, and not fatal: a repeat
                            # of a dropped alert is dropped by the cooldown
                            # anyway, and blocking here would undo the point of
                            # the queue.
                            logger.warning(
                                "Alert queue full (%d); dropping: %s",
                                ALERT_QUEUE_SIZE,
                                alert.get("title"),
                            )
        except httpx.HTTPError as e:
            logger.warning("ntfy subscription error on %s: %s, reconnecting...", topic, e)
        except Exception:
            logger.exception("ntfy subscriber crashed on %s, reconnecting...", topic)

        await asyncio.sleep(5)  # Brief pause before reconnect


async def _alert_worker(queue: asyncio.Queue, mcp_tools: list | None = None) -> None:
    """Investigate queued alerts, one at a time, forever.

    One worker on purpose. Investigations restart pods and reconcile Flux
    resources, and two running at once on the same cluster is a race nobody
    asked for -- and would double the model spend on an alert storm.
    """
    while True:
        alert = await queue.get()
        try:
            await _investigate_alert(alert, mcp_tools)
        except Exception:
            # _investigate_alert handles its own failures; this is the last
            # resort that keeps the worker alive for the next alert.
            logger.exception("Alert worker failed on: %s", alert.get("title"))
        finally:
            queue.task_done()


async def run_alert_subscriber(mcp_tools: list | None = None):
    """Background task: subscribe to ntfy alert topics."""
    topics = [settings.ntfy_alertmanager_topic, settings.ntfy_gatus_topic]
    logger.info("Alert subscriber started for topics: %s", topics)

    queue: asyncio.Queue = asyncio.Queue(maxsize=ALERT_QUEUE_SIZE)
    tasks = [asyncio.create_task(_subscribe_topic(topic, queue)) for topic in topics]
    tasks.append(asyncio.create_task(_alert_worker(queue, mcp_tools)))
    await asyncio.gather(*tasks)
