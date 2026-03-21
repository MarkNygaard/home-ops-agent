"""Prometheus query tools for the agent."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx

from home_ops_agent.agent.core import ToolDefinition

if TYPE_CHECKING:
    from home_ops_agent.agent.skills import SkillDefinition

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://prometheus-operated.monitoring.svc.cluster.local:9090"


async def prometheus_query(params: dict, base_url: str = DEFAULT_URL) -> str:
    """Execute an instant PromQL query."""
    query = params["query"]
    time = params.get("time")

    try:
        request_params: dict = {"query": query}
        if time:
            request_params["time"] = time

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/api/v1/query", params=request_params)
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Query failed")})

        return json.dumps(data["data"], default=str)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Prometheus query failed: {e}"})


async def prometheus_query_range(params: dict, base_url: str = DEFAULT_URL) -> str:
    """Execute a range PromQL query."""
    query = params["query"]
    start = params["start"]
    end = params["end"]
    step = params.get("step", "60s")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{base_url}/api/v1/query_range",
                params={"query": query, "start": start, "end": end, "step": step},
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Query failed")})

        return json.dumps(data["data"], default=str)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Prometheus range query failed: {e}"})


async def prometheus_metric_names(params: dict, base_url: str = DEFAULT_URL) -> str:
    """List available metric names."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/api/v1/label/__name__/values")
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Failed to get metric names")})

        return json.dumps(data["data"])
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Failed to get metric names: {e}"})


async def prometheus_label_names(params: dict, base_url: str = DEFAULT_URL) -> str:
    """List all label names."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/api/v1/labels")
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Failed to get labels")})

        return json.dumps(data["data"])
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Failed to get label names: {e}"})


async def prometheus_label_values(params: dict, base_url: str = DEFAULT_URL) -> str:
    """List values for a specific label."""
    label = params["label"]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/api/v1/label/{label}/values")
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Failed to get label values")})

        return json.dumps(data["data"])
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Failed to get label values: {e}"})


async def prometheus_alerts(params: dict, base_url: str = DEFAULT_URL) -> str:
    """Get currently firing alerts."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/api/v1/alerts")
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            return json.dumps({"error": data.get("error", "Failed to get alerts")})

        return json.dumps(data["data"], default=str)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"Failed to get alerts: {e}"})


def _get_tools(config: dict) -> list[ToolDefinition]:
    """Return Prometheus tool definitions bound to the configured URL."""
    base_url = config.get("url", DEFAULT_URL).rstrip("/")

    return [
        ToolDefinition(
            name="prometheus_query",
            description=(
                "Execute an instant PromQL query against Prometheus. Returns current metric values."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "PromQL query (e.g., 'up', 'rate(http_requests_total[5m])')"
                        ),
                    },
                    "time": {
                        "type": "string",
                        "description": "Evaluation timestamp (RFC3339 or Unix). Defaults to now.",
                    },
                },
                "required": ["query"],
            },
            handler=lambda params, url=base_url: prometheus_query(params, url),
        ),
        ToolDefinition(
            name="prometheus_query_range",
            description="Execute a range PromQL query. Returns metric values over a time range.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "PromQL query string"},
                    "start": {
                        "type": "string",
                        "description": "Start time (RFC3339 or Unix timestamp)",
                    },
                    "end": {
                        "type": "string",
                        "description": "End time (RFC3339 or Unix timestamp)",
                    },
                    "step": {
                        "type": "string",
                        "description": "Query resolution step (e.g., '60s', '5m'). Default: 60s",
                    },
                },
                "required": ["query", "start", "end"],
            },
            handler=lambda params, url=base_url: prometheus_query_range(params, url),
        ),
        ToolDefinition(
            name="prometheus_metric_names",
            description="List all available Prometheus metric names.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda params, url=base_url: prometheus_metric_names(params, url),
        ),
        ToolDefinition(
            name="prometheus_label_names",
            description="List all Prometheus label names.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda params, url=base_url: prometheus_label_names(params, url),
        ),
        ToolDefinition(
            name="prometheus_label_values",
            description="List all values for a specific Prometheus label.",
            input_schema={
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Label name (e.g., 'namespace', 'job', 'instance')",
                    },
                },
                "required": ["label"],
            },
            handler=lambda params, url=base_url: prometheus_label_values(params, url),
        ),
        ToolDefinition(
            name="prometheus_alerts",
            description=(
                "Get all currently firing Prometheus alerts with their labels and annotations."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=lambda params, url=base_url: prometheus_alerts(params, url),
        ),
    ]


def _make_skill() -> SkillDefinition:
    from home_ops_agent.agent.skills import SkillDefinition

    return SkillDefinition(
        id="prometheus",
        name="Prometheus",
        description=(
            "Query Prometheus metrics, run PromQL queries,"
            " list metric/label names, and check firing alerts."
        ),
        builtin=False,
        config_fields=[
            {
                "key": "url",
                "label": "Prometheus URL",
                "type": "url",
                "default": DEFAULT_URL,
            },
        ],
        get_tools=_get_tools,
    )


SKILL: SkillDefinition = _make_skill()
