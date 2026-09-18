"""Web search, backed by the self-hosted SearXNG.

The pi backend has had this since 0.14.0, as `extensions/searxng.ts`. The Claude
backend had nothing: `BUILTIN_TOOLS` is empty and `allowed_tools` is only the
in-process MCP server, so the CLI's own WebSearch and WebFetch are switched off
deliberately -- that is what keeps the guardrails in the Python handlers
authoritative, and it is not worth giving up.

So the agents that review pull requests could read a GitHub Release and nothing
else. For "what does this breaking change actually require" that is half an
answer: plenty of projects put the breaking change in a docs-site upgrade guide
and keep the release body to one line, and an upstream issue thread is often the
only place someone has written down what to do about it.

This is the same query the extension makes, against the same instance. The two
are deliberately separate implementations of thirty lines rather than one shared
one behind a bridge: a bridge would buy nothing, and the logic is small enough
that duplication is cheaper than the hop.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

import httpx

from home_ops_agent.agent.core import ToolDefinition

if TYPE_CHECKING:
    from home_ops_agent.agent.skills import SkillDefinition

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 8
MAX_LIMIT = 20

# Each result carries a snippet, and a model reading twenty untrimmed ones loses
# the investigation in them.
SNIPPET_CHARS = 300


def searxng_url() -> str:
    """The configured instance, or empty when there is none.

    No default. An earlier version of the extension fell back to a Service name
    from one particular cluster, which for anyone else is a tool that is always
    present and always fails -- worse than absent, because the model keeps
    choosing it and reports the failure as though the web were down.
    """
    return (os.environ.get("SEARXNG_URL") or "").rstrip("/")


async def web_search(params: dict) -> str:
    """Search the web through SearXNG."""
    base = searxng_url()
    if not base:
        return json.dumps({"error": "SEARXNG_URL is not set; web search is unavailable."})

    query = (params.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "A query is required."})
    limit = max(1, min(int(params.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{base}/search",
                params={"q": query, "format": "json"},
            )
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"SearXNG unreachable: {exc}"})

    if resp.status_code >= 400:
        return json.dumps({"error": f"SearXNG returned HTTP {resp.status_code}"})

    try:
        data = resp.json()
    except ValueError:
        # A SearXNG without the JSON format enabled answers with HTML and a 200,
        # which would otherwise surface as "no results" forever.
        return json.dumps(
            {"error": "SearXNG did not return JSON — enable the json format in its settings."}
        )

    results = (data.get("results") or [])[:limit]
    if not results:
        # Naming the dead engines matters: SearXNG's scrapers break upstream
        # constantly, and a stale instance returns zero results for every query
        # while looking perfectly healthy.
        dead = ", ".join(e[0] for e in (data.get("unresponsive_engines") or []) if e)
        return json.dumps(
            {"results": [], "note": f"No results. Unresponsive engines: {dead or 'none'}"}
        )

    return json.dumps(
        {
            "count": len(results),
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (r.get("content") or "")[:SNIPPET_CHARS],
                }
                for r in results
            ],
        }
    )


def _get_tools(config: dict) -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="web_search",
            description=(
                "Search the web through the self-hosted SearXNG. Use it for changelogs, "
                "upgrade guides, upstream issues and whether a regression is known — "
                "anything a GitHub Release body does not say. For a breaking change, "
                "search the project and version together, e.g. "
                "'cert-manager 1.19 breaking changes'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {
                        "type": "integer",
                        "description": f"Max results (default {DEFAULT_LIMIT}, max {MAX_LIMIT})",
                    },
                },
                "required": ["query"],
            },
            handler=web_search,
        )
    ]


def _make_skill() -> SkillDefinition:
    from home_ops_agent.agent.skills import SkillDefinition

    return SkillDefinition(
        id="web_search",
        name="Web Search",
        description=(
            "Search the web through a self-hosted SearXNG instance. Lets the PR review"
            " and deep review agents read changelogs, upgrade guides and upstream issues"
            " rather than only the GitHub Release body. Requires SEARXNG_URL."
        ),
        builtin=False,
        get_tools=_get_tools,
    )


SKILL: SkillDefinition = _make_skill()
