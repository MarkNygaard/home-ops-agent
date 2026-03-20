"""Periodic PR monitor — checks open PRs and triggers agent review."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from home_ops_agent.agent.core import Agent, AgentResult
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import PR_REVIEW_PROMPT
from home_ops_agent.agent.tools.github import get_github_tools
from home_ops_agent.agent.tools.ntfy import get_ntfy_tools
from sqlalchemy import select

from home_ops_agent.auth.oauth import get_claude_credentials
from home_ops_agent.config import settings
from home_ops_agent.database import AgentTask, Conversation, Message, Setting, async_session

logger = logging.getLogger(__name__)

# Track which PRs we've already reviewed (by number + head SHA)
_reviewed: set[str] = set()


async def _get_pr_mode() -> str:
    """Get current PR review mode from settings."""
    async with async_session() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "pr_mode")
        )
        setting = result.scalar_one_or_none()
        return setting.value if setting else "comment_only"


async def _review_pr(pr: dict, agent: Agent) -> AgentResult | None:
    """Run the agent to review a single PR."""
    pr_key = f"{pr['number']}:{pr.get('head_sha', '')}"
    if pr_key in _reviewed:
        return None

    pr_mode = await _get_pr_mode()
    mode_instruction = (
        "You are in COMMENT-ONLY mode. Post a review comment but do NOT merge."
        if pr_mode == "comment_only"
        else "Auto-merge is ENABLED. You may merge PRs that meet all auto-merge criteria."
    )

    messages = [
        {
            "role": "user",
            "content": (
                f"{mode_instruction}\n\n"
                f"Review PR #{pr['number']}: {pr['title']}\n"
                f"Author: {pr['author']}\n"
                f"Labels: {', '.join(pr.get('labels', []))}\n"
                f"URL: {pr.get('html_url', '')}\n\n"
                "Use the available tools to get the PR details, check CI status, "
                "review the changed files, and then post your review comment."
            ),
        }
    ]

    try:
        model = await get_model_for_task("pr_review")
        result = await agent.run(
            system_prompt=PR_REVIEW_PROMPT,
            messages=messages,
            model=model,
            max_turns=10,
        )
        _reviewed.add(pr_key)
        return result
    except Exception:
        logger.exception("Failed to review PR #%s", pr['number'])
        return None


async def _save_task(pr: dict, result: AgentResult):
    """Save PR review task to the database."""
    async with async_session() as session:
        conversation = Conversation(
            title=f"PR Review: #{pr['number']} {pr['title'][:100]}",
            source="pr_review",
            status="completed",
        )
        session.add(conversation)
        await session.flush()

        # Save the agent's response
        msg = Message(
            conversation_id=conversation.id,
            role="assistant",
            content={"text": result.response, "tool_calls": result.tool_calls},
        )
        session.add(msg)

        task = AgentTask(
            task_type="pr_review",
            trigger=f"PR #{pr['number']}",
            status="completed",
            conversation_id=conversation.id,
            summary=result.response[:500],
            actions_taken={"tool_calls": result.tool_calls, "tokens": result.total_tokens},
            completed_at=datetime.now(timezone.utc),
        )
        session.add(task)
        await session.commit()


async def check_prs():
    """Check all open PRs and review any new/updated ones."""
    api_key, oauth_token = await get_claude_credentials()
    if not api_key and not oauth_token:
        logger.warning("No Claude credentials configured, skipping PR review")
        return

    agent = Agent(api_key=api_key, oauth_token=oauth_token)
    agent.register_tools(get_github_tools())
    agent.register_tools(get_ntfy_tools())

    # List open PRs via direct API call (not through agent)
    from home_ops_agent.agent.tools.github import list_prs
    prs_json = await list_prs({"state": "open"})
    prs = json.loads(prs_json)

    if not prs:
        logger.debug("No open PRs to review")
        return

    logger.info("Found %d open PRs to check", len(prs))

    for pr in prs:
        result = await _review_pr(pr, agent)
        if result:
            await _save_task(pr, result)
            logger.info("Reviewed PR #%s: %s", pr["number"], result.response[:100])


async def _get_check_interval() -> int:
    """Get PR check interval from DB settings, falling back to env config."""
    async with async_session() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "pr_check_interval_seconds")
        )
        setting = result.scalar_one_or_none()
        if setting:
            return int(setting.value)
    return settings.pr_check_interval_seconds


async def run_pr_monitor():
    """Background task: periodically check PRs."""
    logger.info("PR monitor started (default interval: %ds)", settings.pr_check_interval_seconds)

    while True:
        try:
            await check_prs()
        except Exception:
            logger.exception("PR monitor cycle failed")

        interval = await _get_check_interval()
        await asyncio.sleep(interval)
