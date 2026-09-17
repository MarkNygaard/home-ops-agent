"""Tests for the `code_fix` tool — asking for a fix from the chat.

The interesting cases are the ones where it must *not* run: a closed PR, a
missing token, a backend that cannot open a checkout. Each of those otherwise
ends as a confident report that nothing happened, or as a push to a branch
nobody meant to touch.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

from home_ops_agent.agent.tools import code_fix as code_fix_mod

OPEN_PR = {
    "number": 1043,
    "title": "chore(container): update jellyfin to v12",
    "author": "renovate[bot]",
    "state": "open",
    "head_ref": "renovate/jellyfin-12.x",
    "html_url": "https://github.com/x/y/pull/1043",
}


@pytest.fixture(autouse=True)
def _repo_is_configured(monkeypatch):
    monkeypatch.setattr(code_fix_mod.github, "repo_configured", lambda: None)


def _stub_pr(monkeypatch, pr: dict):
    async def _get_pr(_params):
        return json.dumps(pr)

    monkeypatch.setattr(code_fix_mod.github, "get_pr", _get_pr)


@pytest.mark.asyncio
async def test_a_closed_pr_is_refused(monkeypatch):
    """Pushing to a merged PR's branch is almost never what was meant, and is
    an awkward thing to undo."""
    from home_ops_agent.config import settings

    monkeypatch.setattr(settings, "github_token", "gh-token")
    _stub_pr(monkeypatch, {**OPEN_PR, "state": "closed"})

    result = json.loads(await code_fix_mod.code_fix({"pr_number": 1043}))
    assert "closed" in result["error"]


@pytest.mark.asyncio
async def test_no_token_is_refused_before_a_checkout_is_attempted(monkeypatch):
    from home_ops_agent.config import settings

    monkeypatch.setattr(settings, "github_token", "")
    result = json.loads(await code_fix_mod.code_fix({"pr_number": 1043}))
    assert "GitHub token" in result["error"]


@pytest.mark.asyncio
async def test_a_bad_pr_number_is_reported_not_raised(monkeypatch):
    from home_ops_agent.config import settings

    monkeypatch.setattr(settings, "github_token", "gh-token")
    result = json.loads(await code_fix_mod.code_fix({"pr_number": "not a number"}))
    assert "pr_number" in result["error"]


@pytest.mark.asyncio
async def test_no_checkout_is_an_error_not_a_silent_api_fallback(monkeypatch):
    """`pr_fix` falls back to the single-file API path when a checkout fails,
    which is right for an unattended run — something beats nothing.

    Here a person asked for a fix and is owed the reason, rather than a
    one-file-at-a-time edit they did not ask for reported as success.
    """
    from home_ops_agent.agent import workspace as workspace_mod
    from home_ops_agent.config import settings

    monkeypatch.setattr(settings, "github_token", "gh-token")
    _stub_pr(monkeypatch, OPEN_PR)

    async def _model(_task):
        return "kimi-for-coding"

    @asynccontextmanager
    async def _no_workspace(_model, _branch):
        yield None

    monkeypatch.setattr(workspace_mod, "maybe_workspace", _no_workspace)
    monkeypatch.setattr("home_ops_agent.agent.models.get_model_for_task", _model, raising=False)

    result = json.loads(await code_fix_mod.code_fix({"pr_number": 1043}))
    assert "Could not open a checkout" in result["error"]
    assert "kimi-for-coding" in result["error"]


@pytest.mark.asyncio
async def test_a_fix_runs_with_the_workspace_and_reports_its_tools(monkeypatch, tmp_path):
    """The happy path, and the one field that answers "did it actually commit"."""
    from home_ops_agent.agent import workspace as workspace_mod
    from home_ops_agent.agent.core import AgentResult
    from home_ops_agent.agent.workspace import Workspace
    from home_ops_agent.config import settings

    monkeypatch.setattr(settings, "github_token", "gh-token")
    _stub_pr(monkeypatch, OPEN_PR)

    ws = Workspace(path=tmp_path, branch=OPEN_PR["head_ref"], token="gh-token")
    seen = {}

    @asynccontextmanager
    async def _workspace(_model, branch):
        seen["branch"] = branch
        yield ws

    class _Agent:
        def __init__(self, _creds):
            self.tools = {}

        def register_tools(self, tools):
            for t in tools:
                self.tools[t.name] = t

        async def run(self, **kwargs):
            seen["workspace"] = kwargs["workspace"]
            seen["registered"] = set(self.tools)
            return AgentResult(
                response="fixed and pushed",
                tool_calls=[{"name": "edit"}, {"name": "workspace_commit"}],
            )

    async def _model(_task):
        return "gpt-6-astra"

    async def _creds():
        return object()

    async def _prompt(_name):
        return "sys"

    monkeypatch.setattr(workspace_mod, "maybe_workspace", _workspace)
    monkeypatch.setattr("home_ops_agent.agent.core.Agent", _Agent)
    monkeypatch.setattr("home_ops_agent.agent.models.get_model_for_task", _model)
    monkeypatch.setattr("home_ops_agent.auth.credentials.build_credentials", _creds)
    monkeypatch.setattr("home_ops_agent.agent.prompts.get_prompt", _prompt)

    result = json.loads(await code_fix_mod.code_fix({"pr_number": 1043}))

    assert seen["branch"] == "renovate/jellyfin-12.x"
    assert seen["workspace"] is ws
    assert result["tools_used"] == ["edit", "workspace_commit"]
    assert result["result"] == "fixed and pushed"

    # A fix must not be able to start another fix.
    assert "code_fix" not in seen["registered"]


@pytest.mark.asyncio
async def test_extra_instructions_reach_the_nested_run(monkeypatch, tmp_path):
    """Whatever the person added in the chat is the most specific information
    available, and dropping it silently would be worse than not accepting it."""
    from home_ops_agent.agent import workspace as workspace_mod
    from home_ops_agent.agent.core import AgentResult
    from home_ops_agent.agent.workspace import Workspace
    from home_ops_agent.config import settings

    monkeypatch.setattr(settings, "github_token", "gh-token")
    _stub_pr(monkeypatch, OPEN_PR)
    seen = {}

    @asynccontextmanager
    async def _workspace(_model, _branch):
        yield Workspace(path=tmp_path, branch=OPEN_PR["head_ref"], token="t")

    class _Agent:
        def __init__(self, _creds): ...
        def register_tools(self, _tools): ...

        async def run(self, **kwargs):
            seen["content"] = kwargs["messages"][0]["content"]
            return AgentResult(response="done")

    async def _model(_task):
        return "gpt-6-astra"

    async def _creds():
        return object()

    async def _prompt(_name):
        return "sys"

    monkeypatch.setattr(workspace_mod, "maybe_workspace", _workspace)
    monkeypatch.setattr("home_ops_agent.agent.core.Agent", _Agent)
    monkeypatch.setattr("home_ops_agent.agent.models.get_model_for_task", _model)
    monkeypatch.setattr("home_ops_agent.auth.credentials.build_credentials", _creds)
    monkeypatch.setattr("home_ops_agent.agent.prompts.get_prompt", _prompt)

    await code_fix_mod.code_fix(
        {"pr_number": 1043, "instructions": "keep the existing probe timings"}
    )

    assert "keep the existing probe timings" in seen["content"]


def test_the_skill_is_registered():
    """A tool nothing registers is a tool nobody can call."""
    from home_ops_agent.agent.skills import init_registry, registry

    init_registry()
    assert registry.get("code_fix") is not None
