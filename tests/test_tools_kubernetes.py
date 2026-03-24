"""Tests for agent/tools/kubernetes.py — K8s tools and safety guardrails."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from home_ops_agent.agent.tools.kubernetes import (
    PROTECTED_NAMESPACES,
    _serialize,
    delete_pod,
    describe_resource,
    get_events,
    get_nodes,
    get_pod_logs,
    get_pods,
    restart_deployment,
)

# --- Safety guardrail tests ---


def test_protected_namespaces():
    assert PROTECTED_NAMESPACES == {"kube-system", "flux-system", "cert-manager"}


async def test_restart_deployment_protected_kube_system():
    result = json.loads(await restart_deployment({"name": "coredns", "namespace": "kube-system"}))
    assert "BLOCKED" in result["error"]
    assert "protected namespace" in result["error"]


async def test_restart_deployment_protected_flux_system():
    result = json.loads(
        await restart_deployment({"name": "source-controller", "namespace": "flux-system"})
    )
    assert "BLOCKED" in result["error"]


async def test_restart_deployment_protected_cert_manager():
    result = json.loads(
        await restart_deployment({"name": "cert-manager", "namespace": "cert-manager"})
    )
    assert "BLOCKED" in result["error"]


async def test_delete_pod_protected_namespace():
    result = json.loads(await delete_pod({"name": "pod-1", "namespace": "kube-system"}))
    assert "BLOCKED" in result["error"]
    assert "protected namespace" in result["error"]


async def test_describe_resource_unsupported_kind():
    params = {"kind": "networkpolicy", "name": "deny-all", "namespace": "default"}
    result = json.loads(await describe_resource(params))
    assert "Unsupported kind" in result["error"]


# --- _serialize() tests ---


def test_serialize_datetime():
    dt = datetime(2026, 1, 1, 12, 0, 0)
    assert _serialize(dt) == "2026-01-01T12:00:00"


def test_serialize_string():
    assert _serialize("hello") == "hello"


def test_serialize_dict_passthrough():
    # A plain dict doesn't have to_dict(), so str() is called
    result = _serialize({"key": "val"})
    assert isinstance(result, str)


def test_serialize_object_with_to_dict():
    class FakeK8sObj:
        def to_dict(self):
            return {"name": "pod-1", "status": "Running"}

    result = _serialize(FakeK8sObj())
    assert result == {"name": "pod-1", "status": "Running"}


# --- Restart with unsupported kind ---


async def test_restart_unsupported_kind():
    result = json.loads(
        await restart_deployment({"name": "test", "namespace": "default", "kind": "cronjob"})
    )
    assert "Cannot restart kind" in result["error"]


# --- Mocked K8s API call tests ---
# Use SimpleNamespace instead of MagicMock to avoid infinite mock chains
# that cause memory blowup when json.dumps traverses attributes.


def _mock_pod(name="test-pod", namespace="default", phase="Running", node="node-1"):
    """Create a mock pod object matching the K8s API structure."""
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            creation_timestamp=datetime(2026, 1, 1, 12, 0, 0),
        ),
        status=SimpleNamespace(
            phase=phase,
            container_statuses=[
                SimpleNamespace(name="app", ready=True, restart_count=0, state="running")
            ],
        ),
        spec=SimpleNamespace(node_name=node),
    )


def _mock_event(type_="Normal", reason="Scheduled", message="Successfully assigned"):
    return SimpleNamespace(
        type=type_,
        reason=reason,
        message=message,
        involved_object=SimpleNamespace(kind="Pod", name="test-pod"),
        count=1,
        last_timestamp=datetime(2026, 1, 1, 12, 0, 0),
        metadata=SimpleNamespace(creation_timestamp=datetime(2026, 1, 1, 12, 0, 0)),
    )


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_pods_success(mock_core):
    pod = _mock_pod()
    mock_core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

    result = json.loads(await get_pods({"namespace": "default"}))
    assert len(result) == 1
    assert result[0]["name"] == "test-pod"
    assert result[0]["phase"] == "Running"
    assert result[0]["node"] == "node-1"
    mock_core.list_namespaced_pod.assert_called_once_with(namespace="default", label_selector="")


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_pods_with_label_selector(mock_core):
    pod = _mock_pod(name="nginx-1")
    mock_core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

    result = json.loads(await get_pods({"namespace": "web", "label_selector": "app=nginx"}))
    assert result[0]["name"] == "nginx-1"
    mock_core.list_namespaced_pod.assert_called_once_with(
        namespace="web", label_selector="app=nginx"
    )


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_pods_empty(mock_core):
    mock_core.list_namespaced_pod.return_value = SimpleNamespace(items=[])

    result = json.loads(await get_pods({"namespace": "default"}))
    assert result == []


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_pods_api_error(mock_core):
    from home_ops_agent.agent.tools.kubernetes import ApiException

    mock_core.list_namespaced_pod.side_effect = ApiException(reason="Forbidden")

    result = json.loads(await get_pods({"namespace": "default"}))
    assert "error" in result
    assert "Failed to list pods" in result["error"]


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_pod_logs_success(mock_core):
    mock_core.read_namespaced_pod_log.return_value = "2026-01-01 error: something broke"

    result = await get_pod_logs({"namespace": "default", "pod_name": "test-pod"})
    assert "something broke" in result
    mock_core.read_namespaced_pod_log.assert_called_once_with(
        name="test-pod", namespace="default", container=None, tail_lines=100
    )


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_pod_logs_empty(mock_core):
    mock_core.read_namespaced_pod_log.return_value = ""

    result = await get_pod_logs({"namespace": "default", "pod_name": "test-pod"})
    assert result == "(no logs)"


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_pod_logs_with_container(mock_core):
    mock_core.read_namespaced_pod_log.return_value = "log output"

    await get_pod_logs(
        {
            "namespace": "default",
            "pod_name": "test-pod",
            "container": "sidecar",
            "tail_lines": 50,
        }
    )
    mock_core.read_namespaced_pod_log.assert_called_once_with(
        name="test-pod", namespace="default", container="sidecar", tail_lines=50
    )


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_pod_logs_api_error(mock_core):
    from home_ops_agent.agent.tools.kubernetes import ApiException

    mock_core.read_namespaced_pod_log.side_effect = ApiException(reason="NotFound")

    result = json.loads(await get_pod_logs({"namespace": "default", "pod_name": "missing-pod"}))
    assert "error" in result
    assert "Failed to get logs" in result["error"]


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_events_success(mock_core):
    event = _mock_event()
    mock_core.list_namespaced_event.return_value = SimpleNamespace(items=[event])

    result = json.loads(await get_events({"namespace": "default"}))
    assert len(result) == 1
    assert result[0]["reason"] == "Scheduled"
    assert result[0]["object"] == "Pod/test-pod"


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_events_with_resource_filter(mock_core):
    event = _mock_event()
    mock_core.list_namespaced_event.return_value = SimpleNamespace(items=[event])

    await get_events({"namespace": "default", "resource_name": "test-pod"})
    mock_core.list_namespaced_event.assert_called_once_with(
        namespace="default", field_selector="involvedObject.name=test-pod"
    )


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_events_api_error(mock_core):
    from home_ops_agent.agent.tools.kubernetes import ApiException

    mock_core.list_namespaced_event.side_effect = ApiException(reason="Forbidden")

    result = json.loads(await get_events({"namespace": "default"}))
    assert "error" in result


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_get_nodes_success(mock_core):
    node = SimpleNamespace(
        metadata=SimpleNamespace(name="node-1"),
        status=SimpleNamespace(
            conditions=[SimpleNamespace(type="Ready", status="True")],
            capacity={"cpu": "4", "memory": "16Gi", "pods": "110"},
            allocatable={"cpu": "3800m", "memory": "15Gi", "pods": "110"},
        ),
    )
    mock_core.list_node.return_value = SimpleNamespace(items=[node])

    result = json.loads(await get_nodes({}))
    assert len(result) == 1
    assert result[0]["name"] == "node-1"
    assert result[0]["conditions"] == {"Ready": "True"}
    assert result[0]["capacity"]["cpu"] == "4"


@patch("home_ops_agent.agent.tools.kubernetes.apps_v1")
async def test_restart_deployment_success(mock_apps):
    result = json.loads(await restart_deployment({"name": "nginx", "namespace": "default"}))
    assert result["status"] == "ok"
    assert "nginx" in result["message"]
    mock_apps.patch_namespaced_deployment.assert_called_once()


@patch("home_ops_agent.agent.tools.kubernetes.apps_v1")
async def test_restart_statefulset_success(mock_apps):
    result = json.loads(
        await restart_deployment({"name": "redis", "namespace": "default", "kind": "statefulset"})
    )
    assert result["status"] == "ok"
    assert "redis" in result["message"]
    mock_apps.patch_namespaced_stateful_set.assert_called_once()


@patch("home_ops_agent.agent.tools.kubernetes.apps_v1")
async def test_restart_deployment_api_error(mock_apps):
    from home_ops_agent.agent.tools.kubernetes import ApiException

    mock_apps.patch_namespaced_deployment.side_effect = ApiException(reason="Conflict")

    result = json.loads(await restart_deployment({"name": "nginx", "namespace": "default"}))
    assert "error" in result
    assert "Failed to restart" in result["error"]


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_delete_pod_success(mock_core):
    result = json.loads(await delete_pod({"name": "pod-1", "namespace": "default"}))
    assert result["status"] == "ok"
    assert "pod-1" in result["message"]
    mock_core.delete_namespaced_pod.assert_called_once_with("pod-1", "default")


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_delete_pod_api_error(mock_core):
    from home_ops_agent.agent.tools.kubernetes import ApiException

    mock_core.delete_namespaced_pod.side_effect = ApiException(reason="NotFound")

    result = json.loads(await delete_pod({"name": "pod-1", "namespace": "default"}))
    assert "error" in result
    assert "Failed to delete pod" in result["error"]


class _FakeK8sResource:
    """Minimal K8s resource mock with to_dict() for _serialize()."""

    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


@patch("home_ops_agent.agent.tools.kubernetes.apps_v1")
async def test_describe_deployment_success(mock_apps):
    mock_apps.read_namespaced_deployment.return_value = _FakeK8sResource(
        {"metadata": {"name": "nginx"}, "spec": {"replicas": 3}}
    )

    result = json.loads(
        await describe_resource({"kind": "deployment", "name": "nginx", "namespace": "default"})
    )
    assert result["metadata"]["name"] == "nginx"


@patch("home_ops_agent.agent.tools.kubernetes.core_v1")
async def test_describe_pod_success(mock_core):
    mock_core.read_namespaced_pod.return_value = _FakeK8sResource(
        {"metadata": {"name": "test-pod"}, "status": {"phase": "Running"}}
    )

    result = json.loads(
        await describe_resource({"kind": "pod", "name": "test-pod", "namespace": "default"})
    )
    assert result["metadata"]["name"] == "test-pod"
