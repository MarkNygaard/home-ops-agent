"""Periodic PR monitor — checks open PRs and triggers agent review."""

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from home_ops_agent.agent.core import Agent, AgentResult
from home_ops_agent.agent.costs import record_usage
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.agent.skills import registry
from home_ops_agent.auth.credentials import build_credentials
from home_ops_agent.config import settings
from home_ops_agent.database import AgentTask, Conversation, Message, Setting, async_session

logger = logging.getLogger(__name__)

# Maximum number of PRs to review per cycle (rate limit)
MAX_REVIEWS_PER_CYCLE = 3

# Tools deliberately withheld from the PR agent, so that merging and telling
# you about it stay in code rather than in a model's improvisation.
#
# The review prompt sanctions auto-merging but never asks for a notification,
# so every notification the model sent was unprompted. That is why four
# consecutive merges arrived under four different title formats, and why one
# leaked the tail of a malformed tool call into the body.
#
# Nothing is lost by withholding them. auto_merge_reviewed_prs, deep_review_pr
# and attempt_code_fix each import merge_pr and notifications.notify directly
# rather than going through the agent, so the merge still happens and the
# notification is the code-built one. What changes is that the decision runs
# through _is_safe_to_auto_merge -- a gate that can be read and tested --
# instead of a model re-deriving the rules from prose on every run.
WITHHELD_FROM_PR_AGENT = frozenset({"github_merge_pr", "ntfy_publish"})

# Track last check time for the status API
last_pr_check_at: datetime | None = None


def _extract_verdict(response: str) -> str:
    """Extract the PR review verdict from the agent's response.

    Returns a prefix like '[SAFE_TO_MERGE]', '[NEEDS_REVIEW]', '[NEEDS_FIX]'
    to prepend to the summary so it's never lost to truncation.
    """
    lower = response.lower()
    if "safe_to_merge" in lower or "safe to merge" in lower:
        return "[SAFE_TO_MERGE] "
    if "needs_fix" in lower:
        return "[NEEDS_FIX] "
    if "needs_review" in lower or "needs review" in lower:
        return "[NEEDS_REVIEW] "
    return ""


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


async def _get_review_summary(pr_number: int, head_sha: str) -> str | None:
    """The most recent review for this PR at this SHA.

    Newest first, and the ordering is the point. A PR can have several reviews
    stored against one SHA -- an initial review and the deep review it was
    escalated to -- and the later one supersedes the earlier by definition,
    because escalating exists to get a second opinion.

    Without an explicit order this returned whichever row the database happened
    to yield first, which was the oldest. PR #926 was auto-merged on a shallow
    review reading SAFE_TO_MERGE while the Opus deep review two minutes later
    said NEEDS_REVIEW. The escalation ran, cost its tokens, reached the right
    answer, and was then ignored.
    """
    async with async_session() as session:
        result = await session.execute(
            select(AgentTask)
            .where(
                AgentTask.task_type == "pr_review",
                AgentTask.trigger == f"PR #{pr_number}",
                AgentTask.status == "completed",
            )
            .order_by(AgentTask.created_at.desc(), AgentTask.id.desc())
        )
        tasks = result.scalars().all()
        for task in tasks:
            if task.actions_taken and task.actions_taken.get("head_sha") == head_sha:
                return task.summary
        return None


# A review that refuses cannot also approve. The stored verdict is a prefix
# like "[SAFE_TO_MERGE]" or "[NEEDS_REVIEW]", but the gate greps the whole
# summary, so a body discussing the auto-merge policy can put an approving
# phrase in a review that plainly refuses -- and PR #926's did exactly that,
# carrying a SAFE_TO_MERGE prefix above "Risk Level: HIGH" and "Auto-Merge
# Status: Cannot auto-merge". When both appear, refusal wins.
REFUSAL_MARKERS = (
    "needs_review",
    "needs review",
    "needs_fix",
    "needs fix",
    "cannot auto-merge",
    "do not merge",
)


