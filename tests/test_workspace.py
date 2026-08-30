"""Tests for the git worktree workspace and its commit guardrails.

The guardrails here replace the ones ``github_create_commit`` applies to a
single path, so they are the only thing standing between an agent with Edit and
Bash and the rest of the repo. They get the same scrutiny.
"""

import json
import subprocess
from pathlib import Path

import pytest

from home_ops_agent.agent import claude_code
from home_ops_agent.agent import workspace as ws_mod
from home_ops_agent.agent.workspace import Workspace


def _git(*args, cwd):
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Workspace:
    """A throwaway git repo standing in for a checked-out worktree."""
    root = tmp_path / "wt"
    root.mkdir()
    _git("init", "-q", "-b", "fix/test", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    (root / "kubernetes" / "apps").mkdir(parents=True)
    (root / "kubernetes" / "apps" / "app.yaml").write_text("kind: HelmRelease\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    return Workspace(path=root, branch="fix/test", token="")


# --- path guardrails ---


def test_blocked_paths_allows_manifests():
    assert ws_mod._blocked_paths(["kubernetes/apps/media/app.yaml"]) == []


def test_blocked_paths_rejects_everything_else():
    paths = [
        "kubernetes/apps/ok.yaml",
        ".github/workflows/build.yaml",
        "Dockerfile",
        "kubernetes/flux/cluster.yaml",
    ]
    assert ws_mod._blocked_paths(paths) == [
        ".github/workflows/build.yaml",
        "Dockerfile",
        "kubernetes/flux/cluster.yaml",
    ]


async def test_commit_rejects_protected_branch(repo):
    repo.branch = "main"
    result = await ws_mod.commit_and_push(repo, "nope")
    assert result["status"] == "blocked"
    assert "protected branch" in result["error"]


async def test_commit_reports_no_changes(repo):
    result = await ws_mod.commit_and_push(repo, "nothing to do")
    assert result["status"] == "no_changes"


async def test_commit_blocks_and_unstages_disallowed_paths(repo):
    (repo.path / "kubernetes" / "apps" / "app.yaml").write_text("kind: HelmRelease\nfixed: true\n")
    (repo.path / "Dockerfile").write_text("FROM scratch\n")

    result = await ws_mod.commit_and_push(repo, "sneaky")

    assert result["status"] == "blocked"
    assert result["blocked_paths"] == ["Dockerfile"]
    # A blocked attempt must not leave the index staged for a later commit.
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=str(repo.path),
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert staged == ""
    # And nothing was committed.
    count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=str(repo.path),
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert count == "1"


async def test_commit_blocks_rename_out_of_allowed_path(repo):
    """A rename must be checked on both sides, not just its destination."""
    src = repo.path / "kubernetes" / "apps" / "app.yaml"
    src.rename(repo.path / "escaped.yaml")

    result = await ws_mod.commit_and_push(repo, "move it out")

    assert result["status"] == "blocked"
    assert "escaped.yaml" in result["blocked_paths"]


async def test_commit_blocks_deletion_outside_allowed_path(repo):
    (repo.path / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=repo.path)
    _git("commit", "-q", "-m", "add readme", cwd=repo.path)

    (repo.path / "README.md").unlink()

    result = await ws_mod.commit_and_push(repo, "remove readme")
    assert result["status"] == "blocked"
    assert result["blocked_paths"] == ["README.md"]


# --- workspace lifecycle ---


async def test_open_workspace_refuses_protected_branch():
    with pytest.raises(ValueError, match="protected branch"):
        async with ws_mod.open_workspace("main", "tok"):
            pass


async def test_open_workspace_requires_token():
    with pytest.raises(ValueError, match="GitHub token"):
        async with ws_mod.open_workspace("fix/x", ""):
            pass


def test_authed_url_is_not_persisted_in_logs():
    url = ws_mod._authed_url("secret-token")
    assert "secret-token" in url
    assert ws_mod._redact(f"failed to push {url}", "secret-token") == (
        f"failed to push https://x-access-token:***@github.com/{ws_mod.settings.github_repo}.git"
    )


# --- tool exposure ---


async def test_workspace_commit_tool_requires_a_message(repo):
    tools = ws_mod.build_workspace_tools(repo)
    assert [t.name for t in tools] == ["workspace_commit"]

    result = await tools[0].handler({"message": "  "})
    assert result["status"] == "failed"


def test_workspace_tool_names_the_branch(repo):
    tool = ws_mod.build_workspace_tools(repo)[0]
    assert "fix/test" in tool.description
    assert "kubernetes/apps/" in tool.description


# --- backend options ---


def test_workspace_run_enables_file_tools_and_masks_secrets(repo, monkeypatch):
    ctx = claude_code._ToolContext(None, None)
    options = claude_code.build_options(
        [], "p", "claude-code/sonnet", 30, "oat-token", ctx, workspace=repo
    )

    # With a checkout the agent needs real file/shell tools to be useful.
    assert options.tools == ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
    assert options.cwd == str(repo.path)
    # Bash would otherwise expose the control plane's secrets to the agent.
    for key in ("DATABASE_URL", "SESSION_SECRET", "NTFY_TOKEN", "GITHUB_TOKEN"):
        assert options.env[key] == ""
    # The subscription token must survive the masking — it is what authenticates
    # the CLI itself.
    assert options.env["CLAUDE_CODE_OAUTH_TOKEN"] == "oat-token"


def test_no_workspace_keeps_tools_locked_down():
    ctx = claude_code._ToolContext(None, None)
    options = claude_code.build_options([], "p", "claude-code/sonnet", 10, "tok", ctx)
    assert options.tools == []
    # Without a shell there is nothing to mask beyond the API key trap.
    assert "DATABASE_URL" not in options.env


# --- the truncation bug this uncovered ---


async def test_get_file_content_flags_truncation(monkeypatch):
    """Truncated content must be labelled — it feeds a whole-file commit."""
    import httpx

    from home_ops_agent.agent.tools import github

    big = "x" * 25000

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            import base64

            return {
                "path": "kubernetes/apps/big.yaml",
                "size": len(big),
                "sha": "abc",
                "encoding": "base64",
                "content": base64.b64encode(big.encode()).decode(),
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client())

    result = json.loads(await github.get_file_content({"path": "kubernetes/apps/big.yaml"}))

    assert result["truncated"] is True
    assert "warning" in result
    assert len(result["content"]) == 10000


async def test_get_file_content_untruncated_has_no_warning(monkeypatch):
    import base64

    import httpx

    from home_ops_agent.agent.tools import github

    small = "kind: HelmRelease\n"

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "path": "kubernetes/apps/small.yaml",
                "size": len(small),
                "sha": "abc",
                "encoding": "base64",
                "content": base64.b64encode(small.encode()).decode(),
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client())

    result = json.loads(await github.get_file_content({"path": "kubernetes/apps/small.yaml"}))

    assert result["truncated"] is False
    assert "warning" not in result
    assert result["content"] == small


# --- happy path: it really does commit and push ---


@pytest.fixture
def repo_with_remote(tmp_path: Path) -> Workspace:
    """A worktree wired to a local bare remote, so push is exercised for real."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "fix/test", str(remote)],
        check=True,
        capture_output=True,
    )
    root = tmp_path / "wt"
    root.mkdir()
    _git("init", "-q", "-b", "fix/test", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    (root / "kubernetes" / "apps").mkdir(parents=True)
    (root / "kubernetes" / "apps" / "app.yaml").write_text("kind: HelmRelease\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    _git("push", "-q", str(remote), "HEAD:refs/heads/fix/test", cwd=root)
    return Workspace(path=root, branch="fix/test", token=str(remote))


async def test_commit_pushes_multi_file_change(repo_with_remote, monkeypatch):
    """The whole point: several files, one atomic commit, actually pushed."""
    # The token doubles as the push URL here so no network is involved.
    monkeypatch.setattr(ws_mod, "_authed_url", lambda token: token)

    ws = repo_with_remote
    apps = ws.path / "kubernetes" / "apps"
    (apps / "app.yaml").write_text("kind: HelmRelease\napiVersion: v2\n")
    (apps / "configmap.yaml").write_text("kind: ConfigMap\n")

    result = await ws_mod.commit_and_push(ws, "fix: bump HelmRelease apiVersion")

    assert result["status"] == "ok"
    assert sorted(result["files"]) == [
        "kubernetes/apps/app.yaml",
        "kubernetes/apps/configmap.yaml",
    ]
    assert len(result["sha"]) == 40

    # One commit, not two — the multi-file atomicity the API path cannot do.
    remote_log = (
        subprocess.run(
            ["git", "log", "--oneline", "fix/test"],
            cwd=ws.token,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .splitlines()
    )
    assert len(remote_log) == 2
    assert "bump HelmRelease apiVersion" in remote_log[0]


async def test_commit_uses_the_agent_identity(repo_with_remote, monkeypatch):
    monkeypatch.setattr(ws_mod, "_authed_url", lambda token: token)

    ws = repo_with_remote
    (ws.path / "kubernetes" / "apps" / "app.yaml").write_text("kind: HelmRelease\nx: 1\n")
    await ws_mod.commit_and_push(ws, "fix: something")

    author = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"],
        cwd=str(ws.path),
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert author == f"{ws_mod.GIT_USER_NAME} <{ws_mod.GIT_USER_EMAIL}>"


async def test_open_workspace_creates_and_cleans_up_a_worktree(tmp_path, monkeypatch):
    """Lifecycle: clone, worktree add on the branch, remove on exit."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("config", "user.email", "t@example.com", cwd=origin)
    _git("config", "user.name", "t", cwd=origin)
    (origin / "kubernetes").mkdir()
    (origin / "kubernetes" / "app.yaml").write_text("kind: HelmRelease\n")
    _git("add", "-A", cwd=origin)
    _git("commit", "-q", "-m", "init", cwd=origin)
    _git("branch", "fix/renovate", cwd=origin)

    monkeypatch.setattr(ws_mod, "_authed_url", lambda token: str(origin))
    monkeypatch.setattr(ws_mod, "workspace_root", lambda: tmp_path / "ws")

    async with ws_mod.open_workspace("fix/renovate", "tok") as ws:
        assert (ws.path / "kubernetes" / "app.yaml").exists()
        assert ws.branch == "fix/renovate"
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(ws.path),
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert branch == "fix/renovate"
        held = ws.path

    assert not held.exists()


async def test_open_workspace_reuses_the_clone(tmp_path, monkeypatch):
    """A second run fetches into the existing clone instead of re-cloning."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("config", "user.email", "t@example.com", cwd=origin)
    _git("config", "user.name", "t", cwd=origin)
    (origin / "f.yaml").write_text("a\n")
    _git("add", "-A", cwd=origin)
    _git("commit", "-q", "-m", "init", cwd=origin)
    _git("branch", "fix/one", cwd=origin)
    _git("branch", "fix/two", cwd=origin)

    monkeypatch.setattr(ws_mod, "_authed_url", lambda token: str(origin))
    monkeypatch.setattr(ws_mod, "workspace_root", lambda: tmp_path / "ws")

    async with ws_mod.open_workspace("fix/one", "tok") as first:
        first_path = first.path
    async with ws_mod.open_workspace("fix/two", "tok") as second:
        assert second.path != first_path
        assert (tmp_path / "ws" / "repo" / ".git").exists()
