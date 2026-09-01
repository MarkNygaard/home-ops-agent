"""REST endpoints for usage analytics.

This was a cost page. Every configurable provider now bills a subscription or
plan rather than per token, so cost is $0 everywhere and a page organised around
it shows nothing — its proportion bars were computed as a share of a total that
is now zero.

Token volume and run outcomes are the useful signal instead, and they come from
two different tables with two different vocabularies, so they are reported
separately rather than merged into one misleading axis:

- ``api_usage`` rows are written per model call, keyed by the *model task*
  (``pr_review``, ``alert_triage``, ``code_fix``, ``chat`` …). This is where
  token counts live.
- ``agent_tasks`` rows are written per run, keyed by the ``task_type`` Postgres
  enum (which includes ``pr_merge``, ``alert_response``, ``user_chat`` …) and
  carry a status. This is the only place a *failure* is visible.

Cost is still computed and returned, but ``is_billed`` says whether any of it is
non-zero so the UI can hide the whole notion until a metered provider exists.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from home_ops_agent.agent.costs import MODEL_PRICING
from home_ops_agent.database import AgentTask, ApiUsage, async_session

router = APIRouter()


@router.get("/api/analytics")
async def get_analytics(
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
):
    """Aggregated token usage, run outcomes and (if any) cost."""
    async with async_session() as session:
        # Computed in Python, not as func.make_interval(): that is
        # Postgres-only, and it is why the cost endpoint this replaces had
        # no test coverage at all — it could not run against SQLite.
        cutoff = datetime.now(UTC) - timedelta(days=days)

        # --- per model: tokens, requests, cost ---
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
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_tokens": int(row.input_tokens or 0) + int(row.output_tokens or 0),
                "cost_usd": round(float(row.cost_usd or 0.0), 6),
                "requests": int(row.requests),
            }
            for row in result.all()
        ]

        # --- per model task: tokens too, not just cost ---
        result = await session.execute(
            select(
                ApiUsage.task_type,
                func.sum(ApiUsage.input_tokens).label("input_tokens"),
                func.sum(ApiUsage.output_tokens).label("output_tokens"),
                func.sum(ApiUsage.cost_usd).label("cost_usd"),
                func.count(ApiUsage.id).label("requests"),
            )
            .where(ApiUsage.created_at >= cutoff)
            .group_by(ApiUsage.task_type)
        )
        by_task = [
            {
                "task_type": row.task_type,
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_tokens": int(row.input_tokens or 0) + int(row.output_tokens or 0),
                "cost_usd": round(float(row.cost_usd or 0.0), 6),
                "requests": int(row.requests),
            }
            for row in result.all()
        ]

        # --- run outcomes: the only view of a failure ---
        result = await session.execute(
            select(
                AgentTask.task_type,
                AgentTask.status,
                func.count(AgentTask.id).label("count"),
            )
            .where(AgentTask.created_at >= cutoff)
            .group_by(AgentTask.task_type, AgentTask.status)
        )
        outcomes: dict[str, dict[str, int]] = {}
        for row in result.all():
            entry = outcomes.setdefault(row.task_type, {"completed": 0, "failed": 0, "total": 0})
            if row.status in entry:
                entry[row.status] += int(row.count)
            entry["total"] += int(row.count)

        runs = sorted(
            ({"task_type": task_type, **counts} for task_type, counts in outcomes.items()),
            key=lambda r: -r["total"],
        )

    total_cost = sum(m["cost_usd"] for m in by_model)
    total_input = sum(m["input_tokens"] for m in by_model)
    total_output = sum(m["output_tokens"] for m in by_model)

    return {
        "days": days,
        # Whether cost is worth showing at all. False while every provider is
        # plan-billed; flips on its own if a metered one is ever added back.
        "is_billed": total_cost > 0,
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "total_requests": sum(m["requests"] for m in by_model),
        "by_model": sorted(by_model, key=lambda m: -m["total_tokens"]),
        "by_task": sorted(by_task, key=lambda t: -t["total_tokens"]),
        "runs": runs,
        "total_runs": sum(r["total"] for r in runs),
        "total_failed": sum(r["failed"] for r in runs),
        "pricing": MODEL_PRICING,
    }
