"""Git worktree workspace for checkout-based code fixes.

The GitHub Contents API path (``github_create_commit``) can only replace one
whole file per commit, from content the model regenerates by hand. That makes a
real fix — grep the repo, edit three files, validate, commit once — impossible,
which is why non-trivial breaking changes end up being fixed by a human.

This module gives an agent an actual checkout instead: a per-run ``git
worktree`` on the PR's branch, inside which it can read, search, edit and
validate freely. Only one door leads out — :func:`commit_and_push`, exposed as
the ``workspace_commit`` tool — and it applies the *same* guardrails the API
tool applies, except to the whole staged diff rather than a single path:

- the branch must not be in ``PROTECTED_BRANCHES``
- every changed path must sit under ``ALLOWED_COMMIT_PATHS``

Keeping the check in Python (rather than a ``PreToolUse`` hook) means it stays
auditable and unit-testable, and it sees the final diff — not an intercepted
tool call it has to predict the effect of.

Only the ``claude-code`` provider uses this: the Claude Code CLI already has
Read/Grep/Glob/Edit/Bash, so no equivalent tools have to be written for it. The
API backends keep using ``github_create_commit``.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from home_ops_agent.agent.core import ToolDefinition
from home_ops_agent.agent.tools.github import ALLOWED_COMMIT_PATHS, PROTECTED_BRANCHES
from home_ops_agent.config import settings

logger = logging.getLogger(__name__)

GIT_USER_NAME = "home-ops-agent"
GIT_USER_EMAIL = "home-ops-agent[bot]@users.noreply.github.com"

# Serializes clone/fetch against the shared mirror. Worktrees live in their own
# directories and are safe to use concurrently; the mirror is not.
_mirror_lock = asyncio.Lock()


def workspace_root() -> Path:
    """Base directory for the clone and its worktrees.

    Override with ``AGENT_WORKSPACE_DIR`` to point at a mounted volume; the
    default is fine on an ephemeral filesystem since the clone is re-created
    when missing.
    """
    base = os.environ.get("AGENT_WORKSPACE_DIR") or tempfile.gettempdir()
    return Path(base) / "home-ops-workspace"


def _clone_dir() -> Path:
    return workspace_root() / "repo"


def _worktrees_dir() -> Path:
    return workspace_root() / "worktrees"


def _authed_url(token: str) -> str:
    """Clone/push URL carrying the token.

    Passed as a command argument per invocation and never written to
    ``.git/config``, so the agent's own shell cannot read it back out of the
    checkout with ``git remote -v``.
    """
    return f"https://x-access-token:{token}@github.com/{settings.github_repo}.git"


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


async def _git(*args: str, cwd: Path | None = None, token: str = "") -> tuple[int, str, str]:
    """Run a git command, returning ``(returncode, stdout, stderr)``."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    stdout = out.decode("utf-8", "replace")
    stderr = err.decode("utf-8", "replace")
    if proc.returncode != 0:
        logger.warning(
            "git %s failed (%s): %s",
            _redact(args[0] if args else "", token),
            proc.returncode,
            _redact(stderr.strip()[:500], token),
        )
    return proc.returncode or 0, stdout, stderr


async def _ensure_clone(token: str) -> Path:
    """Clone the repo if absent, otherwise fetch. Returns the clone path."""
    clone = _clone_dir()
    async with _mirror_lock:
        if not (clone / ".git").exists():
            if clone.exists():
                shutil.rmtree(clone, ignore_errors=True)
            clone.parent.mkdir(parents=True, exist_ok=True)
            code, _, err = await _git(
                "clone", "--no-tags", _authed_url(token), str(clone), token=token
            )
            if code != 0:
                raise RuntimeError(f"failed to clone {settings.github_repo}: {_redact(err, token)}")
            # Keep the token out of the persisted remote; every network call
            # passes the authenticated URL explicitly instead.
            await _git(
                "remote",
                "set-url",
                "origin",
                f"https://github.com/{settings.github_repo}.git",
                cwd=clone,
            )
        else:
            code, _, err = await _git(
                "fetch",
                "--no-tags",
                "--prune",
                _authed_url(token),
                "+refs/heads/*:refs/remotes/origin/*",
                cwd=clone,
                token=token,
            )
            if code != 0:
                raise RuntimeError(f"failed to fetch {settings.github_repo}: {_redact(err, token)}")
    return clone


@dataclass
class Workspace:
    """A checked-out worktree on a specific branch."""

    path: Path
    branch: str
    token: str


