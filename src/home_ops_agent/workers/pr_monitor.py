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

# Track last check time for the status API
last_pr_check_at: datetime | None = None


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
    if pr_mode == "comment_only":
        mode_instruction = "You are in COMMENT-ONLY mode. Post a review comment but do NOT merge."
    elif pr_mode == "auto_merge_all":
        mode_instruction = (
            "Auto-merge is ENABLED for ALL updates including critical components. "
            "You may merge PRs that meet auto-merge criteria. "
            "For critical components, be extra thorough in your review."
        )
    elif pr_mode == "auto_merge_minor":
        mode_instruction = (
            "Auto-merge is ENABLED for patch, digest, AND minor updates. "
            "You may merge PRs that meet all auto-merge criteria."
        )
    else:
        mode_instruction = (
            "Auto-merge is ENABLED for patch and digest updates only. "
            "You may merge PRs that meet all auto-merge criteria."
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
        title = f"PR #{pr['number']} needs your review"
    elif "safe_to_merge" in response_lower:
        priority = "default"
        tag = "white_check_mark"
        title = f"PR #{pr['number']} reviewed - safe to merge"
    else:
        priority = "default"
        tag = "mag"
        title = f"PR #{pr['number']} reviewed"

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

    # Must be from renovate
    if pr.get("author") != "renovate[bot]":
        return False

    pr_mode = await _get_pr_mode()
    labels = pr.get("labels", [])

    if pr_mode == "auto_merge_all":
        # All tiers: merge anything rated safe, including critical
        # For NEEDS_REVIEW, check if deep review approved it
        if "safe_to_merge" not in summary_lower and "safe to merge" not in summary_lower:
            return False
        # Accept all label types
        safe_labels = {"type/patch", "type/digest", "type/minor", "type/major"}
        if not any(label in safe_labels for label in labels):
            return False
    elif pr_mode == "auto_merge_minor":
        if "safe_to_merge" not in summary_lower and "safe to merge" not in summary_lower:
            return False
        safe_labels = {"type/patch", "type/digest", "type/minor"}
        if not any(label in safe_labels for label in labels):
            return False
    else:
        # auto_merge (patch only)
        if "safe_to_merge" not in summary_lower and "safe to merge" not in summary_lower:
            return False
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

            # Notify first — DB errors should not prevent notification
            try:
                await publish_notification(
                    {
                        "title": f"Auto-merged PR #{pr_number}",
                        "message": pr["title"],
                        "priority": "default",
                        "tags": "merged",
                        "click_url": pr.get("html_url", ""),
                    }
                )
            except Exception:
                logger.exception(
                    "Failed to send merge notification for PR #%s",
                    pr_number,
                )

            # Save merge task to DB so it appears in history
            try:
                async with async_session() as session:
                    task = AgentTask(
                        task_type="pr_merge",
                        trigger=f"PR #{pr_number}",
                        status="completed",
                        summary=f"Auto-merged: {pr['title']}",
                        actions_taken={
                            "action": "merge",
                            "head_sha": head_sha,
                            "merge_sha": merge_result.get("sha"),
                        },
                        completed_at=datetime.now(UTC),
                    )
                    session.add(task)
                    await session.commit()
            except Exception:
                logger.exception(
                    "Failed to save merge task for PR #%s",
                    pr_number,
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
    if pr_mode in ("auto_merge", "auto_merge_minor", "auto_merge_all"):
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
            logger.info(
                "Reviewed PR #%s: %s",
                pr["number"],
                result.response[:100],
            )

            response_lower = result.response.lower()

            # If review says NEEDS_FIX, attempt a code fix
            if "needs_fix" in response_lower:
                await _attempt_code_fix(pr, result.response, agent)

            # In auto_merge_all mode, escalate NEEDS_REVIEW to Opus
            elif "needs_review" in response_lower and pr_mode == "auto_merge_all":
                await _deep_review_pr(pr, result.response, agent)


async def _deep_review_pr(pr: dict, initial_review: str, agent: Agent):
    """Escalate a NEEDS_REVIEW PR to Opus for a deep review.

    Used in auto_merge_all mode when the initial Haiku/Sonnet review
    flags a critical component. Opus does a thorough check and either
    approves (SAFE_TO_MERGE) or confirms NEEDS_REVIEW.
    """
    pr_number = pr["number"]
    logger.info("Deep review with Opus for PR #%s", pr_number)

    try:
        prompt = await get_prompt("pr_review")
        messages = [
            {
                "role": "user",
                "content": (
                    "A previous review flagged this PR as NEEDS_REVIEW. "
                    "You are the senior reviewer — do a thorough deep review.\n\n"
                    f"PR #{pr['number']}: {pr['title']}\n"
                    f"Author: {pr['author']}\n"
                    f"Labels: {', '.join(pr.get('labels', []))}\n"
                    f"URL: {pr.get('html_url', '')}\n\n"
                    f"Initial review:\n{initial_review[:1000]}\n\n"
                    "Your task:\n"
                    "1. Fetch the release notes for the new version\n"
                    "2. Read the full diff carefully\n"
                    "3. Check for breaking changes, deprecations, "
                    "security issues\n"
                    "4. Determine if this is actually safe to merge\n\n"
                    "End your review with SAFE_TO_MERGE if approved "
                    "or NEEDS_REVIEW if it truly needs human attention. "
                    "Post your review as a comment on the PR."
                ),
            }
        ]

        # Use the deep_review model (defaults to Opus)
        model = await get_model_for_task("deep_review")
        result = await agent.run(
            system_prompt=prompt,
            messages=messages,
            model=model,
            max_turns=12,
        )

        if result:
            # Save as a separate task
            async with async_session() as session:
                conversation = Conversation(
                    title=f"Deep Review: #{pr_number} {pr['title'][:80]}",
                    source="pr_deep_review",
                    status="completed",
                )
                session.add(conversation)
                await session.flush()

                msg = Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content={
                        "text": result.response,
                        "tool_calls": result.tool_calls,
                    },
                )
                session.add(msg)

                task = AgentTask(
                    task_type="pr_review",
                    trigger=f"PR #{pr_number}",
                    status="completed",
                    conversation_id=conversation.id,
                    summary=f"[Deep Review] {result.response[:450]}",
                    actions_taken={
                        "tool_calls": result.tool_calls,
                        "tokens": result.total_tokens,
                        "head_sha": pr.get("head_sha", ""),
                        "deep_review": True,
                    },
                    completed_at=datetime.now(UTC),
                )
                session.add(task)
                await session.commit()

            # Notify
            from home_ops_agent.agent.tools.ntfy import publish_notification

            response_lower = result.response.lower()
            if "safe_to_merge" in response_lower or "safe to merge" in response_lower:
                title = f"Deep review APPROVED PR #{pr_number}"
                priority = "default"
                tags = "white_check_mark"
            else:
                title = f"Deep review: PR #{pr_number} needs attention"
                priority = "high"
                tags = "warning"

            try:
                await publish_notification(
                    {
                        "title": title,
                        "message": f"{pr['title']}\n\n{result.response[:200]}",
                        "priority": priority,
                        "tags": tags,
                        "click_url": pr.get("html_url", ""),
                    }
                )
            except Exception:
                logger.exception(
                    "Failed to send deep review notification for PR #%s",
                    pr_number,
                )

            logger.info("Deep review completed for PR #%s", pr_number)

    except Exception:
        logger.exception("Deep review failed for PR #%s", pr_number)


async def _wait_for_ci_and_merge(pr_number: int, html_url: str, title: str):
    """Wait for CI to pass on a PR, then merge it. Notify on success or failure."""
    from home_ops_agent.agent.tools.github import get_check_runs, get_pr, merge_pr
    from home_ops_agent.agent.tools.ntfy import publish_notification

    logger.info("Waiting for CI on PR #%s before merging", pr_number)

    # Wait up to 5 minutes, checking every 30 seconds
    for attempt in range(10):
        await asyncio.sleep(30)

        try:
            # Get the latest PR info (head SHA may have changed after fix commit)
            pr_json = await get_pr({"pr_number": pr_number})
            pr_data = json.loads(pr_json)
            head_sha = pr_data.get("head_sha", "")

            if not head_sha:
                continue

            checks_json = await get_check_runs({"ref": head_sha})
            checks = json.loads(checks_json)

            if not checks:
                continue

            # Check if all checks completed
            all_completed = all(c.get("status") == "completed" for c in checks)
            if not all_completed:
                continue

            # Check if all passed
            all_passed = all(
                c.get("conclusion") in ("success", "neutral", "skipped") for c in checks
            )

            if all_passed:
                # Merge it
                merge_result_json = await merge_pr({"pr_number": pr_number})
                merge_result = json.loads(merge_result_json)

                if merge_result.get("status") == "merged":
                    logger.info("CI passed, merged code fix PR #%s", pr_number)

                    async with async_session() as session:
                        task = AgentTask(
                            task_type="pr_merge",
                            trigger=f"PR #{pr_number}",
                            status="completed",
                            summary=f"Auto-merged after code fix: {title}",
                            actions_taken={
                                "action": "merge_after_fix",
                                "merge_sha": merge_result.get("sha"),
                            },
                            completed_at=datetime.now(UTC),
                        )
                        session.add(task)
                        await session.commit()

                    try:
                        await publish_notification(
                            {
                                "title": f"Code fix merged: PR #{pr_number}",
                                "message": title,
                                "priority": "default",
                                "tags": "white_check_mark",
                                "click_url": html_url,
                            }
                        )
                    except Exception:
                        logger.exception("Failed to send merge notification")
                    return
                else:
                    logger.warning(
                        "Failed to merge PR #%s: %s",
                        pr_number,
                        merge_result.get("message"),
                    )
                    return
            else:
                # CI failed
                logger.warning("CI failed on code fix PR #%s", pr_number)
                try:
                    await publish_notification(
                        {
                            "title": f"Code fix CI failed: PR #{pr_number}",
                            "message": (
                                f"{title}\n\nCI checks failed after code fix. Manual review needed."
                            ),
                            "priority": "high",
                            "tags": "x",
                            "click_url": html_url,
                        }
                    )
                except Exception:
                    logger.exception("Failed to send CI failure notification")
                return

        except Exception:
            logger.exception("Error checking CI for PR #%s, attempt %d", pr_number, attempt + 1)

    # Timed out waiting for CI
    logger.warning("Timed out waiting for CI on PR #%s", pr_number)
    try:
        await publish_notification(
            {
                "title": f"Code fix: CI timeout on PR #{pr_number}",
                "message": f"{title}\n\nCI did not complete within 5 minutes.",
                "priority": "default",
                "tags": "clock",
                "click_url": html_url,
            }
        )
    except Exception:
        logger.exception("Failed to send timeout notification")


async def _attempt_code_fix(pr: dict, review_summary: str, agent: Agent):
    """Attempt to fix a PR that was flagged as NEEDS_FIX by the review agent."""
    pr_number = pr["number"]
    logger.info("Attempting code fix for PR #%s", pr_number)

    try:
        model = await get_model_for_task("code_fix")
        prompt = await get_prompt("chat")

        messages = [
            {
                "role": "user",
                "content": (
                    f"A PR review identified that PR #{pr_number} needs a code fix.\n\n"
                    f"PR: {pr['title']}\n"
                    f"Author: {pr['author']}\n"
                    f"Branch: {pr.get('head_ref', 'unknown')}\n"
                    f"URL: {pr.get('html_url', '')}\n\n"
                    f"Review findings:\n{review_summary}\n\n"
                    "Your task:\n"
                    "1. Read the changed files using github_get_pr_files\n"
                    "2. Understand what breaking change occurred\n"
                    "3. Read the full file content that needs fixing\n"
                    "4. Create a fix commit on the PR branch\n"
                    "5. Post a comment on the PR explaining what you fixed\n\n"
                    "Only modify files under kubernetes/apps/. "
                    "Use the PR's head branch for the commit, not main."
                ),
            }
        ]

        result = await agent.run(
            system_prompt=prompt,
            messages=messages,
            model=model,
            max_turns=15,
        )

        if result:
            # Save as a code_fix task
            async with async_session() as session:
                conversation = Conversation(
                    title=f"Code Fix: #{pr_number} {pr['title'][:100]}",
                    source="code_fix",
                    status="completed",
                )
                session.add(conversation)
                await session.flush()

                msg = Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content={
                        "text": result.response,
                        "tool_calls": result.tool_calls,
                    },
                )
                session.add(msg)

                task = AgentTask(
                    task_type="code_fix",
                    trigger=f"PR #{pr_number}",
                    status="completed",
                    conversation_id=conversation.id,
                    summary=result.response[:500],
                    actions_taken={
                        "tool_calls": result.tool_calls,
                        "tokens": result.total_tokens,
                    },
                    completed_at=datetime.now(UTC),
                )
                session.add(task)
                await session.commit()

            # Notify about the fix
            from home_ops_agent.agent.tools.ntfy import publish_notification

            try:
                await publish_notification(
                    {
                        "title": f"Code fix pushed for PR #{pr_number}",
                        "message": f"{pr['title']}\n\nWaiting for CI...",
                        "priority": "default",
                        "tags": "wrench",
                        "click_url": pr.get("html_url", ""),
                    }
                )
            except Exception:
                logger.exception("Failed to send code fix notification")

            logger.info("Code fix completed for PR #%s, waiting for CI", pr_number)

            # Wait for CI and merge
            await _wait_for_ci_and_merge(pr_number, pr.get("html_url", ""), pr["title"])

    except Exception:
        logger.exception("Code fix failed for PR #%s", pr_number)


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

    global last_pr_check_at

    while True:
        try:
            await check_prs()
            last_pr_check_at = datetime.now(UTC)
        except Exception:
            logger.exception("PR monitor cycle failed")

        interval = await _get_check_interval()
        await asyncio.sleep(interval)
