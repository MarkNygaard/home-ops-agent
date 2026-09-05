"""REST endpoints for health, task history, and agent status."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import delete, desc, func, select

from home_ops_agent.agent.memory import MEMORY_CATEGORIES
from home_ops_agent.auth.credentials import build_credentials
from home_ops_agent.config import settings
from home_ops_agent.database import AgentTask, Conversation, Memory, Message, async_session

logger = logging.getLogger(__name__)

router = APIRouter()

# The in-flight manual PR check, if any.
#
# This holds a *strong* reference on purpose. asyncio.create_task() hands the
# event loop only a weak reference, so a task nothing else refers to can be
# garbage-collected mid-execution -- and when that happened here, the `finally`
# that cleared the old boolean flag never ran, leaving the trigger permanently
# reporting "already_running" until the pod restarted.
_pr_check_task: "asyncio.Task | None" = None
_pr_check_started_at: datetime | None = None
_pr_check_last_result: dict | None = None
# Last cluster health check cycle, surfaced alongside the PR check so the
# dashboard shows both background loops rather than only the noisy one.
_health_check_last_result: dict | None = None

# A run that outlives this is treated as wedged and superseded, so no single
# stuck HTTP call can disable the button until the next restart.
PR_CHECK_STALE_AFTER = timedelta(minutes=15)


def _pr_check_in_flight() -> bool:
    """Is a manual check genuinely still running?

    Derived from the task rather than a flag: a task that finished, raised, or
    was cancelled reports done(), so no failure mode can leave a stale "yes".
    """
    return _pr_check_task is not None and not _pr_check_task.done()


@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


@router.get("/api/status")
async def agent_status():
    """Get agent status overview."""
    from home_ops_agent.workers.pr_monitor import last_pr_check_at

    credentials = await build_credentials()
    available_providers = sorted(credentials.available_providers())
    has_credentials = credentials.has_any()

    async with async_session() as session:
        # Count tasks by type
        result = await session.execute(
            select(AgentTask.task_type, func.count(AgentTask.id)).group_by(AgentTask.task_type)
        )
        task_counts = {row[0]: row[1] for row in result.all()}

        # Recent task
        result = await session.execute(
            select(AgentTask).order_by(desc(AgentTask.created_at)).limit(1)
        )
        latest_task = result.scalar_one_or_none()

    return {
        "providers": available_providers,
        "has_credentials": has_credentials,
        "github_repo": settings.github_repo,
        "cluster_domain": settings.cluster_domain,
        "last_pr_check_at": last_pr_check_at.isoformat() if last_pr_check_at else None,
        "pr_check_running": _pr_check_in_flight(),
        "last_pr_check_result": _pr_check_last_result,
        "last_health_check_result": _health_check_last_result,
        "task_counts": task_counts,
        "latest_task": {
            "type": latest_task.task_type,
            "trigger": latest_task.trigger,
            "status": latest_task.status,
            "created_at": latest_task.created_at.isoformat() if latest_task.created_at else None,
            "summary": latest_task.summary,
        }
        if latest_task
        else None,
    }


@router.post("/api/pr-check")
async def trigger_pr_check():
    """Trigger an immediate PR review cycle."""
    import home_ops_agent.workers.pr_monitor as pr_monitor

    global _pr_check_task, _pr_check_started_at, _pr_check_last_result

    if _pr_check_in_flight():
        running_for = datetime.now(UTC) - (_pr_check_started_at or datetime.now(UTC))
        if running_for < PR_CHECK_STALE_AFTER:
            return {
                "status": "already_running",
                "started_at": _pr_check_started_at.isoformat() if _pr_check_started_at else None,
                "running_for_seconds": int(running_for.total_seconds()),
            }
        logger.warning(
            "Superseding a PR check that has run for %ds", int(running_for.total_seconds())
        )
        _pr_check_task.cancel()

    # Reset the timer before the task runs, so the countdown cannot fire a
    # scheduled cycle on top of this one.
    _pr_check_started_at = datetime.now(UTC)
    pr_monitor.last_pr_check_at = _pr_check_started_at

    async def _run():
        global _pr_check_last_result
        try:
            result = await pr_monitor.check_prs()
            _pr_check_last_result = {
                **(result or {"status": "completed"}),
                "at": datetime.now(UTC).isoformat(),
            }
            pr_monitor.last_pr_check_at = datetime.now(UTC)
        except asyncio.CancelledError:
            _pr_check_last_result = {
                "status": "cancelled",
                "at": datetime.now(UTC).isoformat(),
            }
            raise
        except Exception as exc:
            logger.exception("Manual PR check failed")
            _pr_check_last_result = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "at": datetime.now(UTC).isoformat(),
            }

    _pr_check_task = asyncio.create_task(_run())
    return {"status": "started"}


@router.get("/api/history")
async def task_history(
    task_type: str | None = Query(None, description="Filter by task type"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """Get agent task history."""
    async with async_session() as session:
        query = select(AgentTask).order_by(desc(AgentTask.created_at))

        if task_type:
            query = query.where(AgentTask.task_type == task_type)

        query = query.limit(limit).offset(offset)
        result = await session.execute(query)
        tasks = result.scalars().all()

        return [
            {
                "id": t.id,
                "type": t.task_type,
                "trigger": t.trigger,
                "status": t.status,
                "summary": t.summary,
                "actions_taken": t.actions_taken,
                "conversation_id": t.conversation_id,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in tasks
        ]


@router.get("/api/history/{task_id}")
async def task_detail(task_id: int):
    """Get detailed view of a specific task including conversation messages."""
    async with async_session() as session:
        result = await session.execute(select(AgentTask).where(AgentTask.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return {"error": "Task not found"}

        messages = []
        if task.conversation_id:
            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == task.conversation_id)
                .order_by(Message.created_at)
            )
            messages = [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in result.scalars().all()
            ]

        return {
            "id": task.id,
            "type": task.task_type,
            "trigger": task.trigger,
            "status": task.status,
            "summary": task.summary,
            "actions_taken": task.actions_taken,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "messages": messages,
        }


@router.get("/api/conversations")
async def list_conversations(
    source: str | None = Query(None),
    limit: int = Query(20, le=100),
):
    """List conversations."""
    async with async_session() as session:
        query = select(Conversation).order_by(desc(Conversation.created_at))
        if source:
            query = query.where(Conversation.source == source)
        query = query.limit(limit)

        result = await session.execute(query)
        conversations = result.scalars().all()

        return [
            {
                "id": c.id,
                "title": c.title,
                "source": c.source,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in conversations
        ]


@router.get("/api/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: int):
    """Get all messages in a conversation."""
    async with async_session() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        return [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in result.scalars().all()
        ]


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int):
    """Delete a conversation and its messages."""
    async with async_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
        await session.commit()
    return {"status": "ok"}


@router.get("/api/memories")
async def list_memories(
    category: str | None = Query(None),
    limit: int = Query(50, le=200),
):
    """List agent memories."""
    async with async_session() as session:
        query = select(Memory).order_by(desc(Memory.created_at))
        if category:
            query = query.where(Memory.category == category)
        query = query.limit(limit)

        result = await session.execute(query)
        return [
            {
                "id": m.id,
                "content": m.content,
                "category": m.category,
                "source_conversation_id": m.source_conversation_id,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in result.scalars().all()
        ]


class NewMemory(BaseModel):
    content: str
    category: str = "knowledge"


@router.post("/api/memories")
async def create_memory(body: NewMemory):
    """Write a memory by hand.

    The extractor only runs on chat conversations, so a fact learned anywhere
    else — an alert investigation, a PR review, or work done outside this agent
    entirely — has no other way in. It is also the only way to correct a fact
    the extractor got wrong, since memories are otherwise append-only-by-robot.
    """
    content = body.content.strip()
    if not content:
        return {"error": "content must not be empty"}
    if body.category not in MEMORY_CATEGORIES:
        return {"error": f"category must be one of: {', '.join(sorted(MEMORY_CATEGORIES))}"}

    async with async_session() as session:
        existing = await session.execute(select(Memory).where(Memory.content == content))
        if existing.scalar_one_or_none():
            return {"error": "A memory with this exact content already exists"}

        memory = Memory(content=content, category=body.category)
        session.add(memory)
        await session.commit()
        await session.refresh(memory)

        return {
            "id": memory.id,
            "content": memory.content,
            "category": memory.category,
            "source_conversation_id": None,
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
        }


@router.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: int):
    """Delete a specific memory."""
    async with async_session() as session:
        result = await session.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()
        if not memory:
            return {"error": "Memory not found"}
        await session.delete(memory)
        await session.commit()
    return {"status": "ok"}
