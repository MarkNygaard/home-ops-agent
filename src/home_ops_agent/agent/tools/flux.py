"""Flux CD tools for the agent — uses in-cluster Kubernetes API for Flux CRDs."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from home_ops_agent.agent.core import ToolDefinition

if TYPE_CHECKING:
    from home_ops_agent.agent.skills import SkillDefinition

logger = logging.getLogger(__name__)

# Initialize Kubernetes client (reuse the same config as kubernetes.py)
try:
    config.load_incluster_config()
except config.ConfigException:
    try:
        config.load_kube_config()
    except config.ConfigException:
        pass

custom_api = client.CustomObjectsApi()

FLUX_GROUP = "kustomize.toolkit.fluxcd.io"
FLUX_HELM_GROUP = "helm.toolkit.fluxcd.io"
FLUX_SOURCE_GROUP = "source.toolkit.fluxcd.io"


def _serialize(obj: Any) -> Any:
    """Serialize Kubernetes API objects to JSON-safe dicts."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return str(obj)


def _extract_conditions(conditions: list[dict] | None) -> list[dict]:
    """Extract key condition info from a list of conditions."""
    if not conditions:
        return []
    return [
        {
            "type": c.get("type"),
            "status": c.get("status"),
            "reason": c.get("reason"),
            "message": c.get("message", "")[:200],
            "lastTransitionTime": c.get("lastTransitionTime"),
        }
        for c in conditions
    ]


async def flux_get_kustomizations(params: dict) -> str:
    """List Flux Kustomizations with status."""
    namespace = params.get("namespace")

    try:
        if namespace:
            result = custom_api.list_namespaced_custom_object(
                group=FLUX_GROUP,
                version="v1",
                namespace=namespace,
                plural="kustomizations",
            )
        else:
            result = custom_api.list_cluster_custom_object(
                group=FLUX_GROUP,
                version="v1",
                plural="kustomizations",
            )

        items = []
        for ks in result.get("items", []):
            meta = ks.get("metadata", {})
            status = ks.get("status", {})
            spec = ks.get("spec", {})
            items.append(
                {
                    "name": meta.get("name"),
                    "namespace": meta.get("namespace"),
                    "path": spec.get("path"),
                    "suspended": spec.get("suspend", False),
                    "ready": any(
                        c.get("type") == "Ready" and c.get("status") == "True"
                        for c in status.get("conditions", [])
                    ),
                    "conditions": _extract_conditions(status.get("conditions")),
                    "lastAppliedRevision": status.get("lastAppliedRevision", ""),
                }
            )
        return json.dumps(items, default=_serialize)
    except ApiException as e:
        return json.dumps({"error": f"Failed to list Kustomizations: {e.reason}"})


async def flux_get_helmreleases(params: dict) -> str:
    """List Flux HelmReleases with status."""
    namespace = params.get("namespace")

    try:
        if namespace:
            result = custom_api.list_namespaced_custom_object(
                group=FLUX_HELM_GROUP,
                version="v2",
                namespace=namespace,
                plural="helmreleases",
            )
        else:
            result = custom_api.list_cluster_custom_object(
                group=FLUX_HELM_GROUP,
                version="v2",
                plural="helmreleases",
            )

        items = []
        for hr in result.get("items", []):
            meta = hr.get("metadata", {})
            status = hr.get("status", {})
            spec = hr.get("spec", {})
            chart = spec.get("chart", {}).get("spec", {})
            items.append(
                {
                    "name": meta.get("name"),
                    "namespace": meta.get("namespace"),
                    "chart": chart.get("chart", ""),
                    "version": chart.get("version", ""),
                    "suspended": spec.get("suspend", False),
                    "ready": any(
                        c.get("type") == "Ready" and c.get("status") == "True"
                        for c in status.get("conditions", [])
                    ),
                    "conditions": _extract_conditions(status.get("conditions")),
                    "lastAppliedRevision": status.get("lastAppliedRevision", ""),
                }
            )
        return json.dumps(items, default=_serialize)
    except ApiException as e:
        return json.dumps({"error": f"Failed to list HelmReleases: {e.reason}"})


async def flux_reconcile(params: dict) -> str:
    """Force reconcile a Flux resource by patching the requestedAt annotation."""
    kind = params["kind"].lower()
    name = params["name"]
    namespace = params["namespace"]

    if kind == "kustomization":
        group = FLUX_GROUP
        version = "v1"
        plural = "kustomizations"
    elif kind == "helmrelease":
        group = FLUX_HELM_GROUP
        version = "v2"
        plural = "helmreleases"
    else:
        return json.dumps(
            {"error": f"Unsupported kind: {kind}. Use 'kustomization' or 'helmrelease'."}
        )

    patch = {
        "metadata": {
            "annotations": {
                "reconcile.fluxcd.io/requestedAt": datetime.now(UTC).isoformat(),
            }
        }
    }

    try:
        custom_api.patch_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural,
            name=name,
            body=patch,
        )
        return json.dumps(
            {
                "status": "ok",
                "message": f"Reconcile triggered for {kind}/{name} in {namespace}",
            }
        )
    except ApiException as e:
        return json.dumps({"error": f"Failed to reconcile {kind}/{name}: {e.reason}"})


