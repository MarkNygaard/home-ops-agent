"""GitHub API tools for the agent."""

import json
import logging
from typing import Any

import httpx

from home_ops_agent.agent.core import ToolDefinition
from home_ops_agent.config import settings

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# Hard safety guardrails — these cannot be overridden by the agent
PROTECTED_BRANCHES = {"main", "master"}
ALLOWED_COMMIT_PATHS = {"kubernetes/apps/"}  # Agent can only modify files under these paths


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def list_prs(params: dict) -> str:
    """List open pull requests."""
    state = params.get("state", "open")
    author = params.get("author")

    async with httpx.AsyncClient() as client:
        url = f"{GITHUB_API}/repos/{settings.github_repo}/pulls"
        query: dict[str, Any] = {"state": state, "per_page": 30}
        resp = await client.get(url, headers=_headers(), params=query)
        resp.raise_for_status()

        prs = resp.json()
        if author:
            prs = [pr for pr in prs if pr["user"]["login"] == author]

        result = []
        for pr in prs:
            result.append(
                {
                    "number": pr["number"],
                    "title": pr["title"],
                    "author": pr["user"]["login"],
                    "labels": [lbl["name"] for lbl in pr["labels"]],
                    "created_at": pr["created_at"],
                    "updated_at": pr["updated_at"],
                    "mergeable_state": pr.get("mergeable_state"),
                    "draft": pr["draft"],
                    "html_url": pr["html_url"],
                }
            )
        return json.dumps(result)


async def get_pr(params: dict) -> str:
    """Get detailed info about a specific PR including diff stats."""
    pr_number = params["pr_number"]

    async with httpx.AsyncClient() as client:
        url = f"{GITHUB_API}/repos/{settings.github_repo}/pulls/{pr_number}"
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        pr = resp.json()

        return json.dumps(
            {
                "number": pr["number"],
                "title": pr["title"],
                "body": pr["body"] or "",
                "author": pr["user"]["login"],
                "labels": [lbl["name"] for lbl in pr["labels"]],
                "state": pr["state"],
                "mergeable": pr.get("mergeable"),
                "mergeable_state": pr.get("mergeable_state"),
                "additions": pr["additions"],
                "deletions": pr["deletions"],
                "changed_files": pr["changed_files"],
                "head_ref": pr["head"]["ref"],
                "head_sha": pr["head"]["sha"],
                "base_ref": pr["base"]["ref"],
                "html_url": pr["html_url"],
            }
        )


async def get_pr_files(params: dict) -> str:
    """Get the list of changed files in a PR with their diffs."""
    pr_number = params["pr_number"]

    async with httpx.AsyncClient() as client:
        url = f"{GITHUB_API}/repos/{settings.github_repo}/pulls/{pr_number}/files"
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()

        files = []
        for f in resp.json():
            files.append(
                {
                    "filename": f["filename"],
                    "status": f["status"],
                    "additions": f["additions"],
                    "deletions": f["deletions"],
                    "patch": f.get("patch", "")[:2000],  # Truncate large diffs
                }
            )
        return json.dumps(files)


async def get_check_runs(params: dict) -> str:
    """Get CI check status for a specific commit/PR."""
    ref = params["ref"]  # SHA or branch name

    async with httpx.AsyncClient() as client:
        url = f"{GITHUB_API}/repos/{settings.github_repo}/commits/{ref}/check-runs"
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()

        checks = []
        for check in resp.json().get("check_runs", []):
            checks.append(
                {
                    "name": check["name"],
                    "status": check["status"],
                    "conclusion": check["conclusion"],
                    "started_at": check.get("started_at"),
                    "completed_at": check.get("completed_at"),
                }
            )
        return json.dumps(checks)


async def create_pr_comment(params: dict) -> str:
    """Post a comment on a PR."""
    pr_number = params["pr_number"]
    body = params["body"]

    async with httpx.AsyncClient() as client:
        url = f"{GITHUB_API}/repos/{settings.github_repo}/issues/{pr_number}/comments"
        resp = await client.post(url, headers=_headers(), json={"body": body})
        resp.raise_for_status()

        return json.dumps({"status": "ok", "comment_id": resp.json()["id"]})


