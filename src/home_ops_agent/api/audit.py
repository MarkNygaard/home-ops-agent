"""REST endpoint for the write audit.

One list of everything the agent changed, newest first, across every run. The
conversation views answer "what did this run do"; this answers "what has been
done to my cluster since I last looked", which is the question that matters
after leaving it running unattended.

Read-only. The rows are a record, and a record you can edit is not one.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from home_ops_agent.audit import WRITE_TOOLS
from home_ops_agent.database import ToolWrite, async_session

router = APIRouter()


@router.get("/api/audit")
async def get_audit(
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(200, ge=1, le=1000),
    outcome: str | None = Query(None, description="ok, blocked or error"),
    source: str | None = Query(None, description="Which agent made the write"),
):
    """Recent mutating tool calls, newest first."""
    since = datetime.now(UTC) - timedelta(days=days)

    async with async_session() as session:
        stmt = select(ToolWrite).where(ToolWrite.created_at >= since)
        if outcome:
            stmt = stmt.where(ToolWrite.outcome == outcome)
        if source:
            stmt = stmt.where(ToolWrite.source == source)
        rows = (
            (await session.execute(stmt.order_by(ToolWrite.created_at.desc()).limit(limit)))
            .scalars()
            .all()
        )

        # Counted over the window rather than over `rows`, so the totals do not
        # change when you filter the list or hit the limit.
        counts = dict(
            (
                await session.execute(
                    select(ToolWrite.outcome, func.count())
                    .where(ToolWrite.created_at >= since)
                    .group_by(ToolWrite.outcome)
                )
            ).all()
        )
        sources = sorted(
            s
            for (s,) in (
                await session.execute(
                    select(ToolWrite.source).where(ToolWrite.created_at >= since).distinct()
                )
            ).all()
        )

    return {
        "days": days,
        "writes": [
            {
                "id": r.id,
                "tool": r.tool,
                "target": r.target,
                "source": r.source,
                "outcome": r.outcome,
                "detail": r.detail,
                "conversation_id": r.conversation_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "counts": {
            "ok": counts.get("ok", 0),
            "blocked": counts.get("blocked", 0),
            "error": counts.get("error", 0),
            "total": sum(counts.values()),
        },
        "sources": sources,
        # So the UI can say what is *not* in the list: reads are excluded, and
        # a page that does not say so reads like "the agent did nothing".
        "tracked_tools": sorted(WRITE_TOOLS),
    }