async def _is_safe_to_auto_merge(pr: dict, summary: str) -> bool:
    """Check if a previously reviewed PR meets auto-merge criteria."""
    summary_lower = summary.lower()

    # Must be from renovate
    if pr.get("author") != "renovate[bot]":
        return False

    # An explicit refusal disqualifies regardless of anything else in the text.
    # Checked before the approval markers so a self-contradicting review is
    # never merged on the strength of the half that agrees with us.
    for marker in REFUSAL_MARKERS:
        if marker in summary_lower:
            logger.info(
                "PR #%s not auto-merged: the review says %r",
                pr.get("number"),
                marker,
            )
            return False

    pr_mode = await _get_pr_mode()
    labels = pr.get("labels", [])

    if pr_mode == "auto_merge_all":
        # Fully autonomous: merge anything rated safe, no label restrictions
        if "safe_to_merge" not in summary_lower and "safe to merge" not in summary_lower:
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


async def _review_pr(pr: dict, agent: Agent) -> AgentResult | None:
    """Run the agent to review a single PR."""
    from home_ops_agent.agent.tools.github import pr_review_comment_exists

    pr_number = pr["number"]
    head_sha = pr.get("head_sha", "")

    if await _already_reviewed(pr_number, head_sha):
        logger.debug("PR #%s already reviewed at SHA %s (DB), skipping", pr_number, head_sha[:8])
        return None

    # Belt-and-suspenders: even if the DB lost track, if a previous run posted
    # an agent review comment for this exact SHA, skip. Prevents the redundant-
    # review comment storm seen on PR #294 where 5 reviews stacked up on the
    # same SHA across worker cycles.
    if head_sha and await pr_review_comment_exists(pr_number, head_sha):
        logger.info(
            "PR #%s has an existing agent review comment at SHA %s, skipping",
            pr_number,
            head_sha[:8],
        )
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


