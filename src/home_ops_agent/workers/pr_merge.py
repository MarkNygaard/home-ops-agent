"""PR merge logic — auto-merge, deep review escalation, and CI-gated merge."""

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from home_ops_agent.agent.core import Agent
from home_ops_agent.agent.costs import record_usage
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.database import AgentTask, Conversation, Message, async_session
from home_ops_agent.workers import notifications, progress
from home_ops_agent.workers import verdict as verdict_mod
from home_ops_agent.workers.pr_monitor import (
    MAX_REVIEWS_PER_CYCLE,
    _already_reviewed,
    _get_pr_mode,
    _get_review_summary,
    _is_safe_to_auto_merge,
)

logger = logging.getLogger(__name__)

PASSING_CONCLUSIONS = {"success", "neutral", "skipped"}


def checks_all_passed(checks: list[dict]) -> bool:
    """Return True if all CI checks completed successfully."""
    if not checks:
        return False
    return all(c.get("status") == "completed" for c in checks) and all(
        c.get("conclusion") in PASSING_CONCLUSIONS for c in checks
    )


def is_approved_by_deep_review(response: str) -> bool:
    """Return True if a deep review response indicates approval.

    Through the shared parser, so a review ending "SAFE_TO_MERGE: no" is not
    read as approval merely because those words appear in it.
    """
    return verdict_mod.parse(response).safe_to_merge


