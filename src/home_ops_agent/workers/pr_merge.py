"""PR merge logic — auto-merge, deep review escalation, and CI-gated merge."""

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from home_ops_agent.agent.core import Agent
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.database import AgentTask, Conversation, Message, async_session
from home_ops_agent.workers.pr_monitor import (
    MAX_REVIEWS_PER_CYCLE,
    _already_reviewed,
    _extract_verdict,
    _get_pr_mode,
    _get_review_summary,
    _is_safe_to_auto_merge,
)

logger = logging.getLogger(__name__)


async def auto_merge_reviewed_prs(prs: list[dict], agent: Agent):
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
            # In auto_merge_all mode, escalate NEEDS_REVIEW to deep review
            pr_mode = await _get_pr_mode()
            summary_lower = summary.lower()
            if (
                pr_mode == "auto_merge_all"
                and ("needs_review" in summary_lower or "needs review" in summary_lower)
                and "deep_review" not in summary_lower
            ):
                logger.info(
                    "Escalating PR #%s to deep review (auto_merge_all mode)",
                    pr_number,
                )
                await deep_review_pr(pr, summary, agent)
                merged_count += 1  # Count towards cycle limit
                # Delay between deep reviews to avoid API rate limits (Opus)
                await asyncio.sleep(60)
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


async def deep_review_pr(pr: dict, initial_review: str, agent: Agent):
    """Escalate a NEEDS_REVIEW PR to Opus for a deep review.

    Used in auto_merge_all mode when the initial Haiku/Sonnet review
    flags a critical component. Opus does a thorough check and either
    approves (SAFE_TO_MERGE) or confirms NEEDS_REVIEW.
    """
    pr_number = pr["number"]

    # Check if deep review was already done for this PR
    async with async_session() as session:
        existing = await session.execute(
            select(Conversation).where(
                Conversation.source == "pr_deep_review",
                Conversation.title.contains(f"#{pr_number}"),
            )
        )
        existing_review = existing.scalars().first()
        if existing_review:
            # Deep review exists — but did the merge succeed?
            # Check the associated task summary for SAFE_TO_MERGE
            task_result = await session.execute(
                select(AgentTask).where(
                    AgentTask.conversation_id == existing_review.id,
                )
            )
            task = task_result.scalars().first()
            if task and "safe_to_merge" in (task.summary or "").lower():
                # Approved but PR still open — try to merge
                from home_ops_agent.agent.tools.github import merge_pr
                from home_ops_agent.agent.tools.ntfy import publish_notification

                logger.info(
                    "Deep review approved PR #%s previously, retrying merge",
                    pr_number,
                )
                merge_result_str = await merge_pr({"pr_number": pr_number})
                merge_result = json.loads(merge_result_str)
                if merge_result.get("status") == "merged":
                    try:
                        merge_task = AgentTask(
                            task_type="pr_merge",
                            trigger=f"PR #{pr_number}",
                            status="completed",
                            summary=f"Auto-merged (deep review retry): {pr['title']}",
                            actions_taken={
                                "action": "merge_after_deep_review",
                                "head_sha": pr.get("head_sha", ""),
                                "merge_sha": merge_result.get("sha"),
                            },
                            completed_at=datetime.now(UTC),
                        )
                        session.add(merge_task)
                        await session.commit()
                    except Exception:
                        logger.exception(
                            "Failed to save merge task for PR #%s",
                            pr_number,
                        )
                    try:
                        await publish_notification(
                            {
                                "title": f"Auto-merged PR #{pr_number} (retry)",
                                "message": pr["title"],
                                "priority": "default",
                                "tags": "white_check_mark",
                                "click_url": pr.get("html_url", ""),
                            }
                        )
                    except Exception:
                        pass
                return
            logger.info("Deep review already done for PR #%s, skipping", pr_number)
            return

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
                    summary=(
                        f"[Deep Review] {_extract_verdict(result.response)}{result.response[:450]}"
                    ),
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
            approved = "safe_to_merge" in response_lower or "safe to merge" in response_lower

            if approved:
                # Auto-merge after Opus approval
                from home_ops_agent.agent.tools.github import merge_pr

                logger.info("Opus approved PR #%s, auto-merging", pr_number)
                merge_result_str = await merge_pr({"pr_number": pr_number})
                merge_result = json.loads(merge_result_str)

                if merge_result.get("status") == "merged":
                    title = f"Deep review APPROVED and MERGED PR #{pr_number}"
                    tags = "white_check_mark"

                    # Save merge task to DB so it appears in history
                    try:
                        async with async_session() as merge_session:
                            merge_task = AgentTask(
                                task_type="pr_merge",
                                trigger=f"PR #{pr_number}",
                                status="completed",
                                summary=f"Auto-merged (deep review): {pr['title']}",
                                actions_taken={
                                    "action": "merge_after_deep_review",
                                    "head_sha": pr.get("head_sha", ""),
                                    "merge_sha": merge_result.get("sha"),
                                },
                                completed_at=datetime.now(UTC),
                            )
                            merge_session.add(merge_task)
                            await merge_session.commit()
                    except Exception:
                        logger.exception(
                            "Failed to save merge task for PR #%s",
                            pr_number,
                        )
                else:
                    title = f"Deep review APPROVED PR #{pr_number} (merge failed)"
                    tags = "warning"
                priority = "default"
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


async def wait_for_ci_and_merge(pr_number: int, html_url: str, title: str):
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
