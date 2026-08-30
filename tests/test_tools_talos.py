"""Tests for the read-only Talos/tuppr diagnostics skill."""

import json
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException

from home_ops_agent.agent.tools import talos


def _ns(**kw):
    return SimpleNamespace(**kw)


# --- upgrade CRs ---


async def test_get_upgrades_surfaces_phase_and_failed_nodes(monkeypatch):
    monkeypatch.setattr(
        talos.custom_api,
        "list_cluster_custom_object",
        lambda **kw: {
            "items": [
                {
                    "metadata": {"name": "talos", "creationTimestamp": "2026-08-30T10:00:00Z"},
                    "spec": {"talos": {"version": "v1.12.6"}},
                    "status": {
                        "phase": "Failed",
                        "failedNodes": ["k8s-1"],
                        "conditions": [
                            {
                                "type": "Ready",
                                "status": "False",
                                "reason": "JobFailed",
                                "message": "upgrade job failed",
                            }
                        ],
                    },
                }
            ]
        },
    )

    result = json.loads(await talos.talos_get_upgrades({}))

    assert result["count"] == 1
    item = result["items"][0]
    assert item["phase"] == "Failed"
    assert item["failed_nodes"] == ["k8s-1"]
    assert item["target_version"] == "v1.12.6"
    assert item["conditions"][0]["reason"] == "JobFailed"


async def test_get_upgrades_selects_kubernetes_plural(monkeypatch):
    seen = {}

    def _list(**kw):
        seen.update(kw)
        return {"items": []}

    monkeypatch.setattr(talos.custom_api, "list_cluster_custom_object", _list)

    await talos.talos_get_upgrades({"kind": "kubernetes"})
    assert seen["plural"] == "kubernetesupgrades"


async def test_get_upgrades_uses_configured_group(monkeypatch):
    seen = {}

    def _list(**kw):
        seen.update(kw)
        return {"items": []}

    monkeypatch.setattr(talos.custom_api, "list_cluster_custom_object", _list)

    tools = talos._get_tools({"group": "custom.example.com", "version": "v1"})
    upgrades = next(t for t in tools if t.name == "talos_get_upgrades")
    await upgrades.handler({})

    assert seen["group"] == "custom.example.com"
    assert seen["version"] == "v1"


async def test_missing_crd_explains_itself(monkeypatch):
    def _raise(**kw):
        raise ApiException(status=404, reason="Not Found")

    monkeypatch.setattr(talos.custom_api, "list_cluster_custom_object", _raise)

    result = json.loads(await talos.talos_get_upgrades({}))
    assert "CRD may not be installed" in result["error"]


async def test_forbidden_names_the_rbac_gap(monkeypatch):
    def _raise(**kw):
        raise ApiException(status=403, reason="Forbidden")

    monkeypatch.setattr(talos.custom_api, "list_cluster_custom_object", _raise)

    result = json.loads(await talos.talos_get_upgrades({}))
    assert "ClusterRole" in result["error"]


# --- upgrade jobs ---


async def test_get_upgrade_jobs_reports_backoff_and_conditions(monkeypatch):
    """The tuppr 0.5.0 race showed up as backoffLimit 0 with a failed condition."""
    job = _ns(
        metadata=_ns(name="talos-upgrade-k8s-1", creation_timestamp="2026-08-30T10:00:00Z"),
        spec=_ns(backoff_limit=0),
        status=_ns(
            active=0,
            succeeded=0,
            failed=0,
            conditions=[
                _ns(
                    type="Failed",
                    status="True",
                    reason="BackoffLimitExceeded",
                    message="Job has reached the specified backoff limit",
                    last_transition_time="2026-08-30T10:05:00Z",
                )
            ],
        ),
    )
    monkeypatch.setattr(talos.batch_api, "list_namespaced_job", lambda **kw: _ns(items=[job]))
    monkeypatch.setattr(talos.core_api, "list_namespaced_pod", lambda **kw: _ns(items=[]))

    result = json.loads(await talos.talos_get_upgrade_jobs({}))

    entry = result["jobs"][0]
    assert entry["backoff_limit"] == 0
    assert entry["failed"] == 0
    assert entry["conditions"][0]["reason"] == "BackoffLimitExceeded"


