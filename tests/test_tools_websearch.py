"""Tests for the SearXNG-backed web search on the Python side.

The cases worth guarding are the ones that look like "the web has nothing" when
they are really "this tool is broken": a SearXNG without the JSON format
answers HTML with a 200, and a stale instance whose scrapers have broken
upstream returns an empty result set while looking perfectly healthy.
"""

from __future__ import annotations

import json

import pytest

from home_ops_agent.agent import untrusted
from home_ops_agent.agent.tools import websearch


@pytest.fixture(autouse=True)
def _instance(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://searxng.productivity.svc.cluster.local:8080/")


@pytest.mark.asyncio
async def test_no_instance_configured_is_reported_not_guessed(monkeypatch):
    """There is deliberately no default.

    An instance name borrowed from one particular cluster is, for anyone else, a
    tool that is always present and always fails — worse than absent, because
    the model keeps choosing it and reports the failure as though the web were
    down.
    """
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    result = json.loads(await websearch.web_search({"query": "anything"}))
    assert "SEARXNG_URL is not set" in result["error"]


@pytest.mark.asyncio
async def test_results_are_returned_with_their_urls(httpx_mock):
    httpx_mock.add_response(
        json={
            "results": [
                {
                    "title": "cert-manager 1.19 release notes",
                    "url": "https://cert-manager.io/docs/releases/1.19/",
                    "content": "x" * 500,
                },
                {"title": "second", "url": "https://example.com/2", "content": "short"},
            ]
        }
    )
    result = json.loads(
        untrusted.unwrap(await websearch.web_search({"query": "cert-manager 1.19"}))
    )

    assert result["count"] == 2
    assert result["results"][0]["url"] == "https://cert-manager.io/docs/releases/1.19/"
    # Snippets are trimmed: twenty untrimmed ones bury the investigation.
    assert len(result["results"][0]["snippet"]) == websearch.SNIPPET_CHARS


@pytest.mark.asyncio
async def test_empty_results_name_the_dead_engines(httpx_mock):
    """SearXNG's scrapers break upstream constantly.

    A stale instance returns zero results for every query while looking healthy,
    so "no results" without the unresponsive engines is indistinguishable from
    "the web has nothing on this".
    """
    httpx_mock.add_response(
        json={"results": [], "unresponsive_engines": [["google", "CAPTCHA"], ["brave", "timeout"]]}
    )
    result = json.loads(await websearch.web_search({"query": "whatever"}))

    assert result["results"] == []
    assert "google" in result["note"] and "brave" in result["note"]


@pytest.mark.asyncio
async def test_html_response_is_called_out(httpx_mock):
    """A SearXNG without the JSON format enabled answers HTML with a 200.

    Left unhandled that is an exception, or worse a permanent silent "no
    results" — when the fix is one setting on the instance.
    """
    httpx_mock.add_response(text="<!DOCTYPE html><html></html>")
    result = json.loads(await websearch.web_search({"query": "whatever"}))
    assert "did not return JSON" in result["error"]


@pytest.mark.asyncio
async def test_an_unreachable_instance_fails_the_call_not_the_run(httpx_mock):
    import httpx

    httpx_mock.add_exception(httpx.ConnectError("no route to host"))
    result = json.loads(await websearch.web_search({"query": "whatever"}))
    assert "unreachable" in result["error"]


@pytest.mark.asyncio
async def test_limit_is_clamped(httpx_mock):
    httpx_mock.add_response(
        json={"results": [{"title": str(i), "url": f"u{i}", "content": ""} for i in range(50)]}
    )
    result = json.loads(untrusted.unwrap(await websearch.web_search({"query": "x", "limit": 999})))
    assert result["count"] == websearch.MAX_LIMIT


def test_the_skill_is_registered():
    from home_ops_agent.agent.skills import init_registry, registry

    init_registry()
    assert registry.get("web_search") is not None


def test_there_is_only_one_web_search_implementation():
    """`searxng.ts` is gone.

    It and this file were the same query against the same instance, written
    twice — and two implementations of one tool drift. The extension directory
    now holds only the bridge, so both backends run this code.
    """
    from pathlib import Path

    extensions = Path(__file__).resolve().parents[1] / "extensions"
    for path in extensions.glob("*.ts"):
        assert "web_search" not in path.read_text(encoding="utf-8"), path.name
