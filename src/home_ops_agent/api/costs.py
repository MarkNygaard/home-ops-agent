"""REST endpoints for API cost tracking."""

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from home_ops_agent.agent.costs import MODEL_PRICING
from home_ops_agent.database import ApiUsage, async_session

router = APIRouter()


@router.get("/api/costs")
async def get_costs(
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
):
    """Get aggregated API costs grouped by model, with a grand total."""
    async with async_session() as session:
        cutoff = func.now() - func.make_interval(0, 0, 0, days)

        # Per-model aggregation
        result = await session.execute(
            select(
                ApiUsage.model,
                func.sum(ApiUsage.input_tokens).label("input_tokens"),
                func.sum(ApiUsage.output_tokens).label("output_tokens"),
                func.sum(ApiUsage.cost_usd).label("cost_usd"),
                func.count(ApiUsage.id).label("requests"),
            )
            .where(ApiUsage.created_at >= cutoff)
            .group_by(ApiUsage.model)
        )
        by_model = [
            {
                "model": row.model,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": round(float(row.cost_usd), 6),
                "requests": int(row.requests),
            }
            for row in result.all()
        ]

        # Per-task-type aggregation
        result = await session.execute(
            select(
                ApiUsage.task_type,
                func.sum(ApiUsage.cost_usd).label("cost_usd"),
                func.count(ApiUsage.id).label("requests"),
            )
            .where(ApiUsage.created_at >= cutoff)
            .group_by(ApiUsage.task_type)
        )
        by_task = [
            {
                "task_type": row.task_type,
                "cost_usd": round(float(row.cost_usd), 6),
                "requests": int(row.requests),
            }
            for row in result.all()
        ]

        total_cost = sum(m["cost_usd"] for m in by_model)
        total_input = sum(m["input_tokens"] for m in by_model)
        total_output = sum(m["output_tokens"] for m in by_model)
        total_requests = sum(m["requests"] for m in by_model)

    return {
        "days": days,
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_requests": total_requests,
        "by_model": by_model,
        "by_task": by_task,
        "pricing": MODEL_PRICING,
    }
