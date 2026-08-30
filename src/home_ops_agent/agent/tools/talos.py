"""Talos / tuppr upgrade diagnostics — read-only.

Node OS upgrades are driven by `tuppr` (`TalosUpgrade` / `KubernetesUpgrade`
CRs). When one stalls, the useful question is always *why*, and the answer has
so far been one of two shapes:

1. **The drain is impossible.** A PodDisruptionBudget allows zero disruptions —
   e.g. a CNPG `minAvailable: 1` PDB in front of a single replica — so the node
   can never be emptied and the upgrade Job times out.
2. **The upgrade Job failed for its own reasons.** Its conditions and its pod's
   state hold the answer, not the CR.

These tools answer both without touching anything. Deliberately **read-only**:
the recovery for a stalled upgrade is `talosctl upgrade --drain=false` against a
node, which is a different risk class from everything else this agent does — it
needs a talosconfig (effectively cluster-root) and it is not reversible in the
way a pod restart or a PR-branch commit is. The agent diagnoses and tells you
the command; a human runs it. Promote an action here only once the History log
shows the same diagnosis leading to the same command enough times to be sure.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from home_ops_agent.agent.core import ToolDefinition

if TYPE_CHECKING:
    from home_ops_agent.agent.skills import SkillDefinition

logger = logging.getLogger(__name__)

# Same in-cluster config the other Kubernetes-backed skills use.
try:
    config.load_incluster_config()
except config.ConfigException:
    try:
        config.load_kube_config()
    except config.ConfigException:
        pass

custom_api = client.CustomObjectsApi()
core_api = client.CoreV1Api()
policy_api = client.PolicyV1Api()
batch_api = client.BatchV1Api()

DEFAULT_GROUP = "tuppr.home-operations.com"
DEFAULT_VERSION = "v1alpha1"
DEFAULT_NAMESPACE = "system-upgrade"
# tuppr taints nodes it still considers out of date; clearing it is part of the
# manual recovery, so the agent needs to be able to see it.
OUTDATED_TAINT_PREFIX = "tuppr.home-operations.com/"


def _conditions(raw: list[dict] | None) -> list[dict]:
    """Condense a conditions array to the fields that explain a failure."""
    if not raw:
        return []
    return [
        {
            "type": c.get("type"),
            "status": c.get("status"),
            "reason": c.get("reason"),
            "message": (c.get("message") or "")[:300],
            "lastTransitionTime": c.get("lastTransitionTime"),
        }
        for c in raw
    ]


def _err(exc: ApiException, what: str) -> str:
    if exc.status == 404:
        return json.dumps(
            {
                "error": f"{what} not found (404). The CRD may not be installed, or the "
                "group/version/namespace configured for this skill may be wrong."
            }
        )
    if exc.status == 403:
        return json.dumps(
            {
                "error": f"Forbidden (403) reading {what}. The agent's ClusterRole is "
                "missing get/list permission for it."
            }
        )
    return json.dumps({"error": f"Kubernetes API error reading {what}: {exc.status} {exc.reason}"})


# --- upgrade CRs ---


async def talos_get_upgrades(params: dict) -> str:
    """List TalosUpgrade / KubernetesUpgrade CRs with phase and failed nodes."""
    cfg = params.get("_config", {})
    group = cfg.get("group") or DEFAULT_GROUP
    version = cfg.get("version") or DEFAULT_VERSION
    kind = params.get("kind", "talos")
    plural = "kubernetesupgrades" if kind == "kubernetes" else "talosupgrades"

    try:
        result = custom_api.list_cluster_custom_object(group=group, version=version, plural=plural)
    except ApiException as exc:
        return _err(exc, plural)

    items = []
    for item in result.get("items", []):
        meta = item.get("metadata", {})
        status = item.get("status", {})
        spec = item.get("spec", {})
        items.append(
            {
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "created": meta.get("creationTimestamp"),
                "phase": status.get("phase"),
                "target_version": (spec.get("talos") or spec.get("kubernetes") or {}).get("version")
                or spec.get("version"),
                "failed_nodes": status.get("failedNodes") or [],
                "current_node": status.get("currentNode"),
                "conditions": _conditions(status.get("conditions")),
                "message": (status.get("message") or "")[:300],
            }
        )

    return json.dumps({"kind": plural, "count": len(items), "items": items}, default=str)


async def talos_get_upgrade_jobs(params: dict) -> str:
    """Show upgrade Jobs and their pods — where a Job-level failure is explained."""
    cfg = params.get("_config", {})
    namespace = params.get("namespace") or cfg.get("namespace") or DEFAULT_NAMESPACE

    try:
        jobs = batch_api.list_namespaced_job(namespace=namespace)
    except ApiException as exc:
        return _err(exc, f"jobs in {namespace}")

    items: list[dict[str, Any]] = []
    for job in jobs.items:
        meta = job.metadata
        status = job.status
        entry = {
            "name": meta.name,
            "created": meta.creation_timestamp,
            "backoff_limit": job.spec.backoff_limit,
            "active": status.active or 0,
            "succeeded": status.succeeded or 0,
            "failed": status.failed or 0,
            "conditions": _conditions(
                [
                    {
                        "type": c.type,
                        "status": c.status,
                        "reason": c.reason,
                        "message": c.message,
                        "lastTransitionTime": c.last_transition_time,
                    }
                    for c in (status.conditions or [])
                ]
            ),
            "pods": [],
        }

        try:
            pods = core_api.list_namespaced_pod(
                namespace=namespace, label_selector=f"job-name={meta.name}"
            )
            for pod in pods.items:
                entry["pods"].append(
                    {
                        "name": pod.metadata.name,
                        "phase": pod.status.phase,
                        "node": pod.spec.node_name,
                        "reason": pod.status.reason,
                        "message": (pod.status.message or "")[:200],
                        "container_states": [
                            {
                                "name": cs.name,
                                "ready": cs.ready,
                                "restarts": cs.restart_count,
                                "waiting": cs.state.waiting.reason if cs.state.waiting else None,
                                "terminated": (
                                    cs.state.terminated.reason if cs.state.terminated else None
                                ),
                                "exit_code": (
                                    cs.state.terminated.exit_code if cs.state.terminated else None
                                ),
                            }
                            for cs in (pod.status.container_statuses or [])
                        ],
                    }
                )
        except ApiException:
            logger.warning("Could not list pods for job %s", meta.name)

        items.append(entry)

    return json.dumps({"namespace": namespace, "count": len(items), "jobs": items}, default=str)


# --- node state ---


async def talos_get_nodes(params: dict) -> str:
    """Node readiness, cordon state, upgrade taints, and Talos/kubelet versions."""
    node_name = params.get("node")

    try:
        if node_name:
            nodes = [core_api.read_node(name=node_name)]
        else:
            nodes = core_api.list_node().items
    except ApiException as exc:
        return _err(exc, f"node {node_name}" if node_name else "nodes")

    items = []
    for node in nodes:
        info = node.status.node_info
        taints = node.spec.taints or []
        items.append(
            {
                "name": node.metadata.name,
                "unschedulable": bool(node.spec.unschedulable),
                "ready": next(
                    (c.status for c in (node.status.conditions or []) if c.type == "Ready"),
                    "Unknown",
                ),
                "conditions": [
                    {"type": c.type, "status": c.status, "reason": c.reason}
                    for c in (node.status.conditions or [])
                    if c.status != "False" or c.type == "Ready"
                ],
                "os_image": info.os_image if info else None,
                "kubelet_version": info.kubelet_version if info else None,
                "taints": [{"key": t.key, "value": t.value, "effect": t.effect} for t in taints],
                # Called out separately because clearing it is a step in the
                # manual recovery.
                "upgrade_taints": [
                    t.key for t in taints if t.key.startswith(OUTDATED_TAINT_PREFIX)
                ],
            }
        )

    return json.dumps({"count": len(items), "nodes": items}, default=str)


# --- the drain question ---


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    """Read a field that may be an attribute or a dict key, under either spelling.

    The Kubernetes Python client exposes ``V1LabelSelector.match_labels``, and
    its ``to_dict()`` emits snake_case, while the raw API JSON uses camelCase
    ``matchLabels``. Accepting all three is what stops a selector silently
    reading as empty.
    """
    for name in names:
        if isinstance(obj, dict):
            value = obj.get(name)
        else:
            value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _selector_parts(selector: Any) -> tuple[dict, list]:
    """Extract ``(match_labels, match_expressions)`` from a label selector."""
    if selector is None:
        return {}, []
    labels = _field(selector, "match_labels", "matchLabels", default={}) or {}
    expressions = _field(selector, "match_expressions", "matchExpressions", default=[]) or []
    return labels, expressions


def _expression_matches(expression: Any, pod_labels: dict) -> bool | None:
    """Evaluate one matchExpressions requirement. ``None`` = unknown operator.

    Note the Kubernetes semantics for the negative operators: ``NotIn`` and
    ``DoesNotExist`` also match objects that do not carry the label at all.
    """
    key = _field(expression, "key")
    operator = _field(expression, "operator")
    values = _field(expression, "values", default=[]) or []
    if not key or not operator:
        return None

    present = key in pod_labels
    value = pod_labels.get(key)
    if operator == "In":
        return present and value in values
    if operator == "NotIn":
        return not present or value not in values
    if operator == "Exists":
        return present
    if operator == "DoesNotExist":
        return not present
    return None


def _selector_matches(selector: Any, labels: dict | None) -> bool | None:
    """Does a PDB label selector match these pod labels?

    ``None`` means undetermined — a requirement used an operator we do not
    understand. Undetermined is reported to the caller rather than folded into
    "no match", because a missed match reads as "nothing is blocking the
    drain", which is a confidently wrong answer.

    Selector semantics follow the PodDisruptionBudget API: a *null* selector
    selects no pods, while an *empty* selector selects every pod in the
    namespace.
    """
    if selector is None:
        return False

    match_labels, expressions = _selector_parts(selector)
    if not match_labels and not expressions:
        return True

    pod_labels = labels or {}
    if not all(pod_labels.get(k) == v for k, v in match_labels.items()):
        return False

    undetermined = False
    for expression in expressions:
        result = _expression_matches(expression, pod_labels)
        if result is None:
            undetermined = True
        elif result is False:
            return False
    return None if undetermined else True


async def talos_get_drain_blockers(params: dict) -> str:
    """Explain why a node cannot be drained.

    This is the diagnostic for the most common Talos upgrade stall: a
    PodDisruptionBudget that allows zero disruptions, so eviction can never
    succeed and the upgrade Job simply times out.
    """
    node_name = params["node"]

    try:
        pods = core_api.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}")
    except ApiException as exc:
        return _err(exc, f"pods on node {node_name}")

    try:
        pdbs = policy_api.list_pod_disruption_budget_for_all_namespaces().items
    except ApiException as exc:
        return _err(exc, "poddisruptionbudgets")

    blockers: list[dict[str, Any]] = []
    undetermined: list[dict[str, Any]] = []
    unmanaged: list[str] = []

    for pod in pods.items:
        ns = pod.metadata.namespace
        # Mirror pods and DaemonSet pods are not drained, so they never block.
        owners = pod.metadata.owner_references or []
        if any(o.kind == "DaemonSet" for o in owners):
            continue
        if not owners:
            unmanaged.append(f"{ns}/{pod.metadata.name}")

        for pdb in pdbs:
            if pdb.metadata.namespace != ns:
                continue
            matched = _selector_matches(pdb.spec.selector, pod.metadata.labels)
            entry = {
                "pdb": f"{ns}/{pdb.metadata.name}",
                "pod": f"{ns}/{pod.metadata.name}",
                "disruptions_allowed": pdb.status.disruptions_allowed if pdb.status else None,
                "current_healthy": pdb.status.current_healthy if pdb.status else None,
                "desired_healthy": pdb.status.desired_healthy if pdb.status else None,
                "min_available": pdb.spec.min_available,
                "max_unavailable": pdb.spec.max_unavailable,
            }
            if matched is None:
                undetermined.append({**entry, "note": "PDB uses matchExpressions; not evaluated"})
            elif matched and pdb.status and (pdb.status.disruptions_allowed or 0) == 0:
                blockers.append(entry)

    return json.dumps(
        {
            "node": node_name,
            "drainable": not blockers and not undetermined,
            "blocking_pdbs": blockers,
            "undetermined_pdbs": undetermined,
            "pods_without_a_controller": unmanaged,
            "hint": (
                "A PDB with disruptions_allowed=0 makes eviction impossible, so the "
                "upgrade will time out rather than fail fast. Either scale the workload "
                "up so the budget can be met, or run the Talos upgrade with "
                "--drain=false once you have confirmed the workload tolerates it."
            )
            if blockers
            else None,
        },
        default=str,
    )


# --- skill wiring ---


def _get_tools(config: dict) -> list[ToolDefinition]:
    """Return Talos diagnostic tool definitions, bound to the skill's config."""

    def _bind(handler):
        async def wrapped(params: dict) -> str:
            return await handler({**params, "_config": config})

        return wrapped

    return [
        ToolDefinition(
            name="talos_get_upgrades",
            description=(
                "List tuppr upgrade resources (TalosUpgrade or KubernetesUpgrade) with their"
                " phase, target version, failed nodes and conditions. Start here when an"
                " upgrade is reported stuck or failed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Which upgrade kind to list (default: talos)",
                        "enum": ["talos", "kubernetes"],
                    },
                },
            },
            handler=_bind(talos_get_upgrades),
        ),
        ToolDefinition(
            name="talos_get_upgrade_jobs",
            description=(
                "List the upgrade Jobs in the system-upgrade namespace with their conditions,"
                " backoff limit, failure counts and pod container states. Use this when the"
                " upgrade CR says a node failed but not why — a Job that failed on its own"
                " terms is explained here, not on the CR."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "Namespace holding the upgrade Jobs "
                        f"(default: {DEFAULT_NAMESPACE})",
                    },
                },
            },
            handler=_bind(talos_get_upgrade_jobs),
        ),
        ToolDefinition(
            name="talos_get_nodes",
            description=(
                "Show node readiness, cordon (unschedulable) state, taints — including tuppr's"
                " outdated taint — and OS/kubelet versions. Use it to see which nodes are"
                " already upgraded and whether a failed node was left cordoned or tainted."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "Node name; omit to list all nodes",
                    },
                },
            },
            handler=_bind(talos_get_nodes),
        ),
        ToolDefinition(
            name="talos_get_drain_blockers",
            description=(
                "Explain why a node cannot be drained: which PodDisruptionBudgets currently"
                " allow zero disruptions for pods on it, and which pods have no controller."
                " This is the usual cause of a Talos upgrade that hangs rather than fails"
                " (for example a single-replica database behind a minAvailable:1 PDB)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node name to check"},
                },
                "required": ["node"],
            },
            handler=_bind(talos_get_drain_blockers),
        ),
    ]


def _make_skill() -> SkillDefinition:
    from home_ops_agent.agent.skills import SkillDefinition

    return SkillDefinition(
        id="talos",
        name="Talos Upgrades",
        description=(
            "Read-only diagnostics for Talos and Kubernetes node upgrades driven by tuppr:"
            " upgrade CR status, upgrade Job failures, node taint/cordon state, and why a"
            " node cannot be drained. Diagnoses only — recovery stays manual."
        ),
        builtin=False,
        config_fields=[
            {
                "key": "group",
                "label": "tuppr CRD API group",
                "type": "text",
                "default": DEFAULT_GROUP,
            },
            {
                "key": "version",
                "label": "tuppr CRD version",
                "type": "text",
                "default": DEFAULT_VERSION,
            },
            {
                "key": "namespace",
                "label": "Upgrade jobs namespace",
                "type": "text",
                "default": DEFAULT_NAMESPACE,
            },
        ],
        get_tools=_get_tools,
    )


SKILL: SkillDefinition = _make_skill()
