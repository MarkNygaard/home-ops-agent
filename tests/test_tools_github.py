"""Tests for agent/tools/github.py — GitHub API tools and safety guardrails."""

import json

from home_ops_agent.agent.tools.github import (
    ALLOWED_COMMIT_PATHS,
    PROTECTED_BRANCHES,
    create_branch,
    create_commit,
    create_pr,
    create_pr_comment,
    get_check_runs,
    get_file_content,
    get_pr,
    get_pr_files,
    get_release,
    list_prs,
    merge_pr,
)

# --- Safety guardrail tests (no HTTP needed) ---


async def test_create_commit_blocked_protected_branch_main():
    result = json.loads(
        await create_commit(
            {"path": "kubernetes/apps/test.yaml", "content": "x", "message": "m", "branch": "main"}
        )
    )
    assert "BLOCKED" in result["error"]
    assert "protected branch" in result["error"]


async def test_create_commit_blocked_protected_branch_master():
    result = json.loads(
        await create_commit(
            {
                "path": "kubernetes/apps/test.yaml",
                "content": "x",
                "message": "m",
                "branch": "master",
            }
        )
    )
    assert "BLOCKED" in result["error"]


async def test_create_commit_blocked_disallowed_path():
    result = json.loads(
        await create_commit(
            {"path": "src/main.py", "content": "x", "message": "m", "branch": "fix/test"}
        )
    )
    assert "BLOCKED" in result["error"]
    assert "Cannot modify" in result["error"]


async def test_create_branch_blocked_bad_prefix():
    result = json.loads(await create_branch({"branch_name": "bad-branch-name"}))
    assert "BLOCKED" in result["error"]
    assert "must start with" in result["error"]


async def test_create_branch_blocked_random_prefix():
    result = json.loads(await create_branch({"branch_name": "hotfix/urgent"}))
    assert "BLOCKED" in result["error"]


def test_protected_branches_set():
    assert PROTECTED_BRANCHES == {"main", "master"}


def test_allowed_commit_paths_set():
    assert ALLOWED_COMMIT_PATHS == {"kubernetes/apps/"}


# --- HTTP-mocked tests ---


async def test_list_prs_empty(httpx_mock, mock_settings):
    httpx_mock.add_response(json=[])
    result = json.loads(await list_prs({"state": "open"}))
    assert result == []


async def test_list_prs_with_results(httpx_mock, mock_settings):
    httpx_mock.add_response(
        json=[
            {
                "number": 42,
                "title": "Update cilium",
                "user": {"login": "renovate[bot]"},
                "labels": [{"name": "type/patch"}],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T01:00:00Z",
                "mergeable_state": "clean",
                "draft": False,
                "html_url": "https://github.com/test/42",
            }
        ],
    )
    result = json.loads(await list_prs({"state": "open"}))
    assert len(result) == 1
    assert result[0]["number"] == 42
    assert result[0]["author"] == "renovate[bot]"
    assert result[0]["labels"] == ["type/patch"]


async def test_list_prs_filter_author(httpx_mock, mock_settings):
    httpx_mock.add_response(
        json=[
            {
                "number": 1,
                "title": "PR 1",
                "user": {"login": "renovate[bot]"},
                "labels": [],
                "created_at": "2026-01-01",
                "updated_at": "2026-01-01",
                "draft": False,
                "html_url": "url1",
            },
            {
                "number": 2,
                "title": "PR 2",
                "user": {"login": "human"},
                "labels": [],
                "created_at": "2026-01-01",
                "updated_at": "2026-01-01",
                "draft": False,
                "html_url": "url2",
            },
        ],
    )
    result = json.loads(await list_prs({"state": "open", "author": "renovate[bot]"}))
    assert len(result) == 1
    assert result[0]["author"] == "renovate[bot]"