async def auto_merge_reviewed_prs(prs: list[dict], agent: Agent):
    """Try to auto-merge already-reviewed PRs that are safe to merge.

    This handles the case where PRs were reviewed in comment-only mode
    and the user later switches to auto-merge mode.
    """
    from home_ops_agent.agent.tools.github import merge_pr

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
            # The guard against re-escalating looked for "deep_review", but
            # deep_review_pr stamps its summary "[Deep Review]" -- a space, not
            # an underscore -- so it never matched. It went unnoticed only
            # because the gate was reading the shallow review and never got
            # here; fixing that ordering would have turned this into an Opus
            # deep review of the same PR on every cycle, forever.
            already_deep = "deep_review" in summary_lower or "deep review" in summary_lower
            if (
                pr_mode == "auto_merge_all"
                and ("needs_review" in summary_lower or "needs review" in summary_lower)
                and not already_deep
            ):
                logger.info(
                    "Escalating PR #%s to deep review (auto_merge_all mode)",
                    pr_number,
                )
                await deep_review_pr(pr, summary, agent)
                merged_count += 1  # Count towards cycle limit
                # Delay between deep reviews to avoid API rate limits (Opus)
                await asyncio.sleep(60)
            else:
                # Previously this was a silent skip. Now that the model no
                # longer merges for itself, this branch is the only thing
                # standing between a reviewed PR and a merge, so a PR that
                # quietly stops merging must say so somewhere.
                logger.info(
                    "PR #%s reviewed but not merged: the review does not state "
                    "SAFE_TO_MERGE, or the PR does not meet this mode's criteria",
                    pr_number,
                )
            continue

        # Merge it
        progress.step("merge_safe", f"PR #{pr_number}")
        logger.info("Auto-merging PR #%s: %s", pr_number, pr["title"])
        result = await merge_pr({"pr_number": pr_number})
        merge_result = json.loads(result)

        if merge_result.get("status") == "merged":
            merged_count += 1
            logger.info("Successfully merged PR #%s", pr_number)

            # Notify first — DB errors should not prevent notification
            try:
                await notifications.notify(
                    notifications.OUTCOME,
                    {
                        "title": f"Auto-merged PR #{pr_number}",
                        "message": pr["title"],
                        "priority": "default",
                        "tags": "merged",
                        "click_url": pr.get("html_url", ""),
                    },
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
            # Previously log-only: the agent decided to merge, could not, and
            # said nothing. Successes were announced twice while the one
            # outcome actually needing a human was silent.
            await notifications.notify(
                notifications.FAILURE,
                {
                    "title": f"Auto-merge failed: PR #{pr_number}",
                    "message": (f"{pr['title']}\n\n{merge_result.get('message', 'unknown error')}")[
                        :400
                    ],
                    "priority": "high",
                    "tags": "x",
                    "click_url": pr.get("html_url", ""),
                },
            )
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
                        await notifications.notify(
                            notifications.OUTCOME,
                            {
                                "title": f"Auto-merged PR #{pr_number} (retry)",
                                "message": pr["title"],
                                "priority": "default",
                                "tags": "white_check_mark",
                                "click_url": pr.get("html_url", ""),
                            },
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
                    "4. Determine if this is actually safe to merge\n"
                    "5. If it is NOT safe, decide whether you know the concrete "
                    "change that would make it safe — a manifest edit under "
                    "kubernetes/apps/. If you do, say so and describe it "
                    "precisely: a code fix will be attempted from your review, "
                    "and your findings are what it works from.\n\n"
                    "Post your review as a comment on the PR, and end it with "
                    "exactly these two lines:\n"
                    "SAFE_TO_MERGE: yes|no\n"
                    "FIXABLE: yes|no\n\n"
                    "FIXABLE means you know the specific change and it is "
                    "confined to kubernetes/apps/. Answer no when the fix needs "
                    "a decision that is the operator's to make — then it waits "
                    "for them, which is the right outcome."
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
                        f"[Deep Review] {verdict_mod.parse_result(result).label}"
                        f"{result.response[:450]}"
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

            await record_usage(
                model=result.model,
                task_type="deep_review",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )

            # Notify

            # From the comment it posted, not its closing summary: on PR #1072
            # the comment ended SAFE_TO_MERGE: yes and the summary ended
            # "I disagree with the `NEEDS_REVIEW` flag", so reading the summary
            # turned an approval into a notification.
            approved = verdict_mod.parse_result(result).safe_to_merge

            if approved:
                # Auto-merge after Opus approval
                from home_ops_agent.agent.tools.github import merge_pr

                logger.info("Opus approved PR #%s, auto-merging", pr_number)
                progress.step("merge_after_deep", f"PR #{pr_number}")
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
                # The escalation that did not exist. Opus has just done the
                # research -- release notes, upstream changelog, the diff -- and
                # its findings are strictly better input to a fix than the first
                # reviewer's. Before this, a deep review that knew exactly what
                # was wrong wrote it on the PR and stopped, and nothing ever
                # picked it up: the next cycle skips any PR whose head SHA has
                # already been reviewed, so the comment was never read again.
                deep_verdict = verdict_mod.parse_result(result)
                if deep_verdict.fixable:
                    from home_ops_agent.workers.pr_monitor import out_of_scope_paths

                    blocked = await out_of_scope_paths(pr_number)
                    if blocked:
                        logger.info(
                            "PR #%s judged fixable but touches %d path(s) a fix may not "
                            "commit (%s); leaving it for the operator",
                            pr_number,
                            len(blocked),
                            ", ".join(blocked[:3]),
                        )
                    else:
                        from home_ops_agent.workers.pr_fix import attempt_code_fix

                        logger.info("Deep review judged PR #%s fixable; attempting", pr_number)
                        await attempt_code_fix(pr, result.response, agent)
                        return

                progress.step("notify_deep", f"PR #{pr_number}")
                title = f"Deep review: PR #{pr_number} needs attention"
                priority = "high"
                tags = "warning"

            try:
                await notifications.notify(
                    notifications.OUTCOME if priority != "high" else notifications.ATTENTION,
                    {
                        "title": title,
                        "message": f"{pr['title']}\n\n{result.response[:200]}",
                        "priority": priority,
                        "tags": tags,
                        "click_url": pr.get("html_url", ""),
                    },
                )
            except Exception:
                logger.exception(
                    "Failed to send deep review notification for PR #%s",
                    pr_number,
                )

            logger.info("Deep review completed for PR #%s", pr_number)

    except Exception:
        logger.exception("Deep review failed for PR #%s", pr_number)


async def review_fixed_pr(pr_number: int, agent: Agent) -> tuple[bool, str]:
    """Re-review a PR after a fix was pushed. Returns (approved, summary).

    Nothing used to look at a fix before it merged. The fix changes the head
    SHA, and ``wait_for_ci_and_merge`` merges within five minutes -- long before
    the next monitor cycle, which is the only thing that would have reviewed the
    new SHA. So the sole gate between an agent's edit and `main` was Flux Local,
    which proves the manifests *render*, not that the change is *right*. A
    semantically wrong but perfectly valid manifest merged unseen.

    That was tolerable while the fix path almost never fired. Letting deep
    review escalate into it is designed to make it fire more, and on the harder
    changes, so the cheap second opinion earns its place. It runs on the
    pr_review model, not the deep review one -- this is a check on a known,
    described change, not a fresh investigation.
    """
    from home_ops_agent.agent.tools.github import get_pr

    try:
        pr = json.loads(await get_pr({"pr_number": pr_number}))
    except Exception:
        logger.exception("Could not re-read PR #%s for review", pr_number)
        return False, "could not re-read the PR"

    model = await get_model_for_task("pr_review")
    result = await agent.run(
        system_prompt=await get_prompt("pr_review"),
        messages=[
            {
                "role": "user",
                "content": (
                    "An automated code fix has just been pushed to this PR. Review the "
                    "PR as it now stands, paying attention to whether the fix is correct "
                    "and complete rather than merely valid YAML.\n\n"
                    f"PR #{pr_number}: {pr.get('title')}\n"
                    f"Branch: {pr.get('head_ref')}\n"
                    f"URL: {pr.get('html_url', '')}\n\n"
                    "Do not post a comment. End your reply with exactly these two lines:\n"
                    "SAFE_TO_MERGE: yes|no\n"
                    "FIXABLE: yes|no"
                ),
            }
        ],
        model=model,
        max_turns=10,
    )
    if result is None:
        return False, "the re-review produced no result"

    await record_usage(
        model=result.model,
        task_type="pr_review",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    approved = verdict_mod.parse_result(result).safe_to_merge
    logger.info(
        "Re-review of fixed PR #%s: %s", pr_number, "approved" if approved else "not approved"
    )
    return approved, result.response


async def wait_for_ci_and_merge(
    pr_number: int, html_url: str, title: str, agent: Agent | None = None
):
    """Wait for CI to pass on a PR, then merge it. Notify on success or failure.

    When ``agent`` is given, CI passing is necessary but not sufficient: the
    fixed PR is re-reviewed and merged only if that review approves it.
    """
    from home_ops_agent.agent.tools.github import get_check_runs, get_pr, merge_pr

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

            if checks_all_passed(checks):
                # CI proves the manifests render. It does not prove the fix is
                # right, so a second opinion stands between the edit and main.
                if agent is not None:
                    progress.step("re_review", f"PR #{pr_number}")
                    approved, summary = await review_fixed_pr(pr_number, agent)
                    if not approved:
                        progress.step("notify_rereview", f"PR #{pr_number}")
                        logger.info(
                            "Fixed PR #%s passed CI but the re-review declined it", pr_number
                        )
                        try:
                            await notifications.notify(
                                notifications.ATTENTION,
                                {
                                    "title": f"Code fix on PR #{pr_number} needs you",
                                    "message": (
                                        f"{title}\n\nCI passed, but the re-review did not "
                                        f"approve the fix:\n\n{summary[:300]}"
                                    ),
                                    "priority": "high",
                                    "tags": "warning",
                                    "click_url": html_url,
                                },
                            )
                        except Exception:
                            logger.exception("Failed to notify about PR #%s", pr_number)
                        return

                # Merge it
                progress.step("merge_after_fix", f"PR #{pr_number}")
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
                        await notifications.notify(
                            notifications.OUTCOME,
                            {
                                "title": f"Code fix merged: PR #{pr_number}",
                                "message": title,
                                "priority": "default",
                                "tags": "white_check_mark",
                                "click_url": html_url,
                            },
                        )
                    except Exception:
                        logger.exception("Failed to send merge notification")
                    return
                else:
                    await notifications.notify(
                        notifications.FAILURE,
                        {
                            "title": f"Code fix merge failed: PR #{pr_number}",
                            "message": (
                                f"{title}\n\nCI passed but the merge failed: "
                                f"{merge_result.get('message', 'unknown error')}"
                            )[:400],
                            "priority": "high",
                            "tags": "x",
                            "click_url": html_url,
                        },
                    )
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
                    await notifications.notify(
                        notifications.FAILURE,
                        {
                            "title": f"Code fix CI failed: PR #{pr_number}",
                            "message": (
                                f"{title}\n\nCI checks failed after code fix. Manual review needed."
                            ),
                            "priority": "high",
                            "tags": "x",
                            "click_url": html_url,
                        },
                    )
                except Exception:
                    logger.exception("Failed to send CI failure notification")
                return

        except Exception:
            logger.exception("Error checking CI for PR #%s, attempt %d", pr_number, attempt + 1)

    # Timed out waiting for CI
    logger.warning("Timed out waiting for CI on PR #%s", pr_number)
    try:
        await notifications.notify(
            notifications.ATTENTION,
            {
                "title": f"Code fix: CI timeout on PR #{pr_number}",
                "message": f"{title}\n\nCI did not complete within 5 minutes.",
                "priority": "default",
                "tags": "clock",
                "click_url": html_url,
            },
        )
    except Exception:
        logger.exception("Failed to send timeout notification")