async def test_get_upgrade_jobs_includes_pod_container_state(monkeypatch):
    job = _ns(
        metadata=_ns(name="j", creation_timestamp=None),
        spec=_ns(backoff_limit=0),
        status=_ns(active=1, succeeded=0, failed=0, conditions=[]),
    )
    pod = _ns(
        metadata=_ns(name="j-abc"),
        spec=_ns(node_name="k8s-1"),
        status=_ns(
            phase="Pending",
            reason=None,
            message=None,
            container_statuses=[
                _ns(
                    name="upgrade",
                    ready=False,
                    restart_count=0,
                    state=_ns(waiting=_ns(reason="ImagePullBackOff"), terminated=None),
                )
            ],
        ),
    )
    monkeypatch.setattr(talos.batch_api, "list_namespaced_job", lambda **kw: _ns(items=[job]))
    monkeypatch.setattr(talos.core_api, "list_namespaced_pod", lambda **kw: _ns(items=[pod]))

    result = json.loads(await talos.talos_get_upgrade_jobs({}))

    state = result["jobs"][0]["pods"][0]["container_states"][0]
    assert state["waiting"] == "ImagePullBackOff"


# --- nodes ---


async def test_get_nodes_calls_out_upgrade_taints_and_cordon(monkeypatch):
    node = _ns(
        metadata=_ns(name="k8s-1"),
        spec=_ns(
            unschedulable=True,
            taints=[
                _ns(key="tuppr.home-operations.com/outdated", value=None, effect="NoSchedule"),
                _ns(key="node.kubernetes.io/unreachable", value=None, effect="NoExecute"),
            ],
        ),
        status=_ns(
            conditions=[_ns(type="Ready", status="True", reason=None)],
            node_info=_ns(os_image="Talos (v1.12.5)", kubelet_version="v1.34.1"),
        ),
    )
    monkeypatch.setattr(talos.core_api, "list_node", lambda **kw: _ns(items=[node]))

    result = json.loads(await talos.talos_get_nodes({}))

    entry = result["nodes"][0]
    assert entry["unschedulable"] is True
    assert entry["upgrade_taints"] == ["tuppr.home-operations.com/outdated"]
    assert entry["os_image"] == "Talos (v1.12.5)"
    assert len(entry["taints"]) == 2


# --- drain blockers: the failure that actually bit ---


def _pdb(name, ns, match_labels, disruptions_allowed, min_available=None, expressions=None):
    selector = {}
    if match_labels:
        selector["matchLabels"] = match_labels
    if expressions:
        selector["matchExpressions"] = expressions
    return _ns(
        metadata=_ns(name=name, namespace=ns),
        spec=_ns(
            selector=_ns(to_dict=lambda s=selector: s),
            min_available=min_available,
            max_unavailable=None,
        ),
        status=_ns(disruptions_allowed=disruptions_allowed, current_healthy=1, desired_healthy=1),
    )


def _pod(name, ns, labels, owner_kind="ReplicaSet"):
    owners = [_ns(kind=owner_kind)] if owner_kind else []
    return _ns(metadata=_ns(name=name, namespace=ns, labels=labels, owner_references=owners))


async def test_drain_blockers_finds_the_cnpg_pdb(monkeypatch):
    """The exact shape of failure 1: minAvailable:1 in front of one replica."""
    pod = _pod("postgres-1", "database", {"cnpg.io/cluster": "postgres", "role": "primary"})
    pdb = _pdb("postgres-primary", "database", {"role": "primary"}, 0, min_available=1)

    monkeypatch.setattr(
        talos.core_api, "list_pod_for_all_namespaces", lambda **kw: _ns(items=[pod])
    )
    monkeypatch.setattr(
        talos.policy_api,
        "list_pod_disruption_budget_for_all_namespaces",
        lambda **kw: _ns(items=[pdb]),
    )

    result = json.loads(await talos.talos_get_drain_blockers({"node": "k8s-1"}))

    assert result["drainable"] is False
    assert result["blocking_pdbs"][0]["pdb"] == "database/postgres-primary"
    assert result["blocking_pdbs"][0]["disruptions_allowed"] == 0
    assert "--drain=false" in result["hint"]


async def test_drain_blockers_ignores_healthy_pdbs(monkeypatch):
    pod = _pod("web-1", "default", {"app": "web"})
    pdb = _pdb("web", "default", {"app": "web"}, 2, min_available=1)

    monkeypatch.setattr(
        talos.core_api, "list_pod_for_all_namespaces", lambda **kw: _ns(items=[pod])
    )
    monkeypatch.setattr(
        talos.policy_api,
        "list_pod_disruption_budget_for_all_namespaces",
        lambda **kw: _ns(items=[pdb]),
    )

    result = json.loads(await talos.talos_get_drain_blockers({"node": "k8s-1"}))
    assert result["drainable"] is True
    assert result["blocking_pdbs"] == []


