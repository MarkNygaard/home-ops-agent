"""Code fix logic — attempts to fix PRs flagged as NEEDS_FIX."""

import logging
from datetime import UTC, datetime

from home_ops_agent.agent.core import Agent
from home_ops_agent.agent.costs import record_usage
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.agent.workspace import WORKSPACE_TASK, maybe_workspace
from home_ops_agent.database import AgentTask, Conversation, Message, async_session
from home_ops_agent.workers import notifications
from home_ops_agent.workers.pr_merge import wait_for_ci_and_merge
from home_ops_agent.workers.pr_monitor import _extract_verdict

logger = logging.getLogger(__name__)


_API_TASK = """Your task:
1. Read the changed files using github_get_pr_files
2. Understand what breaking change occurred
3. Read the full file content that needs fixing
4. Create a fix commit on the PR branch
5. Post a comment on the PR explaining what you fixed

Only modify files under kubernetes/apps/. Use the PR's head branch for the
commit, not main."""


async def attempt_code_fix(pr: dict, review_summary: str, agent: Agent):
    """Attempt to fix a PR that was flagged as NEEDS_FIX by the review agent."""
    pr_number = pr["number"]
    logger.info("Attempting code fix for PR #%s", pr_number)

    try:
        model = await get_model_for_task("code_fix")
        prompt = await get_prompt("code_fix")
        branch = pr.get("head_ref", "unknown")

        async with maybe_workspace(model, branch) as ws:
            task = WORKSPACE_TASK if ws is not None else _API_TASK
            messages = [
                {
                    "role": "user",
                    "content": (
                        f"A PR review identified that PR #{pr_number} needs a code fix.\n\n"
                        f"PR: {pr['title']}\n"
                        f"Author: {pr['author']}\n"
                        f"Branch: {branch}\n"
                        f"URL: {pr.get('html_url', '')}\n\n"
                        f"Review findings:\n{review_summary}\n\n" + task
                    ),
                }
            ]

            result = await agent.run(
                system_prompt=prompt,
                messages=messages,
                model=model,
                # A checkout run explores before it edits (grep, read, validate),
                # so it needs more turns than the single-file API path.
                max_turns=30 if ws is not None else 15,
                workspace=ws,
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

                task_row = AgentTask(
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
                session.add(task_row)
                await session.commit()

            await record_usage(
                model=result.model,
                task_type="code_fix",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )

            # Notify about the fix

            try:
                await notifications.notify(
                    notifications.ROUTINE,
                    {
                        "title": f"Code fix pushed for PR #{pr_number}",
                        "message": f"{pr['title']}\n\nWaiting for CI...",
                        "priority": "default",
                        "tags": "wrench",
                        "click_url": pr.get("html_url", ""),
                    },
                )
            except Exception:
                logger.exception("Failed to send code fix notification")

            logger.info("Code fix completed for PR #%s, waiting for CI", pr_number)

            # Wait for CI and merge
            await wait_for_ci_and_merge(pr_number, pr.get("html_url", ""), pr["title"], agent)

    except Exception:
        logger.exception("Code fix failed for PR #%s", pr_number)
