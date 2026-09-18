"""A record of everything the agent changed.

The conversation views answer "what did this run do". They do not answer "what
has this agent done to my cluster this week", and that is the question you
actually have after leaving it running unattended: every restart, every
reconcile, every commit, every merge, in one list, in order, whatever run it
came from.

Only mutating tools are recorded. Reads are the overwhelming majority of tool
calls and including them would bury the handful of lines that matter -- the
point of this table is that it is short enough to read.

Recording never fails a tool call. A write that succeeded and went unrecorded is
bad; a write that was refused *because the audit log was unavailable* is worse,
and this log is not the safety mechanism. The ones that decide whether a write
happens at all involve no model and no database: ALLOWED_COMMIT_PATHS,
PROTECTED_BRANCHES, PROTECTED_NAMESPACES, the renovate-only merge gate, and the
tools withheld from each agent. This is the receipt, not the lock.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# The mutating surface, and how to say what each one touched. Anything not
# listed here is a read and is not recorded.
#
# The value is the argument names that identify the target, most specific
# first. `github_create_pr` names a branch; `k8s_restart_workload` names a
# workload in a namespace. A tool with no useful arguments still gets a row --
# the tool name alone is the record.
WRITE_TOOLS: dict[str, tuple[str, ...]] = {
    # Cluster state
    "k8s_restart_workload": ("namespace", "name", "kind"),
    "k8s_delete_pod": ("namespace", "name"),
    # Flux
    "flux_reconcile": ("namespace", "name", "kind"),
    "flux_suspend": ("namespace", "name", "kind"),
    "flux_resume": ("namespace", "name", "kind"),
    # Git and GitHub
    "github_create_branch": ("branch", "from_branch"),
    "github_create_commit": ("path", "branch"),
    "github_create_pr": ("head", "title"),
    "github_create_pr_comment": ("pr_number",),
    "github_merge_pr": ("pr_number",),
    "workspace_commit": ("message",),
    "code_fix": ("pr_number", "branch"),
    # Outbound
    "ntfy_publish": ("title",),
}


def is_write(tool: str) -> bool:
    return tool in WRITE_TOOLS


def describe_target(tool: str, args: Any) -> str:
    """A short, human-readable "what did it touch" for one call."""
    if not isinstance(args, dict):
        return ""
    parts = [str(args[k]) for k in WRITE_TOOLS.get(tool, ()) if args.get(k) not in (None, "")]
    return " ".join(parts)[:200]


def classify(result: str) -> tuple[str, str]:
    """(outcome, detail) for a tool's return value.

    `blocked` is separated from `error` deliberately. A guardrail refusing a
    write is the log's most interesting line -- it is the agent trying something
    it is not allowed to do -- and it would be invisible if it were filed next
    to timeouts and typos.
    """
    text = result if isinstance(result, str) else str(result)
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return "ok", text[:200]

    if isinstance(parsed, dict):
        error = parsed.get("error")
        if error:
            detail = str(error)[:200]
            return ("blocked" if "BLOCKED" in str(error).upper() else "error"), detail
        message = parsed.get("message") or parsed.get("status") or ""
        return "ok", str(message)[:200]
    return "ok", text[:200]


def records(tool: str):
    """Mark a tool handler as a write, and record every call to it.

    This sits on the *handler* rather than on the dispatchers on purpose. There
    are three dispatchers -- the Anthropic loop in `core`, the Unix socket for
    pi, and the Claude Code SDK wrapper -- and the workers also call handlers
    like `merge_pr` directly, with no dispatcher at all. Recording per
    dispatcher missed two of those four, including the busiest: every PR review
    runs on the Claude Code backend, so the first version of this log recorded
    nothing at all while looking like it worked.

    The handler is the one place all four meet.
    """

    def decorate(fn):
        @functools.wraps(fn)
        async def wrapper(params, *args, **kwargs):
            try:
                result = await fn(params, *args, **kwargs)
            except Exception as exc:
                # Recorded and re-raised: an attempt that raised is still an
                # attempt, and the caller's error handling is unchanged.
                await record(tool, params, json.dumps({"error": str(exc)}))
                raise
            await record(tool, params, result if isinstance(result, str) else str(result))
            return result

        wrapper.__audit_tool__ = tool
        return wrapper

    return decorate


async def record(
    tool: str,
    args: Any,
    result: str,
    *,
    source: str = "unknown",
    conversation_id: int | None = None,
) -> None:
    """File one write. Swallows every failure by design -- see the module docstring."""
    if not is_write(tool):
        return

    try:
        from sqlalchemy import insert

        from home_ops_agent.database import ToolWrite, async_session

        outcome, detail = classify(result)
        async with async_session() as session:
            await session.execute(
                insert(ToolWrite).values(
                    tool=tool,
                    target=describe_target(tool, args),
                    source=source,
                    outcome=outcome,
                    detail=detail,
                    conversation_id=conversation_id,
                )
            )
            await session.commit()
    except Exception:
        logger.warning("Could not record write of %s", tool, exc_info=True)