async def merge_pr(params: dict) -> str:
    """Merge a PR (squash merge)."""
    pr_number = params["pr_number"]
    commit_title = params.get("commit_title", "")

    # Safety: log every merge attempt
    logger.warning("Merge requested for PR #%s", pr_number)

    async with httpx.AsyncClient() as client:
        url = f"{GITHUB_API}/repos/{settings.github_repo}/pulls/{pr_number}/merge"
        payload: dict[str, Any] = {"merge_method": "squash"}
        if commit_title:
            payload["commit_title"] = commit_title

        resp = await client.put(url, headers=_headers(), json=payload)
        if resp.status_code == 200:
            return json.dumps({"status": "merged", "sha": resp.json().get("sha")})
        else:
            msg = resp.json().get("message", resp.text)
            return json.dumps({"status": "failed", "message": msg})


async def get_file_content(params: dict) -> str:
    """Get a file's content from the repo."""
    path = params["path"]
    ref = params.get("ref", "main")

    async with httpx.AsyncClient() as client:
        url = f"{GITHUB_API}/repos/{settings.github_repo}/contents/{path}"
        resp = await client.get(url, headers=_headers(), params={"ref": ref})
        resp.raise_for_status()

        data = resp.json()
        if data.get("encoding") == "base64":
            import base64

            content = base64.b64decode(data["content"]).decode("utf-8")
        else:
            content = data.get("content", "")

        return json.dumps(
            {
                "path": data["path"],
                "size": data["size"],
                "sha": data["sha"],
                "content": content[:10000],  # Truncate very large files
            }
        )


async def create_commit(params: dict) -> str:
    """Create a commit on a branch by updating a file."""
    path = params["path"]
    content = params["content"]
    message = params["message"]
    branch = params["branch"]
    sha = params.get("sha")  # Current file SHA (required for updates)

    # Safety: never commit directly to protected branches
    if branch in PROTECTED_BRANCHES:
        return json.dumps(
            {
                "error": f"BLOCKED: Cannot commit directly to protected branch '{branch}'. "
                "Create a PR branch instead."
            }
        )

    # Safety: only allow modifications under approved paths
    if not any(path.startswith(prefix) for prefix in ALLOWED_COMMIT_PATHS):
        return json.dumps(
            {
                "error": f"BLOCKED: Cannot modify '{path}'. "
                f"Agent can only modify files under: {', '.join(ALLOWED_COMMIT_PATHS)}"
            }
        )

    import base64

    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

    async with httpx.AsyncClient() as client:
        url = f"{GITHUB_API}/repos/{settings.github_repo}/contents/{path}"
        payload: dict[str, Any] = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        resp = await client.put(url, headers=_headers(), json=payload)
        if resp.status_code in (200, 201):
            return json.dumps({"status": "ok", "sha": resp.json()["commit"]["sha"]})
        else:
            return json.dumps({"status": "failed", "message": resp.text})


