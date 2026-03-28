"""API cost tracking — calculates and records Anthropic API usage."""

import logging

from home_ops_agent.database import ApiUsage, async_session

logger = logging.getLogger(__name__)

# Pricing per million tokens (USD) — update when Anthropic changes pricing.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}

# Fallback for unknown models (use Sonnet pricing as a reasonable default).
_DEFAULT_PRICING = {"input": 3.00, "output": 15.00}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for a given model and token counts."""
    pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


async def record_usage(
    model: str,
    task_type: str,
    input_tokens: int,
    output_tokens: int,
    task_id: int | None = None,
) -> None:
    """Record an API usage entry in the database."""
    cost = calculate_cost(model, input_tokens, output_tokens)
    async with async_session() as session:
        session.add(
            ApiUsage(
                model=model,
                task_type=task_type,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                task_id=task_id,
            )
        )
        await session.commit()
    logger.debug(
        "Recorded API usage: model=%s task=%s in=%d out=%d cost=$%.6f",
        model,
        task_type,
        input_tokens,
        output_tokens,
        cost,
    )
