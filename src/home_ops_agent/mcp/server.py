"""MCP server — read-only access to what the agent has actually done.

The agent's own record of its work (tasks, the conversations behind them, the
tool calls inside those, memories, cost) is only reachable by clicking through
the web UI. That makes it awkward to reason about from a coding session: to
diagnose why a PR review went wrong you want the tool-call trace, not a
screenshot of a summary.

This exposes that record over MCP-over-HTTP, as a thin layer on the same
queries the REST API already serves. It is mounted at ``/mcp`` and is
**deliberately read-only** — nothing here triggers a run, edits a setting, or
writes a memory. A read tool is safe to hand to any session; a write tool means
an outside session can act on the cluster's operator, which is a separate
decision.

**Disabled unless ``MCP_API_TOKEN`` is set.** With no token the endpoint is not
mounted at all, so it cannot be reached by accident. When set, every request
must carry ``Authorization: Bearer <token>``.
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import desc, func, select

from home_ops_agent.config import settings
from home_ops_agent.database import (
    AgentTask,
    ApiUsage,
    Conversation,
    Memory,
    Message,
    Setting,
    async_session,
)

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"

# The mounted server, once mount() has run. Held so the app lifespan can start
# its session manager: Starlette does not propagate lifespan to mounted
# sub-applications, and without that startup every request fails with
# "Task group is not initialized".
_server: Any = None

# Tool results are read by a model, so they are capped: a single alert
# investigation can carry very large tool outputs, and an unbounded dump would
# swamp the caller's context rather than inform it.
MAX_TOOL_RESULT_CHARS = 2000
MAX_MESSAGE_TEXT_CHARS = 4000


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [truncated, {len(text)} chars total]"


# --- queries -----------------------------------------------------------------


async def _agent_tasks(
    task_type: str | None,
    status: str | None,
    since_hours: int | None,
    limit: int,
) -> dict[str, Any]:
    async with async_session() as session:
        query = select(AgentTask).order_by(desc(AgentTask.created_at))
        if task_type:
            query = query.where(AgentTask.task_type == task_type)
        if status:
            query = query.where(AgentTask.status == status)
        if since_hours:
            cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
            query = query.where(AgentTask.created_at >= cutoff)
        result = await session.execute(query.limit(limit))
        tasks = result.scalars().all()

    return {
        "count": len(tasks),
        "tasks": [
            {
                "id": t.id,
                "type": t.task_type,
                "trigger": t.trigger,
                "status": t.status,
                "summary": (t.summary or "")[:500],
                "conversation_id": t.conversation_id,
                "tool_calls": len((t.actions_taken or {}).get("tool_calls", []) or []),
                "tokens": (t.actions_taken or {}).get("tokens"),
                "created_at": _iso(t.created_at),
                "completed_at": _iso(t.completed_at),
            }
            for t in tasks
        ],
    }


async def _task_detail(task_id: int) -> dict[str, Any]:
    async with async_session() as session:
        result = await session.execute(select(AgentTask).where(AgentTask.id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            return {"error": f"No task with id {task_id}"}

        conversation = None
        messages: list[Message] = []
        if task.conversation_id:
            result = await session.execute(
                select(Conversation).where(Conversation.id == task.conversation_id)
            )
            conversation = result.scalar_one_or_none()
            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == task.conversation_id)
                .order_by(Message.created_at)
            )
            messages = list(result.scalars().all())

    rendered = []
    for message in messages:
        content = message.content or {}
        entry: dict[str, Any] = {"role": message.role, "at": _iso(message.created_at)}
        text = content.get("text")
        if text:
            entry["text"] = _truncate(str(text), MAX_MESSAGE_TEXT_CHARS)
        calls = content.get("tool_calls")
        if calls:
            entry["tool_calls"] = [
                {
                    "tool": c.get("tool"),
                    "input": c.get("input"),
                    "result": _truncate(str(c["result"]), MAX_TOOL_RESULT_CHARS)
                    if c.get("result") is not None
                    else None,
                }
                for c in calls
            ]
        rendered.append(entry)

    return {
        "id": task.id,
        "type": task.task_type,
        "trigger": task.trigger,
        "status": task.status,
        "summary": task.summary,
        "actions_taken": task.actions_taken,
        "created_at": _iso(task.created_at),
        "completed_at": _iso(task.completed_at),
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "source": conversation.source,
            "status": conversation.status,
        }
        if conversation
        else None,
        "messages": rendered,
    }


async def _memories(category: str | None, limit: int) -> dict[str, Any]:
    from home_ops_agent.agent.memory import PERISHABLE_MAX_AGE, _is_stale

    now = datetime.now(UTC)
    async with async_session() as session:
        query = select(Memory).order_by(desc(Memory.created_at))
        if category:
            query = query.where(Memory.category == category)
        result = await session.execute(query.limit(limit))
        memories = result.scalars().all()

    return {
        "count": len(memories),
        "perishable_max_age_days": PERISHABLE_MAX_AGE.days,
        "memories": [
            {
                "id": m.id,
                "category": m.category,
                "content": m.content,
                "created_at": _iso(m.created_at),
                # Stale entries stay in the table but are withheld from agent
                # prompts; surfacing that here avoids reasoning about a fact the
                # agent itself can no longer see.
                "in_prompt": not _is_stale(m, now),
                "hand_written": m.source_conversation_id is None,
            }
            for m in memories
        ],
    }


async def _agent_status() -> dict[str, Any]:
    from home_ops_agent.agent.models import _DEFAULTS
    from home_ops_agent.agent.skills import registry
    from home_ops_agent.api import status as status_api
    from home_ops_agent.auth.credentials import build_credentials
    from home_ops_agent.workers.pr_monitor import last_pr_check_at

    credentials = await build_credentials()

    async with async_session() as session:
        result = await session.execute(select(Setting))
        db_settings = {s.key: s.value for s in result.scalars().all()}
        result = await session.execute(
            select(AgentTask.task_type, func.count(AgentTask.id)).group_by(AgentTask.task_type)
        )
        task_counts = {row[0]: row[1] for row in result.all()}

    models = {
        task: db_settings.get(f"model_{task}") or default for task, default in _DEFAULTS.items()
    }

    skills = []
    for skill in registry.get_all():
        enabled, _ = await registry._get_skill_settings(skill.id)
        skills.append({"id": skill.id, "enabled": enabled, "builtin": skill.builtin})

    return {
        "agent_enabled": db_settings.get("agent_enabled", "true").lower() in ("true", "1", "yes"),
        "pr_mode": db_settings.get("pr_mode", "comment_only"),
        "providers_configured": sorted(credentials.available_providers()),
        "models": models,
        "skills": skills,
        "github_repo": settings.github_repo,
        "last_pr_check_at": _iso(last_pr_check_at),
        "pr_check_running": status_api._pr_check_in_flight(),
        "last_pr_check_result": status_api._pr_check_last_result,
        "task_counts": task_counts,
    }


async def _costs(days: int) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with async_session() as session:
        result = await session.execute(
            select(
                ApiUsage.model,
                func.sum(ApiUsage.input_tokens),
                func.sum(ApiUsage.output_tokens),
                func.sum(ApiUsage.cost_usd),
                func.count(ApiUsage.id),
            )
            .where(ApiUsage.created_at >= cutoff)
            .group_by(ApiUsage.model)
        )
        rows = result.all()

    by_model = [
        {
            "model": row[0],
            "input_tokens": int(row[1] or 0),
            "output_tokens": int(row[2] or 0),
            "cost_usd": round(float(row[3] or 0.0), 6),
            "requests": int(row[4] or 0),
        }
        for row in rows
    ]
    return {
        "days": days,
        "total_cost_usd": round(sum(m["cost_usd"] for m in by_model), 6),
        "total_requests": sum(m["requests"] for m in by_model),
        # Subscription-billed models (kimi, ChatGPT, claude-code/*) record $0,
        # so a $0 total with requests > 0 is expected, not missing data.
        "by_model": sorted(by_model, key=lambda m: -m["cost_usd"]),
    }


# --- server ------------------------------------------------------------------


def allowed_hosts() -> list[str]:
    """Host header values the endpoint will accept.

    The MCP SDK enables DNS-rebinding protection by default and ships an empty
    allowlist, which rejects every request — including ones arriving through
    the ingress. The public host is derived from `base_url` so a correct
    deployment needs no extra configuration, with `MCP_ALLOWED_HOSTS` for
    anything that reaches the pod under a different name.
    """
    hosts = {"localhost", "127.0.0.1"}
    parsed = urlparse(settings.base_url)
    if parsed.hostname:
        hosts.add(parsed.hostname)
        if parsed.port:
            hosts.add(f"{parsed.hostname}:{parsed.port}")
    for extra in settings.mcp_allowed_hosts.split(","):
        extra = extra.strip()
        if extra:
            hosts.add(extra)
    return sorted(hosts)


def build_server():
    """Construct the FastMCP server with the read-only tool set."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = allowed_hosts()

    mcp = FastMCP(
        name="home-ops-agent",
        instructions=(
            "Read-only access to the home-ops-agent operator: what it has run, the "
            "conversations and tool calls behind each run, what it remembers, how it "
            "is configured, and what it has cost. Nothing here changes cluster or "
            "agent state."
        ),
        stateless_http=True,
        json_response=True,
        # Mounting at MCP_PATH strips that prefix, so the inner app serves at
        # its own root; leaving the default "/mcp" here would require /mcp/mcp.
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=hosts,
            # MCP clients are not browsers and generally send no Origin; mirror
            # the host list rather than allowing any origin.
            allowed_origins=[f"https://{h}" for h in hosts] + [f"http://{h}" for h in hosts],
        ),
    )

    @mcp.tool()
    async def agent_tasks(
        task_type: str | None = None,
        status: str | None = None,
        since_hours: int | None = None,
        limit: int = 20,
    ) -> str:
        """List what the agent has run, most recent first.

        task_type is one of pr_review, pr_merge, alert_response, alert_triage,
        alert_fix, user_chat, cluster_fix, code_fix (the agent_tasks.task_type
        enum in database.py — a Postgres enum, so the set only changes with a
        migration). status is completed or failed. since_hours limits to recent
        work. Use task_detail for the full trace of one task.
        """
        return json.dumps(
            await _agent_tasks(task_type, status, since_hours, min(limit, 100)), default=str
        )

    @mcp.tool()
    async def task_detail(task_id: int) -> str:
        """Full record of one task: its conversation, every tool call and result.

        This is the tool for diagnosing why a run behaved the way it did — the
        summary rarely says. Long tool results are truncated.
        """
        return json.dumps(await _task_detail(task_id), default=str)

    @mcp.tool()
    async def memories(category: str | None = None, limit: int = 50) -> str:
        """Facts the agent carries into every prompt.

        Includes `in_prompt`, which is false for entries withheld as stale, and
        `hand_written` for ones added through the UI rather than extracted.
        """
        return json.dumps(await _memories(category, min(limit, 200)), default=str)

    @mcp.tool()
    async def agent_status() -> str:
        """How the agent is configured right now.

        Providers with credentials, the model assigned to each task, which
        skills are enabled, PR mode, and the last PR check's outcome.
        """
        return json.dumps(await _agent_status(), default=str)

    @mcp.tool()
    async def costs(days: int = 30) -> str:
        """Token usage and cost by model over the last N days."""
        return json.dumps(await _costs(max(1, min(days, 365))), default=str)

    return mcp