def _summarise(text: str, limit: int = 400) -> str:
    """Trim a review to notification length without cutting mid-word.

    A hard slice produced endings like "not a tooling b", which reads as a
    broken notification rather than a shortened one. Prefer the last paragraph
    break, then sentence, then word, so the text ends somewhere deliberate.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    for separator in ("\n\n", ". ", "\n", " "):
        cut = head.rfind(separator)
        if cut > limit // 2:
            return head[:cut].rstrip(" .,;:-") + " [...]"
    return head.rstrip() + " [...]"


async def _notify_review(pr: dict, result: AgentResult, pr_mode: str = "comment_only"):
    """Send ntfy notification about a completed PR review.

    The *kind* matters as much as the text. A SAFE_TO_MERGE verdict in an
    auto-merge mode is an intermediate step -- the merge itself will report the
    outcome a cycle later -- so it is ROUTINE and suppressed at the default
    notification level. In comment_only mode nothing else will fire, so the same
    verdict is the outcome.
    """
    from home_ops_agent.workers import notifications

    # Determine risk level from the response
    response_lower = result.response.lower()
    if "needs_review" in response_lower or "high risk" in response_lower:
        priority = "high"
        tag = "warning"
        title = f"PR #{pr['number']} needs your review"
        # Same reasoning as the SAFE_TO_MERGE branch below, which this used to
        # miss. In auto_merge_all a NEEDS_REVIEW verdict is not a verdict yet:
        # check_prs escalates it to deep review immediately afterwards, and
        # that reports its own conclusion. Sending ATTENTION here produced two
        # pushes about the same PR a couple of minutes apart, the first of them
        # premature -- and ATTENTION is exactly the class no notify_level can
        # filter, so "outcomes only" could not save the user from it.
        kind = notifications.ROUTINE if pr_mode == "auto_merge_all" else notifications.ATTENTION
    elif "safe_to_merge" in response_lower:
        priority = "default"
        tag = "white_check_mark"
        title = f"PR #{pr['number']} reviewed - safe to merge"
        kind = notifications.ROUTINE if pr_mode != "comment_only" else notifications.OUTCOME
    else:
        priority = "default"
        tag = "mag"
        title = f"PR #{pr['number']} reviewed"
        # NEEDS_FIX starts a code-fix chain that reports its own outcome.
        kind = notifications.ROUTINE if pr_mode != "comment_only" else notifications.OUTCOME

    summary = _summarise(result.response)

    await notifications.notify(
        kind,
        {
            "title": title,
            "message": f"{pr['title']}\n\n{summary}",
            "priority": priority,
            "tags": tag,
            "click_url": pr.get("html_url", ""),
        },
    )


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
            summary=_extract_verdict(result.response) + result.response[:500],
            actions_taken={
                "tool_calls": result.tool_calls,
                "tokens": result.total_tokens,
                "head_sha": pr.get("head_sha", ""),
            },
            completed_at=datetime.now(UTC),
        )
        session.add(task)
        await session.commit()


async def check_prs() -> dict:
    """Check all open PRs and review any new/updated ones.

    Returns a summary of what happened. Every early exit here used to be
    invisible from the dashboard -- the manual trigger reported "started"
    whether the cycle reviewed three PRs, found none, or bailed because the
    agent was switched off. The caller surfaces this so a no-op is
    distinguishable from a failure.
    """
    from home_ops_agent.workers.pr_fix import attempt_code_fix
    from home_ops_agent.workers.pr_merge import auto_merge_reviewed_prs, deep_review_pr

    if not await _is_enabled():
        logger.info("Agent is disabled, skipping PR review")
        return {"status": "disabled"}

    credentials = await build_credentials()
    if not credentials.has_any():
        logger.warning("No model credentials configured, skipping PR review")
        return {"status": "no_credentials"}

    agent = Agent(credentials)
    skill_tools = await registry.get_all_enabled_tools()
    agent.register_tools([tool for tool in skill_tools if tool.name not in WITHHELD_FROM_PR_AGENT])

    # List open PRs via direct API call (not through agent)
    from home_ops_agent.agent.tools.github import list_prs

    prs_json = await list_prs({"state": "open"})
    prs = json.loads(prs_json)

    if not prs:
        logger.info("No open PRs to review")
        return {"status": "no_open_prs", "open_prs": 0}

    logger.info("Found %d open PRs to check", len(prs))

    # Auto-merge previously reviewed PRs if in auto-merge mode
    pr_mode = await _get_pr_mode()
    if pr_mode in ("auto_merge", "auto_merge_minor", "auto_merge_all"):
        await auto_merge_reviewed_prs(prs, agent)

    reviewed_count = 0
    failed_count = 0
    rate_limited = False
    for pr in prs:
        if reviewed_count >= MAX_REVIEWS_PER_CYCLE:
            rate_limited = True
            logger.info(
                "Rate limit reached (%d reviews), remaining PRs next cycle",
                MAX_REVIEWS_PER_CYCLE,
            )
            break

        result = await _review_pr(pr, agent)
        if result is None:
            # _review_pr swallows its own exceptions, so a model without
            # credentials or a failing tool looks the same as "nothing to do"
            # unless it is counted here.
            failed_count += 1
        if result:
            await _save_task(pr, result)
            await record_usage(
                model=result.model,
                task_type="pr_review",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            await _notify_review(pr, result, pr_mode)
            reviewed_count += 1
            logger.info(
                "Reviewed PR #%s: %s",
                pr["number"],
                result.response[:100],
            )

            # Post-review actions depend on the current PR mode
            if pr_mode != "comment_only":
                response_lower = result.response.lower()

                # If review says NEEDS_FIX, attempt a code fix
                if "needs_fix" in response_lower:
                    await attempt_code_fix(pr, result.response, agent)

                # In auto_merge_all mode, escalate NEEDS_REVIEW to Opus
                elif "needs_review" in response_lower and pr_mode == "auto_merge_all":
                    await deep_review_pr(pr, result.response, agent)

    return {
        "status": "completed",
        "open_prs": len(prs),
        "reviewed": reviewed_count,
        "failed": failed_count,
        "rate_limited": rate_limited,
        "pr_mode": pr_mode,
    }


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


def _record_cycle_result(result: dict | None) -> None:
    """Publish a cycle summary for the status endpoint."""
    from datetime import datetime as _dt

    from home_ops_agent.api import status as status_api

    status_api._pr_check_last_result = {
        **(result or {"status": "completed"}),
        "at": _dt.now(UTC).isoformat(),
    }


async def run_pr_monitor():
    """Background task: periodically check PRs."""
    logger.info(
        "PR monitor started (default interval: %ds)",
        settings.pr_check_interval_seconds,
    )

    global last_pr_check_at

    while True:
        try:
            # The scheduled cycle is the one that runs all day; recording its
            # outcome is what makes the dashboard reflect reality rather than
            # only the last time someone pressed Run now.
            result = await check_prs()
            _record_cycle_result(result)
            last_pr_check_at = datetime.now(UTC)
        except Exception:
            logger.exception("PR monitor cycle failed")

        interval = await _get_check_interval()
        await asyncio.sleep(interval)
