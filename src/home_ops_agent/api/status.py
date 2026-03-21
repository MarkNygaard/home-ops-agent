"""REST endpoints for health, task history, and agent status."""

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import delete, desc, func, select

from home_ops_agent.auth.oauth import get_auth_method, get_valid_token
from home_ops_agent.config import settings
from home_ops_agent.database import AgentTask, Conversation, Memory, Message, async_session

router = APIRouter()


@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


@router.get("/api/status")
async def agent_status():
    """Get agent status overview."""
    from home_ops_agent.workers.pr_monitor import last_pr_check_at

    auth_method = await get_auth_method()
    has_credentials = False

    if auth_method == "oauth":
        token = await get_valid_token()
        has_credentials = token is not None
    else:
        has_credentials = bool(settings.anthropic_api_key)

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
        "auth_method": auth_method,
        "has_credentials": has_credentials,
        "github_repo": settings.github_repo,
        "cluster_domain": settings.cluster_domain,
        "last_pr_check_at": last_pr_check_at.isoformat() if last_pr_check_at else None,
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
