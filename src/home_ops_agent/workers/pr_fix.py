"""Code fix logic — attempts to fix PRs flagged as NEEDS_FIX."""

import logging
from datetime import UTC, datetime

from home_ops_agent.agent.core import Agent
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.database import AgentTask, Conversation, Message, async_session
from home_ops_agent.workers.pr_merge import wait_for_ci_and_merge
from home_ops_agent.workers.pr_monitor import _extract_verdict

logger = logging.getLogger(__name__)


async def attempt_code_fix(pr: dict, review_summary: str, agent: Agent):
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
                    summary=_extract_verdict(result.response) + result.response[:500],
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
            await wait_for_ci_and_merge(pr_number, pr.get("html_url", ""), pr["title"])

    except Exception:
        logger.exception("Code fix failed for PR #%s", pr_number)
