"""API cost tracking — calculates and records Anthropic API usage."""

import logging

from home_ops_agent.agent import providers
from home_ops_agent.database import ApiUsage, async_session

logger = logging.getLogger(__name__)

# Pricing per million tokens (USD).
#
# Every configurable provider now bills a subscription or plan rather than per
# token, so these are all zero: usage rows still record token counts, without
# implying a per-token charge that is not made. The dated Anthropic entries were
# removed along with the metered Anthropic provider; historical `api_usage` rows
# keep the cost that was computed when they were written.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "kimi-for-coding": {"input": 0.00, "output": 0.00},
    "gpt-5.6-sol": {"input": 0.00, "output": 0.00},
    "gpt-5.6-terra": {"input": 0.00, "output": 0.00},
    "gpt-5.6-luna": {"input": 0.00, "output": 0.00},
    "gpt-5.5": {"input": 0.00, "output": 0.00},
}

# Unknown models: $0, matching every provider that can actually be configured.
# A metered provider added later needs its real prices here, not this default.
_DEFAULT_PRICING = {"input": 0.00, "output": 0.00}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for a given model and token counts."""
    # `claude-code/*` runs bill a Claude subscription, not API credit. The
    # suffix is a free-form CLI model name, so match the prefix rather than
    # adding table entries — otherwise the Sonnet fallback below would invent
    # an API price for a request that was never metered.
    if providers.resolve_provider(model) == providers.CLAUDE_CODE:
        return 0.0
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