async def create_branch(params: dict) -> str:
    """Create a new branch from a base ref (default: main)."""
    branch_name = params["branch_name"]
    base = params.get("base", "main")

    # Safety: branch name must start with a safe prefix
    safe_prefixes = ("fix/", "feat/", "agent/")
    if not any(branch_name.startswith(p) for p in safe_prefixes):
        return json.dumps(
            {"error": f"BLOCKED: Branch name must start with one of: {', '.join(safe_prefixes)}"}
        )

    async with httpx.AsyncClient() as client:
        # Get the SHA of the base branch
        url = f"{GITHUB_API}/repos/{settings.github_repo}/git/ref/heads/{base}"
        resp = await client.get(url, headers=_headers())
        if resp.status_code != 200:
            return json.dumps({"error": f"Base branch '{base}' not found"})
        base_sha = resp.json()["object"]["sha"]

        # Create the new branch
        url = f"{GITHUB_API}/repos/{settings.github_repo}/git/refs"
        resp = await client.post(
            url,
            headers=_headers(),
            json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
        if resp.status_code == 201:
            return json.dumps({"status": "ok", "branch": branch_name, "sha": base_sha})
        else:
            return json.dumps({"status": "failed", "message": resp.text})


async def create_pr(params: dict) -> str:
    """Create a pull request."""
    title = params["title"]
    body = params.get("body", "")
    head = params["head"]  # Branch with changes
    base = params.get("base", "main")

    async with httpx.AsyncClient() as client:
        url = f"{GITHUB_API}/repos/{settings.github_repo}/pulls"
        resp = await client.post(
            url,
            headers=_headers(),
            json={"title": title, "body": body, "head": head, "base": base},
        )
        if resp.status_code == 201:
            pr = resp.json()
            return json.dumps(
                {
                    "status": "ok",
                    "pr_number": pr["number"],
                    "html_url": pr["html_url"],
                }
            )
        else:
            return json.dumps({"status": "failed", "message": resp.text})


def get_github_tools() -> list[ToolDefinition]:
    """Return all GitHub tool definitions."""
    return [
        ToolDefinition(
            name="github_list_prs",
            description="List open pull requests on the home-ops repository. Can filter by author.",
            input_schema={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "PR state: open, closed, all (default: open)",
                    },
                    "author": {
                        "type": "string",
                        "description": "Filter by author login (e.g., 'renovate[bot]')",
                    },
                },
            },
            handler=list_prs,
        ),
        ToolDefinition(
            name="github_get_pr",
            description=(
                "Get detailed information about a specific PR"
                " including merge status, additions/deletions, labels."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pr_number": {"type": "integer", "description": "PR number"},
                },
                "required": ["pr_number"],
            },
            handler=get_pr,
        ),
        ToolDefinition(
            name="github_get_pr_files",
            description="Get the list of changed files in a PR with their diffs (patches).",
            input_schema={
                "type": "object",
                "properties": {
                    "pr_number": {"type": "integer", "description": "PR number"},
                },
                "required": ["pr_number"],
            },
            handler=get_pr_files,
        ),
        ToolDefinition(
            name="github_get_check_runs",
            description="Get CI check status (passing/failing) for a commit SHA or branch.",
            input_schema={
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Commit SHA or branch name"},
                },
                "required": ["ref"],
            },
            handler=get_check_runs,
        ),
        ToolDefinition(
            name="github_create_pr_comment",
            description="Post a comment on a pull request with your review analysis.",
            input_schema={
                "type": "object",
                "properties": {
                    "pr_number": {"type": "integer", "description": "PR number"},
                    "body": {"type": "string", "description": "Comment body (markdown supported)"},
                },
                "required": ["pr_number", "body"],
            },
            handler=create_pr_comment,
        ),
        ToolDefinition(
            name="github_merge_pr",
            description=(
                "Merge a pull request using squash merge."
                " Only use when auto-merge mode is enabled and all criteria are met."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pr_number": {"type": "integer", "description": "PR number"},
                    "commit_title": {
                        "type": "string",
                        "description": "Custom commit title (optional)",
                    },
                },
                "required": ["pr_number"],
            },
            handler=merge_pr,
        ),
        ToolDefinition(
            name="github_get_file_content",
            description="Get the content of a file from the repository at a specific ref/branch.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path in the repo"},
                    "ref": {
                        "type": "string",
                        "description": "Branch or commit SHA (default: main)",
                    },
                },
                "required": ["path"],
            },
            handler=get_file_content,
        ),
        ToolDefinition(
            name="github_create_commit",
            description=(
                "Create a commit on a branch by creating or updating a file."
                " Use for fix commits on PR branches."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path in the repo"},
                    "content": {"type": "string", "description": "New file content"},
                    "message": {"type": "string", "description": "Commit message"},
                    "branch": {"type": "string", "description": "Target branch"},
                    "sha": {
                        "type": "string",
                        "description": (
                            "Current file SHA (required for updates,"
                            " get from github_get_file_content)"
                        ),
                    },
                },
                "required": ["path", "content", "message", "branch"],
            },
            handler=create_commit,
        ),
        ToolDefinition(
            name="github_create_branch",
            description=(
                "Create a new git branch from main."
                " Branch name must start with fix/, feat/, or agent/."
                " Use this before committing fixes to a PR branch."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "branch_name": {
                        "type": "string",
                        "description": "Branch name (must start with fix/, feat/, or agent/)",
                    },
                    "base": {
                        "type": "string",
                        "description": "Base branch (default: main)",
                    },
                },
                "required": ["branch_name"],
            },
            handler=create_branch,
        ),
        ToolDefinition(
            name="github_create_pr",
            description=(
                "Create a pull request from a branch."
                " Use after creating a branch and committing changes."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "PR title"},
                    "body": {"type": "string", "description": "PR description (markdown)"},
                    "head": {"type": "string", "description": "Branch with changes"},
                    "base": {
                        "type": "string",
                        "description": "Target branch (default: main)",
                    },
                },
                "required": ["title", "head"],
            },
            handler=create_pr,
        ),
    ]
