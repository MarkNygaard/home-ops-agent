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
from home_ops_agent.workers import progress
from home_ops_agent.workers import verdict as verdict_mod

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
    """The prefix shown in the task history, so it survives truncation.

    Delegates to the shared parser. It used to have its own precedence --
    safe_to_merge before needs_fix -- which disagreed with the dispatch below
    and filed runs as [SAFE_TO_MERGE] while a code fix was running.
    """
    return verdict_mod.parse(response).label


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
    """Check if we already reviewed this PR at this SHA (DB-backed, survives restarts).

    An unknown SHA is never a match. Without this, an empty string compares equal
    to the empty string stored by an earlier run, so the answer is "yes, at the
    commit I do not know about" -- and the PR is skipped forever, whatever is
    pushed to it. That is exactly what happened while `list_prs` omitted
    `head_sha`: every PR was reviewed once and never again.
    """
    if not head_sha:
        return False
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

    Returns None for an unknown SHA, rather than matching a review of some other
    commit. That mattered more than the skip: this summary is what
    `auto_merge_reviewed_prs` merges on, so a stale SAFE_TO_MERGE from an earlier
    version of a force-pushed PR could approve code nothing had looked at.

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
    if not head_sha:
        return None
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
# Kept importable from here for anything that referenced it.
REFUSAL_MARKERS = verdict_mod.REFUSAL_MARKERS


async def _is_safe_to_auto_merge(pr: dict, summary: str) -> bool:
    """Check if a previously reviewed PR meets auto-merge criteria."""
    # Must be from renovate
    if pr.get("author") != "renovate[bot]":
        return False

    # One reading of the text, shared with the router and the history label. A
    # refusal anywhere in a legacy response still vetoes approval -- that
    # precedence was always right here and is now right everywhere.
    if not verdict_mod.parse(summary).safe_to_merge:
        logger.info("PR #%s not auto-merged: the review did not say it was safe", pr.get("number"))
        return False

    pr_mode = await _get_pr_mode()
    labels = pr.get("labels", [])

    if pr_mode == "auto_merge_all":
        # Fully autonomous: merge anything rated safe, no label restrictions
        return True
    if pr_mode == "auto_merge_minor":
        safe_labels = {"type/patch", "type/digest", "type/minor"}
    else:
        safe_labels = {"type/patch", "type/digest"}
    return any(label in safe_labels for label in labels)


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


async def out_of_scope_paths(pr_number: int) -> list[str]:
    """The PR's changed files that a fix would not be allowed to commit.

    The router asks this before dispatching a fix, using the same function the
    commit guard uses. Previously the two disagreed: a PR touching `talos/` was
    routed to the code fixer, which opened a checkout, read the repository, made
    the edit, and only then had `workspace_commit` reject it -- a wasted model
    run ending in a confusing failure, when the answer was knowable up front
    from the file list.

    A failure to read the file list returns an empty list, so an API blip does
    not silently stop fixes happening. The commit guard still has the last word.
    """
    from home_ops_agent.agent.tools.github import get_pr_files
    from home_ops_agent.agent.workspace import blocked_paths

    try:
        files = json.loads(await get_pr_files({"pr_number": pr_number}))
    except Exception:
        logger.exception("Could not read the file list for PR #%s; not gating on paths", pr_number)
        return []
    if not isinstance(files, list):
        return []
    return blocked_paths([f.get("filename", "") for f in files if isinstance(f, dict)])


async def _route(pr: dict, response: str, agent: Agent, pr_mode: str) -> None:
    """Decide what happens to a PR after its review.

    Routing is on the parsed verdict plus facts the code can check -- the file
    list, the mode -- rather than on which of two overlapping phrases a small
    model happened to write. Component criticality stays a judgement the model
    makes inside the review; it is deliberately not a hard-coded list here,
    because a cert-manager patch and a cert-manager major are not the same risk
    and any such list goes stale.
    """
    from home_ops_agent.workers.pr_fix import attempt_code_fix
    from home_ops_agent.workers.pr_merge import deep_review_pr

    pr_number = pr["number"]
    progress.step("decide", f"PR #{pr_number}")
    verdict = verdict_mod.parse(response)

    if not verdict.structured:
        # Not fatal -- the legacy markers still routed this -- but it means the
        # review ignored its output format, which is worth knowing about.
        logger.warning(
            "PR #%s review had no structured verdict block; fell back to markers", pr_number
        )

    if verdict.safe_to_merge:
        # Merging is handled by auto_merge_reviewed_prs against the same gate.
        return

    if verdict.fixable:
        progress.step("in_scope", f"PR #{pr_number}")
        blocked = await out_of_scope_paths(pr_number)
        if blocked:
            progress.step("notify_scope", f"PR #{pr_number}")
            logger.info(
                "PR #%s is fixable but touches %d path(s) a fix may not commit (%s); "
                "escalating instead",
                pr_number,
                len(blocked),
                ", ".join(blocked[:3]),
            )
        else:
            progress.step("code_fix", f"PR #{pr_number}")
            await attempt_code_fix(pr, response, agent)
            return

    if pr_mode == "auto_merge_all":
        progress.step("deep_review", f"PR #{pr_number}")
        await deep_review_pr(pr, response, agent)


async def check_prs() -> dict:
    """Check all open PRs and review any new/updated ones.

    Returns a summary of what happened. Every early exit here used to be
    invisible from the dashboard -- the manual trigger reported "started"
    whether the cycle reviewed three PRs, found none, or bailed because the
    agent was switched off. The caller surfaces this so a no-op is
    distinguishable from a failure.
    """
    from home_ops_agent.workers.pr_merge import auto_merge_reviewed_prs

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

    # Declared before the merge pass, not after it. This pass merges what the
    # *previous* cycle reviewed, and it was running outside any declared run:
    # its `merge_safe` step lit nothing on the diagram, and every write it made
    # was filed in the audit under "unknown".
    progress.begin("pr_review", f"{len(prs)} open PR(s)")

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

        progress.step("check_pr", f"PR #{pr['number']}")
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
                await _route(pr, result.response, agent, pr_mode)

    progress.finish()
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