@asynccontextmanager
async def open_workspace(branch: str, token: str) -> AsyncIterator[Workspace]:
    """Check ``branch`` out into a fresh worktree for the duration of the block."""
    if not token:
        raise ValueError("a GitHub token is required to open a workspace")
    if branch in PROTECTED_BRANCHES:
        raise ValueError(f"refusing to open a workspace on protected branch '{branch}'")

    clone = await _ensure_clone(token)
    _worktrees_dir().mkdir(parents=True, exist_ok=True)
    # mkdtemp then remove: `git worktree add` wants to create the directory
    # itself, but we want the unique name allocated atomically.
    slot = Path(tempfile.mkdtemp(prefix="wt-", dir=str(_worktrees_dir())))
    slot.rmdir()

    code, _, err = await _git(
        "worktree", "add", "--force", "-B", branch, str(slot), f"origin/{branch}", cwd=clone
    )
    if code != 0:
        raise RuntimeError(f"failed to check out branch '{branch}': {_redact(err, token)}")

    try:
        yield Workspace(path=slot, branch=branch, token=token)
    finally:
        await _git("worktree", "remove", "--force", str(slot), cwd=clone)
        shutil.rmtree(slot, ignore_errors=True)
        await _git("worktree", "prune", cwd=clone)


def _blocked_paths(paths: list[str]) -> list[str]:
    """Return the staged paths that fall outside ``ALLOWED_COMMIT_PATHS``."""
    return [p for p in paths if not any(p.startswith(prefix) for prefix in ALLOWED_COMMIT_PATHS)]


async def commit_and_push(ws: Workspace, message: str) -> dict:
    """Stage everything, enforce the guardrails on the diff, then commit and push.

    Returns a JSON-serializable dict describing what happened. A guardrail
    violation unstages the change and reports which paths were rejected, so the
    agent can correct course rather than failing the run.
    """
    # Re-check: the branch is fixed at checkout, but this is the gate that
    # matters and it should not depend on an earlier call having run.
    if ws.branch in PROTECTED_BRANCHES:
        return {"status": "blocked", "error": f"Cannot commit to protected branch '{ws.branch}'."}

    code, _, err = await _git("add", "-A", cwd=ws.path, token=ws.token)
    if code != 0:
        return {"status": "failed", "error": f"git add failed: {_redact(err, ws.token)}"}

    # --no-renames so a rename is reported as delete+add: both sides are then
    # checked, instead of a rename smuggling a file out of an allowed path.
    code, out, err = await _git(
        "diff", "--cached", "--name-only", "--no-renames", cwd=ws.path, token=ws.token
    )
    if code != 0:
        return {"status": "failed", "error": f"git diff failed: {_redact(err, ws.token)}"}

    paths = [line.strip() for line in out.splitlines() if line.strip()]
    if not paths:
        return {"status": "no_changes", "message": "Nothing to commit — no files were modified."}

    blocked = _blocked_paths(paths)
    if blocked:
        await _git("reset", cwd=ws.path, token=ws.token)
        allowed = ", ".join(sorted(ALLOWED_COMMIT_PATHS))
        return {
            "status": "blocked",
            "error": (
                f"BLOCKED: {len(blocked)} path(s) outside the allowed prefixes. "
                f"The agent can only modify files under: {allowed}"
            ),
            "blocked_paths": blocked,
        }

    code, _, err = await _git(
        "-c",
        f"user.name={GIT_USER_NAME}",
        "-c",
        f"user.email={GIT_USER_EMAIL}",
        "commit",
        "-m",
        message,
        cwd=ws.path,
        token=ws.token,
    )
    if code != 0:
        return {"status": "failed", "error": f"git commit failed: {_redact(err, ws.token)}"}

    code, _, err = await _git(
        "push",
        _authed_url(ws.token),
        f"HEAD:refs/heads/{ws.branch}",
        cwd=ws.path,
        token=ws.token,
    )
    if code != 0:
        return {"status": "failed", "error": f"git push failed: {_redact(err, ws.token)}"}

    code, sha, _ = await _git("rev-parse", "HEAD", cwd=ws.path, token=ws.token)
    return {
        "status": "ok",
        "branch": ws.branch,
        "sha": sha.strip(),
        "files": paths,
        "message": f"Committed {len(paths)} file(s) and pushed to {ws.branch}.",
    }


def build_workspace_tools(ws: Workspace) -> list[ToolDefinition]:
    """The only write path out of a workspace."""

    async def handler(params: dict) -> dict:
        message = (params.get("message") or "").strip()
        if not message:
            return {"status": "failed", "error": "A commit message is required."}
        return await commit_and_push(ws, message)

    return [
        ToolDefinition(
            name="workspace_commit",
            description=(
                "Commit all changes you have made in the working directory and push them "
                f"to the PR branch '{ws.branch}'. Only files under "
                f"{', '.join(sorted(ALLOWED_COMMIT_PATHS))} may be modified — a commit "
                "touching anything else is rejected and unstaged. Call this once, after "
                "you have made and validated every edit."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message describing the fix.",
                    },
                },
                "required": ["message"],
            },
            handler=handler,
        )
    ]
