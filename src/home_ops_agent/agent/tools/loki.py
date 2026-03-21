"""Loki log query tools for the agent."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx

from home_ops_agent.agent.core import ToolDefinition

if TYPE_CHECKING:
    from home_ops_agent.agent.skills import SkillDefinition

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://loki-gateway.monitoring.svc.cluster.local"


async def loki_query(params: dict, base_url: str = DEFAULT_URL) -> str:
    """Execute an instant LogQL query."""
    query = params["query"]
    time = params.get("time")
    limit = params.get("limit", 100)

    try:
        request_params: dict = {"query": query, "limit": limit}
        if time:
            request_params["time"] = time

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/loki/api/v1/query", params=request_params)
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Query failed")})

        return json.dumps(data["data"], default=str)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Loki query failed: {e}"})


async def loki_query_range(params: dict, base_url: str = DEFAULT_URL) -> str:
    """Execute a range LogQL query."""
    query = params["query"]
    start = params["start"]
    end = params["end"]
    limit = params.get("limit", 100)
    step = params.get("step")

    try:
        request_params: dict = {"query": query, "start": start, "end": end, "limit": limit}
        if step:
            request_params["step"] = step

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/loki/api/v1/query_range", params=request_params)
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Query failed")})

        return json.dumps(data["data"], default=str)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Loki range query failed: {e}"})


async def loki_label_names(params: dict, base_url: str = DEFAULT_URL) -> str:
    """List all Loki label names."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/loki/api/v1/labels")
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Failed to get labels")})

        return json.dumps(data["data"])
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Failed to get Loki label names: {e}"})


async def loki_label_values(params: dict, base_url: str = DEFAULT_URL) -> str:
    """List values for a specific Loki label."""
    label = params["label"]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/loki/api/v1/label/{label}/values")
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Failed to get label values")})

        return json.dumps(data["data"])
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Failed to get Loki label values: {e}"})


def _get_tools(config: dict) -> list[ToolDefinition]:
    """Return Loki tool definitions bound to the configured URL."""
    base_url = config.get("url", DEFAULT_URL).rstrip("/")

    return [
        ToolDefinition(
            name="loki_query",
            description="Execute an instant LogQL query against Loki. Returns matching log lines.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "LogQL query string"
                            ' (e.g., \'{namespace="media"}\', \'{app="radarr"} |= "error"\')'
                        ),
                    },
                    "time": {
                        "type": "string",
                        "description": "Evaluation timestamp (RFC3339 or Unix). Defaults to now.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of log lines to return (default: 100)",
                    },
                },
                "required": ["query"],
            },
            handler=lambda params, url=base_url: loki_query(params, url),
        ),
        ToolDefinition(
            name="loki_query_range",
            description="Execute a range LogQL query. Returns log lines over a time range.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "LogQL query string"},
                    "start": {
                        "type": "string",
                        "description": "Start time (RFC3339 or Unix timestamp)",
                    },
                    "end": {
                        "type": "string",
                        "description": "End time (RFC3339 or Unix timestamp)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of log lines (default: 100)",
                    },
                    "step": {
                        "type": "string",
                        "description": "Query resolution step (e.g., '60s')",
                    },
                },
                "required": ["query", "start", "end"],
            },
            handler=lambda params, url=base_url: loki_query_range(params, url),
        ),
        ToolDefinition(
            name="loki_label_names",
            description="List all available Loki label names.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda params, url=base_url: loki_label_names(params, url),
        ),
        ToolDefinition(
            name="loki_label_values",
            description="List all values for a specific Loki label.",
            input_schema={
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Label name (e.g., 'namespace', 'app', 'container')",
                    },
                },
                "required": ["label"],
            },
            handler=lambda params, url=base_url: loki_label_values(params, url),
        ),
    ]


def _make_skill() -> SkillDefinition:
    from home_ops_agent.agent.skills import SkillDefinition

    return SkillDefinition(
        id="loki",
        name="Loki",
        description=(
            "Query Loki logs using LogQL. Search, filter, and aggregate"
            " log lines across all cluster workloads."
        ),
        builtin=False,
        config_fields=[
            {
                "key": "url",
                "label": "Loki Gateway URL",
                "type": "url",
                "default": DEFAULT_URL,
            },
        ],
        get_tools=_get_tools,
    )


SKILL: SkillDefinition = _make_skill()
