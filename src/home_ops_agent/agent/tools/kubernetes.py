"""Kubernetes API tools for the agent."""

import json
import logging
from datetime import datetime
from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from home_ops_agent.agent.core import ToolDefinition

logger = logging.getLogger(__name__)

# Initialize Kubernetes client (in-cluster or from kubeconfig)
try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

core_v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()
custom_api = client.CustomObjectsApi()


def _serialize(obj: Any) -> Any:
    """Serialize Kubernetes API objects to JSON-safe dicts."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return str(obj)


async def get_pods(params: dict) -> str:
    """List pods in a namespace."""
    namespace = params.get("namespace", "default")
    label_selector = params.get("label_selector", "")

    try:
        pods = core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )
        result = []
        for pod in pods.items:
            container_statuses = []
            for cs in pod.status.container_statuses or []:
                container_statuses.append(
                    {
                        "name": cs.name,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count,
                        "state": str(cs.state),
                    }
                )
            result.append(
                {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "phase": pod.status.phase,
                    "node": pod.spec.node_name,
                    "containers": container_statuses,
                    "created": pod.metadata.creation_timestamp,
                }
            )
        return json.dumps(result, default=_serialize)
    except ApiException as e:
        return json.dumps({"error": f"Failed to list pods: {e.reason}"})


async def get_pod_logs(params: dict) -> str:
    """Get logs from a pod."""
    namespace = params["namespace"]
    pod_name = params["pod_name"]
    container = params.get("container")
    tail_lines = params.get("tail_lines", 100)

    try:
        logs = core_v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines,
        )
        return logs or "(no logs)"
    except ApiException as e:
        return json.dumps({"error": f"Failed to get logs: {e.reason}"})


async def get_events(params: dict) -> str:
    """Get events for a namespace or specific resource."""
    namespace = params.get("namespace", "default")
    resource_name = params.get("resource_name")

    try:
        if resource_name:
            field_selector = f"involvedObject.name={resource_name}"
        else:
            field_selector = ""

        events = core_v1.list_namespaced_event(
            namespace=namespace,
            field_selector=field_selector,
        )
        result = []
        sorted_events = sorted(
            events.items,
            key=lambda e: e.last_timestamp or e.metadata.creation_timestamp or datetime.min,
            reverse=True,
        )[:20]
        for event in sorted_events:
            result.append(
                {
                    "type": event.type,
                    "reason": event.reason,
                    "message": event.message,
                    "object": f"{event.involved_object.kind}/{event.involved_object.name}",
                    "count": event.count,
                    "last_seen": event.last_timestamp,
                }
            )
        return json.dumps(result, default=_serialize)
    except ApiException as e:
        return json.dumps({"error": f"Failed to get events: {e.reason}"})


async def describe_resource(params: dict) -> str:
    """Get details of any Kubernetes resource."""
    kind = params["kind"].lower()
    name = params["name"]
    namespace = params.get("namespace", "default")

    try:
        if kind == "pod":
            obj = core_v1.read_namespaced_pod(name, namespace)
        elif kind == "service":
            obj = core_v1.read_namespaced_service(name, namespace)
        elif kind == "deployment":
            obj = apps_v1.read_namespaced_deployment(name, namespace)
        elif kind == "statefulset":
            obj = apps_v1.read_namespaced_stateful_set(name, namespace)
        elif kind == "daemonset":
            obj = apps_v1.read_namespaced_daemon_set(name, namespace)
        elif kind == "node":
            obj = core_v1.read_node(name)
        elif kind == "configmap":
            obj = core_v1.read_namespaced_config_map(name, namespace)
        elif kind == "pvc":
            obj = core_v1.read_namespaced_persistent_volume_claim(name, namespace)
        else:
            return json.dumps({"error": f"Unsupported kind: {kind}"})

        return json.dumps(_serialize(obj), default=_serialize)
    except ApiException as e:
        return json.dumps({"error": f"Failed to describe {kind}/{name}: {e.reason}"})


async def get_nodes(params: dict) -> str:
    """Get node status and resource usage."""
    try:
        nodes = core_v1.list_node()
        result = []
        for node in nodes.items:
            conditions = {c.type: c.status for c in node.status.conditions or []}
            result.append(
                {
                    "name": node.metadata.name,
                    "conditions": conditions,
                    "capacity": {
                        "cpu": node.status.capacity.get("cpu"),
                        "memory": node.status.capacity.get("memory"),
                        "pods": node.status.capacity.get("pods"),
                    },
                    "allocatable": {
                        "cpu": node.status.allocatable.get("cpu"),
                        "memory": node.status.allocatable.get("memory"),
                        "pods": node.status.allocatable.get("pods"),
                    },
                }
            )
        return json.dumps(result, default=_serialize)
    except ApiException as e:
        return json.dumps({"error": f"Failed to list nodes: {e.reason}"})


# Namespaces where the agent must NOT take destructive actions
PROTECTED_NAMESPACES = {"kube-system", "flux-system", "cert-manager"}


async def restart_deployment(params: dict) -> str:
    """Trigger a rollout restart by patching the deployment annotation."""
    name = params["name"]
    namespace = params["namespace"]
    kind = params.get("kind", "deployment").lower()

    if namespace in PROTECTED_NAMESPACES:
        return json.dumps(
            {"error": f"BLOCKED: Cannot restart workloads in protected namespace '{namespace}'"}
        )

    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {"home-ops-agent/restartedAt": datetime.utcnow().isoformat()}
                }
            }
        }
    }

    try:
        if kind == "deployment":
            apps_v1.patch_namespaced_deployment(name, namespace, patch)
        elif kind == "statefulset":
            apps_v1.patch_namespaced_stateful_set(name, namespace, patch)
        else:
            return json.dumps({"error": f"Cannot restart kind: {kind}"})

        return json.dumps({"status": "ok", "message": f"Restarted {kind}/{name} in {namespace}"})
    except ApiException as e:
        return json.dumps({"error": f"Failed to restart: {e.reason}"})


async def delete_pod(params: dict) -> str:
    """Delete a pod to force recreation."""
    name = params["name"]
    namespace = params["namespace"]

    if namespace in PROTECTED_NAMESPACES:
        return json.dumps(
            {"error": f"BLOCKED: Cannot delete pods in protected namespace '{namespace}'"}
        )

    try:
        core_v1.delete_namespaced_pod(name, namespace)
        return json.dumps({"status": "ok", "message": f"Deleted pod {name} in {namespace}"})
    except ApiException as e:
        return json.dumps({"error": f"Failed to delete pod: {e.reason}"})


def get_kubernetes_tools() -> list[ToolDefinition]:
    """Return all Kubernetes tool definitions."""
    return [
        ToolDefinition(
            name="k8s_get_pods",
            description=(
                "List pods in a Kubernetes namespace with their status,"
                " restarts, and node placement."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace (default: 'default')",
                    },
                    "label_selector": {
                        "type": "string",
                        "description": "Label selector (e.g., 'app=nginx')",
                    },
                },
            },
            handler=get_pods,
        ),
        ToolDefinition(
            name="k8s_get_pod_logs",
            description=(
                "Get recent logs from a specific pod. Useful for diagnosing crashes or errors."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "pod_name": {"type": "string", "description": "Pod name"},
                    "container": {
                        "type": "string",
                        "description": "Container name (optional, for multi-container pods)",
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": "Number of log lines to return (default: 100)",
                    },
                },
                "required": ["namespace", "pod_name"],
            },
            handler=get_pod_logs,
        ),
        ToolDefinition(
            name="k8s_get_events",
            description=(
                "Get Kubernetes events for a namespace or specific resource."
                " Shows warnings, errors, and scheduling info."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "resource_name": {
                        "type": "string",
                        "description": "Filter events for a specific resource name",
                    },
                },
            },
            handler=get_events,
        ),
        ToolDefinition(
            name="k8s_describe_resource",
            description=(
                "Get full details of a Kubernetes resource"
                " (pod, deployment, statefulset, service, node, configmap, pvc)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": (
                            "Resource kind: pod, deployment, statefulset,"
                            " daemonset, service, node, configmap, pvc"
                        ),
                    },
                    "name": {"type": "string", "description": "Resource name"},
                    "namespace": {
                        "type": "string",
                        "description": "Kubernetes namespace (not needed for nodes)",
                    },
                },
                "required": ["kind", "name"],
            },
            handler=describe_resource,
        ),
        ToolDefinition(
            name="k8s_get_nodes",
            description=(
                "Get node status, conditions, and resource"
                " capacity/allocatable for all cluster nodes."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=get_nodes,
        ),
        ToolDefinition(
            name="k8s_restart_workload",
            description=(
                "Trigger a rollout restart on a deployment or statefulset"
                " by patching its annotation. The pods will be recreated."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Workload name"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "kind": {
                        "type": "string",
                        "description": (
                            "Workload kind: deployment or statefulset (default: deployment)"
                        ),
                    },
                },
                "required": ["name", "namespace"],
            },
            handler=restart_deployment,
        ),
        ToolDefinition(
            name="k8s_delete_pod",
            description=(
                "Delete a specific pod to force its recreation by the controller."
                " Use for stuck pods."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Pod name"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                },
                "required": ["name", "namespace"],
            },
            handler=delete_pod,
        ),
    ]