async def test_drain_blockers_ignores_other_namespaces(monkeypatch):
    """A same-labelled PDB in another namespace must not be attributed."""
    pod = _pod("web-1", "default", {"app": "web"})
    pdb = _pdb("web", "other", {"app": "web"}, 0, min_available=1)

    monkeypatch.setattr(
        talos.core_api, "list_pod_for_all_namespaces", lambda **kw: _ns(items=[pod])
    )
    monkeypatch.setattr(
        talos.policy_api,
        "list_pod_disruption_budget_for_all_namespaces",
        lambda **kw: _ns(items=[pdb]),
    )

    result = json.loads(await talos.talos_get_drain_blockers({"node": "k8s-1"}))
    assert result["blocking_pdbs"] == []


async def test_drain_blockers_skips_daemonset_pods(monkeypatch):
    """DaemonSet pods are never drained, so they cannot block one."""
    pod = _pod("csi-node-1", "kube-system", {"app": "csi"}, owner_kind="DaemonSet")
    pdb = _pdb("csi", "kube-system", {"app": "csi"}, 0, min_available=1)

    monkeypatch.setattr(
        talos.core_api, "list_pod_for_all_namespaces", lambda **kw: _ns(items=[pod])
    )
    monkeypatch.setattr(
        talos.policy_api,
        "list_pod_disruption_budget_for_all_namespaces",
        lambda **kw: _ns(items=[pdb]),
    )

    result = json.loads(await talos.talos_get_drain_blockers({"node": "k8s-1"}))
    assert result["blocking_pdbs"] == []
    assert result["drainable"] is True


async def test_drain_blockers_reports_unevaluated_selectors(monkeypatch):
    """A matchExpressions PDB must never be silently read as 'no blocker'."""
    pod = _pod("app-1", "default", {"app": "x"})
    pdb = _pdb(
        "expr",
        "default",
        None,
        0,
        expressions=[{"key": "app", "operator": "In", "values": ["x"]}],
    )

    monkeypatch.setattr(
        talos.core_api, "list_pod_for_all_namespaces", lambda **kw: _ns(items=[pod])
    )
    monkeypatch.setattr(
        talos.policy_api,
        "list_pod_disruption_budget_for_all_namespaces",
        lambda **kw: _ns(items=[pdb]),
    )

    result = json.loads(await talos.talos_get_drain_blockers({"node": "k8s-1"}))

    assert result["drainable"] is False
    assert result["undetermined_pdbs"][0]["pdb"] == "default/expr"


async def test_drain_blockers_flags_uncontrolled_pods(monkeypatch):
    pod = _pod("bare", "default", {"app": "x"}, owner_kind=None)

    monkeypatch.setattr(
        talos.core_api, "list_pod_for_all_namespaces", lambda **kw: _ns(items=[pod])
    )
    monkeypatch.setattr(
        talos.policy_api,
        "list_pod_disruption_budget_for_all_namespaces",
        lambda **kw: _ns(items=[]),
    )

    result = json.loads(await talos.talos_get_drain_blockers({"node": "k8s-1"}))
    assert result["pods_without_a_controller"] == ["default/bare"]


def test_selector_matching_rules():
    assert talos._selector_matches({"matchLabels": {"a": "1"}}, {"a": "1", "b": "2"}) is True
    assert talos._selector_matches({"matchLabels": {"a": "1"}}, {"a": "2"}) is False
    assert talos._selector_matches({"matchLabels": {"a": "1"}}, None) is False
    assert talos._selector_matches(None, {"a": "1"}) is False
    # Unevaluated rather than guessed.
    assert talos._selector_matches({"matchExpressions": [{"key": "a"}]}, {"a": "1"}) is None


# --- skill wiring ---


def test_skill_is_optional_and_read_only():
    tools = talos.SKILL.get_tools({})
    names = sorted(t.name for t in tools)

    assert talos.SKILL.id == "talos"
    assert talos.SKILL.builtin is False
    assert names == [
        "talos_get_drain_blockers",
        "talos_get_nodes",
        "talos_get_upgrade_jobs",
        "talos_get_upgrades",
    ]
    # No tool here may mutate cluster state — recovery stays manual.
    assert not any(
        verb in t.name for t in tools for verb in ("upgrade_node", "uncordon", "delete", "taint")
    )


def test_skill_is_registered():
    from home_ops_agent.agent.skills import init_registry, registry

    init_registry()
    assert registry.get("talos") is not None


@pytest.mark.parametrize("field", ["group", "version", "namespace"])
def test_skill_exposes_config_fields(field):
    keys = {f["key"] for f in talos.SKILL.config_fields}
    assert field in keys
