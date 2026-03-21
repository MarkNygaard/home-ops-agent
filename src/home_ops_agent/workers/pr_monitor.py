"""Periodic PR monitor — checks open PRs and triggers agent review."""

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from home_ops_agent.agent.core import Agent, AgentResult
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.agent.skills import registry
from home_ops_agent.auth.oauth import get_claude_credentials
from home_ops_agent.config import settings
from home_ops_agent.database import AgentTask, Conversation, Message, Setting, async_session

logger = logging.getLogger(__name__)

# Maximum number of PRs to review per cycle (rate limit)
MAX_REVIEWS_PER_CYCLE = 3


async def _is_enabled() -> bool:
    """Check if the agent is enabled via settings."""
    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == "agent_enabled"))
        setting = result.scalar_one_or_none()
        # Enabled by default
        if setting is None:
            return True
        return setting.value.lower() in ("true", "1", "yes")


async def _get_pr_mode() -> str:
    """Get current PR review mode from settings."""
    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == "pr_mode"))
        setting = result.scalar_one_or_none()
        return setting.value if setting else "comment_only"


async def _already_reviewed(pr_number: int, head_sha: str) -> bool:
    """Check if we already reviewed this PR at this SHA (DB-backed, survives restarts)."""
    async with async_session() as session:
        result = await session.execute(
            select(AgentTask).where(
                AgentTask.task_type == "pr_review",
                AgentTask.trigger == f"PR #{pr_number}",
                AgentTask.status == "completed",
            )
        )
        tasks = result.scalars().all()
        for task in tasks:
            if task.actions_taken and task.actions_taken.get("head_sha") == head_sha:
                return True
        return False


async def _review_pr(pr: dict, agent: Agent) -> AgentResult | None:
    """Run the agent to review a single PR."""
    pr_number = pr["number"]
    head_sha = pr.get("head_sha", "")

    if await _already_reviewed(pr_number, head_sha):
        logger.debug("PR #%s already reviewed at SHA %s, skipping", pr_number, head_sha[:8])
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
        prompt = await get_prompt("pr_review")
        result = await agent.run(
            system_prompt=prompt,
            messages=messages,
            model=model,
            max_turns=10,
        )
        return result
    except Exception:
        logger.exception("Failed to review PR #%s", pr["number"])
        return None


async def _notify_review(pr: dict, result: AgentResult):
    """Send ntfy notification about a completed PR review."""
    from home_ops_agent.agent.tools.ntfy import publish_notification

    # Determine risk level from the response
    response_lower = result.response.lower()
    if "needs_review" in response_lower or "high risk" in response_lower:
        priority = "high"
        tag = "warning"
        title = f"⚠️ PR #{pr['number']} needs your review"
    elif "safe_to_merge" in response_lower:
        priority = "default"
        tag = "white_check_mark"
        title = f"✅ PR #{pr['number']} reviewed — safe to merge"
    else:
        priority = "default"
        tag = "mag"
        title = f"🔍 PR #{pr['number']} reviewed"

    # Truncate summary for notification
    summary = result.response[:300]
    if len(result.response) > 300:
        summary += "..."

    try:
        await publish_notification(
            {
                "title": title,
                "message": f"{pr['title']}\n\n{summary}",
                "priority": priority,
                "tags": tag,
                "click_url": pr.get("html_url", ""),
            }
        )
    except Exception:
        logger.exception("Failed to send ntfy notification for PR #%s", pr["number"])


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
            actions_taken={
                "tool_calls": result.tool_calls,
                "tokens": result.total_tokens,
                "head_sha": pr.get("head_sha", ""),
            },
            completed_at=datetime.now(UTC),
        )
        session.add(task)
        await session.commit()


async def _get_review_summary(pr_number: int, head_sha: str) -> str | None:
    """Get the review summary for a previously reviewed PR."""
    async with async_session() as session:
        result = await session.execute(
            select(AgentTask).where(
                AgentTask.task_type == "pr_review",
                AgentTask.trigger == f"PR #{pr_number}",
                AgentTask.status == "completed",
            )
        )
        tasks = result.scalars().all()
        for task in tasks:
            if task.actions_taken and task.actions_taken.get("head_sha") == head_sha:
                return task.summary
        return None