async def test_get_pr_details(httpx_mock, mock_settings):
    httpx_mock.add_response(
        json={
            "number": 42,
            "title": "Update cilium",
            "body": "Bump version",
            "user": {"login": "renovate[bot]"},
            "labels": [{"name": "type/patch"}],
            "state": "open",
            "mergeable": True,
            "mergeable_state": "clean",
            "additions": 5,
            "deletions": 3,
            "changed_files": 1,
            "head": {"ref": "renovate/cilium", "sha": "abc123"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/test/42",
        },
    )
    result = json.loads(await get_pr({"pr_number": 42}))
    assert result["number"] == 42
    assert result["head_sha"] == "abc123"
    assert result["additions"] == 5


async def test_get_pr_files_truncates_patch(httpx_mock, mock_settings):
    long_patch = "x" * 3000
    httpx_mock.add_response(
        json=[
            {
                "filename": "test.yaml",
                "status": "modified",
                "additions": 10,
                "deletions": 5,
                "patch": long_patch,
            }
        ],
    )
    result = json.loads(await get_pr_files({"pr_number": 42}))
    assert len(result[0]["patch"]) == 2000


async def test_get_check_runs(httpx_mock, mock_settings):
    httpx_mock.add_response(
        json={
            "check_runs": [
                {
                    "name": "lint",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-01-01",
                    "completed_at": "2026-01-01",
                },
            ]
        },
    )
    result = json.loads(await get_check_runs({"ref": "abc123"}))
    assert len(result) == 1
    assert result[0]["name"] == "lint"
    assert result[0]["conclusion"] == "success"


async def test_merge_pr_success(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=200, json={"sha": "merge_sha_123"})
    result = json.loads(await merge_pr({"pr_number": 42}))
    assert result["status"] == "merged"
    assert result["sha"] == "merge_sha_123"


async def test_merge_pr_failure(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=409, json={"message": "Pull request not mergeable"})
    result = json.loads(await merge_pr({"pr_number": 42}))
    assert result["status"] == "failed"
    assert "not mergeable" in result["message"]


async def test_create_pr_comment_success(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=201, json={"id": 999})
    result = json.loads(await create_pr_comment({"pr_number": 42, "body": "LGTM"}))
    assert result["status"] == "ok"
    assert result["comment_id"] == 999


async def test_get_file_content_base64(httpx_mock, mock_settings):
    import base64

    content = base64.b64encode(b"apiVersion: v1\nkind: ConfigMap").decode()
    resp_json = {
        "path": "test.yaml",
        "size": 30,
        "sha": "abc",
        "encoding": "base64",
        "content": content,
    }
    httpx_mock.add_response(json=resp_json)
    result = json.loads(await get_file_content({"path": "test.yaml"}))
    assert "apiVersion" in result["content"]


async def test_get_release_found(httpx_mock, mock_settings):
    httpx_mock.add_response(
        json={
            "tag_name": "v1.0.0",
            "name": "Release 1.0.0",
            "published_at": "2026-01-01",
            "body": "Bug fixes and improvements",
            "html_url": "https://github.com/test/releases/v1.0.0",
            "prerelease": False,
        },
    )
    result = json.loads(await get_release({"repo": "test/repo", "tag": "v1.0.0"}))
    assert result["tag"] == "v1.0.0"
    assert "Bug fixes" in result["body"]


async def test_get_release_404_fallback_v_prefix(httpx_mock, mock_settings):
    # First request 404, second with alt tag succeeds
    httpx_mock.add_response(status_code=404)
    httpx_mock.add_response(
        json={
            "tag_name": "v1.0.0",
            "name": "Release",
            "published_at": "2026-01-01",
            "body": "notes",
            "html_url": "url",
            "prerelease": False,
        },
    )
    result = json.loads(await get_release({"repo": "test/repo", "tag": "1.0.0"}))
    assert result["tag"] == "v1.0.0"


async def test_get_release_not_found(httpx_mock, mock_settings):
    httpx_mock.add_response(status_code=404)
    httpx_mock.add_response(status_code=404)
    result = json.loads(await get_release({"repo": "test/repo", "tag": "v99.0.0"}))
    assert "error" in result
    assert "not found" in result["error"]


async def test_create_commit_allowed_path(httpx_mock, mock_settings):
    httpx_mock.add_response(
        status_code=201,
        json={"commit": {"sha": "new_sha_123"}},
    )
    result = json.loads(
        await create_commit(
            {
                "path": "kubernetes/apps/monitoring/grafana.yaml",
                "content": "test",
                "message": "fix grafana",
                "branch": "fix/grafana",
            }
        )
    )
    assert result["status"] == "ok"
    assert result["sha"] == "new_sha_123"


async def test_create_branch_allowed_fix(httpx_mock, mock_settings):
    httpx_mock.add_response(json={"object": {"sha": "base_sha"}})
    httpx_mock.add_response(status_code=201, json={})
    result = json.loads(await create_branch({"branch_name": "fix/my-fix"}))
    assert result["status"] == "ok"


async def test_create_branch_allowed_feat(httpx_mock, mock_settings):
    httpx_mock.add_response(json={"object": {"sha": "base_sha"}})
    httpx_mock.add_response(status_code=201, json={})
    result = json.loads(await create_branch({"branch_name": "feat/new-feature"}))
    assert result["status"] == "ok"


async def test_create_branch_allowed_agent(httpx_mock, mock_settings):
    httpx_mock.add_response(json={"object": {"sha": "base_sha"}})
    httpx_mock.add_response(status_code=201, json={})
    result = json.loads(await create_branch({"branch_name": "agent/auto-fix"}))
    assert result["status"] == "ok"


async def test_create_pr_success(httpx_mock, mock_settings):
    httpx_mock.add_response(
        status_code=201,
        json={"number": 99, "html_url": "https://github.com/test/99"},
    )
    result = json.loads(await create_pr({"title": "Fix things", "head": "fix/things"}))
    assert result["status"] == "ok"
    assert result["pr_number"] == 99