class BearerTokenMiddleware:
    """Require ``Authorization: Bearer <token>`` on every MCP request.

    FastMCP's built-in auth is a full OAuth provider, which is more than a
    single-operator server needs. This is the minimum that keeps a
    machine-readable read-all interface from being open on the cluster network.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {self.token}"
        # Compare with compare_digest so the check is not timing-dependent.
        import hmac

        if not hmac.compare_digest(provided, expected):
            body = json.dumps({"error": "unauthorized"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


def mount(app) -> bool:
    """Mount the MCP endpoint on the FastAPI app if a token is configured.

    Returns whether it was mounted. Absent a token the endpoint does not exist,
    so an unconfigured deployment cannot expose it by accident.
    """
    token = settings.mcp_api_token
    if not token:
        logger.info("MCP_API_TOKEN not set — MCP endpoint disabled")
        return False

    global _server
    server = build_server()
    # streamable_http_app() must be called before session_manager exists.
    http_app = server.streamable_http_app()
    app.mount(MCP_PATH, BearerTokenMiddleware(http_app, token))
    _server = server
    logger.info(
        "MCP endpoint mounted at %s (bearer auth required, hosts: %s)",
        MCP_PATH,
        ", ".join(allowed_hosts()),
    )
    return True


@asynccontextmanager
async def lifespan() -> AsyncIterator[None]:
    """Run the MCP session manager for the life of the app.

    Must be entered by the parent app's lifespan. A no-op when the endpoint is
    not mounted.
    """
    if _server is None:
        yield
        return
    async with _server.session_manager.run():
        yield
