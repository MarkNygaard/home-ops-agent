"""Tests for agent/tools/kubernetes.py — K8s tools and safety guardrails."""

import json
from datetime import datetime

from home_ops_agent.agent.tools.kubernetes import (
    PROTECTED_NAMESPACES,
    _serialize,
    delete_pod,
    describe_resource,
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