async def flux_suspend(params: dict) -> str:
    """Suspend a Flux resource."""
    kind = params["kind"].lower()
    name = params["name"]
    namespace = params["namespace"]

    if kind == "kustomization":
        group = FLUX_GROUP
        version = "v1"
        plural = "kustomizations"
    elif kind == "helmrelease":
        group = FLUX_HELM_GROUP
        version = "v2"
        plural = "helmreleases"
    else:
        return json.dumps({"error": f"Unsupported kind: {kind}"})

    patch = {"spec": {"suspend": True}}

    try:
        custom_api.patch_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural,
            name=name,
            body=patch,
        )
        return json.dumps({"status": "ok", "message": f"Suspended {kind}/{name} in {namespace}"})
    except ApiException as e:
        return json.dumps({"error": f"Failed to suspend {kind}/{name}: {e.reason}"})


async def flux_resume(params: dict) -> str:
    """Resume a suspended Flux resource."""
    kind = params["kind"].lower()
    name = params["name"]
    namespace = params["namespace"]

    if kind == "kustomization":
        group = FLUX_GROUP
        version = "v1"
        plural = "kustomizations"
    elif kind == "helmrelease":
        group = FLUX_HELM_GROUP
        version = "v2"
        plural = "helmreleases"
    else:
        return json.dumps({"error": f"Unsupported kind: {kind}"})

    patch = {"spec": {"suspend": False}}

    try:
        custom_api.patch_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural,
            name=name,
            body=patch,
        )
        return json.dumps({"status": "ok", "message": f"Resumed {kind}/{name} in {namespace}"})
    except ApiException as e:
        return json.dumps({"error": f"Failed to resume {kind}/{name}: {e.reason}"})


def _get_tools(config: dict) -> list[ToolDefinition]:
    """Return Flux tool definitions."""
    return [
        ToolDefinition(
            name="flux_get_kustomizations",
            description=(
                "List Flux Kustomizations with their ready status, conditions,"
                " and last applied revision."
                " Optionally filter by namespace."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": ("Namespace to list from. Omit for all namespaces."),
                    },
                },
            },
            handler=flux_get_kustomizations,
        ),
        ToolDefinition(
            name="flux_get_helmreleases",
            description=(
                "List Flux HelmReleases with their ready status,"
                " chart info, conditions, and revision."
                " Optionally filter by namespace."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": ("Namespace to list from. Omit for all namespaces."),
                    },
                },
            },
            handler=flux_get_helmreleases,
        ),
        ToolDefinition(
            name="flux_reconcile",
            description=(
                "Force reconcile a Flux Kustomization or HelmRelease by patching the"
                " reconcile.fluxcd.io/requestedAt annotation."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Resource kind: 'kustomization' or 'helmrelease'",
                        "enum": ["kustomization", "helmrelease"],
                    },
                    "name": {"type": "string", "description": "Resource name"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                },
                "required": ["kind", "name", "namespace"],
            },
            handler=flux_reconcile,
        ),
        ToolDefinition(
            name="flux_suspend",
            description="Suspend a Flux Kustomization or HelmRelease to stop reconciliation.",
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Resource kind: 'kustomization' or 'helmrelease'",
                        "enum": ["kustomization", "helmrelease"],
                    },
                    "name": {"type": "string", "description": "Resource name"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                },
                "required": ["kind", "name", "namespace"],
            },
            handler=flux_suspend,
        ),
        ToolDefinition(
            name="flux_resume",
            description="Resume a suspended Flux Kustomization or HelmRelease.",
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Resource kind: 'kustomization' or 'helmrelease'",
                        "enum": ["kustomization", "helmrelease"],
                    },
                    "name": {"type": "string", "description": "Resource name"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                },
                "required": ["kind", "name", "namespace"],
            },
            handler=flux_resume,
        ),
    ]


def _make_skill() -> SkillDefinition:
    from home_ops_agent.agent.skills import SkillDefinition

    return SkillDefinition(
        id="flux",
        name="Flux CD",
        description=(
            "Manage Flux GitOps resources: list Kustomizations and HelmReleases,"
            " force reconcile, suspend, and resume."
        ),
        builtin=False,
        get_tools=_get_tools,
    )


SKILL: SkillDefinition = _make_skill()