async def _is_safe_to_auto_merge(pr: dict, summary: str) -> bool:
    """Check if a previously reviewed PR meets auto-merge criteria."""
    summary_lower = summary.lower()

    # Must have been rated as safe to merge
    if "safe_to_merge" not in summary_lower:
        return False

    # Must be from renovate
    if pr.get("author") != "renovate[bot]":
        return False

    # Must be patch or digest (not minor/major)
    labels = pr.get("labels", [])
    safe_labels = {"type/patch", "type/digest"}
    if not any(label in safe_labels for label in labels):
        return False

    return True


async def _auto_merge_reviewed_prs(prs: list[dict], agent: Agent):
    """Try to auto-merge already-reviewed PRs that are safe to merge.

    This handles the case where PRs were reviewed in comment-only mode
    and the user later switches to auto-merge mode.
    """
    from home_ops_agent.agent.tools.github import merge_pr
    from home_ops_agent.agent.tools.ntfy import publish_notification

    merged_count = 0
    for pr in prs:
        if merged_count >= MAX_REVIEWS_PER_CYCLE:
            break

        pr_number = pr["number"]
        head_sha = pr.get("head_sha", "")

        # Only consider PRs that were already reviewed
        if not await _already_reviewed(pr_number, head_sha):
            continue

        summary = await _get_review_summary(pr_number, head_sha)
        if not summary:
            continue

        if not await _is_safe_to_auto_merge(pr, summary):
            continue

        # Merge it
        logger.info("Auto-merging PR #%s: %s", pr_number, pr["title"])
        result = await merge_pr({"pr_number": pr_number})
        merge_result = json.loads(result)

        if merge_result.get("status") == "merged":
            merged_count += 1
            logger.info("Successfully merged PR #%s", pr_number)
            try:
                await publish_notification(
                    {
                        "title": f"🔀 Auto-merged PR #{pr_number}",
                        "message": pr["title"],
                        "priority": "default",
                        "tags": "merged",
                        "click_url": pr.get("html_url", ""),
                    }
                )
            except Exception:
                logger.exception(
                    "Failed to send merge notification for PR #%s", pr_number
                )
        else:
            logger.warning(
                "Failed to merge PR #%s: %s",
                pr_number,
                merge_result.get("message", "unknown error"),
            )


async def check_prs():
    """Check all open PRs and review any new/updated ones."""
    if not await _is_enabled():
        logger.debug("Agent is disabled, skipping PR review")
        return

    api_key, oauth_token = await get_claude_credentials()
    if not api_key and not oauth_token:
        logger.warning("No Claude credentials configured, skipping PR review")
        return

    agent = Agent(api_key=api_key, oauth_token=oauth_token)
    skill_tools = await registry.get_all_enabled_tools()
    agent.register_tools(skill_tools)

    # List open PRs via direct API call (not through agent)
    from home_ops_agent.agent.tools.github import list_prs

    prs_json = await list_prs({"state": "open"})
    prs = json.loads(prs_json)

    if not prs:
        logger.debug("No open PRs to review")
        return

    logger.info("Found %d open PRs to check", len(prs))

    # Auto-merge previously reviewed PRs if in auto-merge mode
    pr_mode = await _get_pr_mode()
    if pr_mode == "auto_merge":
        await _auto_merge_reviewed_prs(prs, agent)

    reviewed_count = 0
    for pr in prs:
        if reviewed_count >= MAX_REVIEWS_PER_CYCLE:
            logger.info(
                "Rate limit reached (%d reviews), remaining PRs next cycle",
                MAX_REVIEWS_PER_CYCLE,
            )
            break

        result = await _review_pr(pr, agent)
        if result:
            await _save_task(pr, result)
            await _notify_review(pr, result)
            reviewed_count += 1
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
    logger.info(
        "PR monitor started (default interval: %ds)",
        settings.pr_check_interval_seconds,
    )

    while True:
        try:
            await check_prs()
        except Exception:
            logger.exception("PR monitor cycle failed")

        interval = await _get_check_interval()
        await asyncio.sleep(interval)
