"""Tests for the scheduled cluster health check.

The failure this worker exists for was silent: a Talos upgrade left a node
cordoned for three hours with six pods stranded, and nothing notified, because
ntfy, alertmanager and gatus are all pinned to that same node by local-path
PVCs. These pin the two properties that make the check worth having — that it
notifies on transitions rather than every cycle, and that it explains *why* pods
are Pending rather than just counting them.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from home_ops_agent.workers import health_check
from home_ops_agent.workers.health_check import (
    DEGRADED,
    WARNING,
    Finding,
    HealthState,
    format_message,
)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Each test starts from a clean transition state."""
    health_check._last_notified_at = None
    health_check._last_healthy = None
    health_check._degraded_since = None
    yield
    health_check._last_notified_at = None
    health_check._last_healthy = None
    health_check._degraded_since = None


# --- fake Kubernetes objects -------------------------------------------------


@dataclass
class _Meta:
    name: str
    namespace: str = "default"
    creation_timestamp: datetime | None = None


def _node(name, ready="True", unschedulable=False, taints=()):
    node = MagicMock()
    node.metadata = _Meta(name=name)
    node.spec.unschedulable = unschedulable
    node.spec.taints = [MagicMock(key=k, value=None, effect="NoSchedule") for k in taints]
    node.status.conditions = [MagicMock(type="Ready", status=ready, reason=None)]
    return node


def _pod(name, namespace, phase="Running", age_minutes=0, pvc=None):
    pod = MagicMock()
    pod.metadata = _Meta(
        name=name,
        namespace=namespace,
        creation_timestamp=datetime.now(UTC) - timedelta(minutes=age_minutes),
    )
    pod.status.phase = phase
    pod.status.container_statuses = []
    if pvc:
        volume = MagicMock()
        volume.persistent_volume_claim.claim_name = pvc
        pod.spec.volumes = [volume]
    else:
        pod.spec.volumes = []
    return pod


# --- the correlation, which is the whole point -------------------------------


def test_pending_pod_is_explained_by_the_node_it_is_pinned_to():
    """A Pending pod must name the cordoned node its PVC binds it to.

    "6 pods Pending" is a symptom list. "6 pods Pending because they are pinned
    to a node that is cordoned" is a diagnosis, and only the second one saves
    anyone the twenty minutes of clicking.
    """
    pins = {("monitoring", "ntfy"): "k8s-1"}
    pods = [_pod("ntfy-abc", "monitoring", phase="Pending", age_minutes=30, pvc="ntfy")]

    with patch.object(health_check.core_api, "list_pod_for_all_namespaces") as listed:
        listed.return_value.items = pods
        findings = health_check._check_pods(10, pins, {"k8s-1"})

    assert len(findings) == 1
    assert findings[0].severity == DEGRADED
    assert "k8s-1" in findings[0].cause
    assert "unschedulable" in findings[0].cause
    assert any("pinned to k8s-1" in d for d in findings[0].detail)


def test_pending_pod_on_a_healthy_node_gets_no_false_cause():
    """Only blame the node when that node is actually unhealthy."""
    pins = {("monitoring", "ntfy"): "k8s-1"}
    pods = [_pod("ntfy-abc", "monitoring", phase="Pending", age_minutes=30, pvc="ntfy")]

    with patch.object(health_check.core_api, "list_pod_for_all_namespaces") as listed:
        listed.return_value.items = pods
        findings = health_check._check_pods(10, pins, set())  # no unhealthy nodes

    assert findings[0].cause is None


def test_recently_pending_pods_are_ignored():
    """A pod Pending for seconds is scheduling, not an incident."""
    pods = [_pod("x", "default", phase="Pending", age_minutes=1)]

    with patch.object(health_check.core_api, "list_pod_for_all_namespaces") as listed:
        listed.return_value.items = pods
        assert health_check._check_pods(10, {}, set()) == []


def test_missing_pv_permission_degrades_the_report_not_the_check():
    """A 403 on persistentvolumes loses the correlation, never the finding."""
    from kubernetes.client.rest import ApiException

    with patch.object(health_check.core_api, "list_persistent_volume") as listed:
        listed.side_effect = ApiException(status=403, reason="Forbidden")
        assert health_check._node_pin_map() == {}


# --- node state --------------------------------------------------------------


def test_cordoned_node_is_degraded_and_names_its_upgrade_taint():
    with patch.object(health_check.core_api, "list_node") as listed:
        listed.return_value.items = [
            _node("k8s-0"),
            _node("k8s-1", unschedulable=True, taints=["tuppr.home-operations.com/outdated"]),
        ]
        findings, bad = health_check._check_nodes()

    assert bad == {"k8s-1"}
    assert findings[0].severity == DEGRADED
    assert "tuppr.home-operations.com/outdated" in findings[0].detail[0]


def test_all_nodes_healthy_produces_no_findings():
    with patch.object(health_check.core_api, "list_node") as listed:
        listed.return_value.items = [_node("k8s-0"), _node("k8s-1")]
        findings, bad = health_check._check_nodes()

    assert findings == []
    assert bad == set()


# --- Flux's Ready condition is three-state ------------------------------------


def _flux(name, namespace, status, message, age_minutes):
    """A Flux resource whose Ready condition has held `status` for `age_minutes`."""
    stamp = (datetime.now(UTC) - timedelta(minutes=age_minutes)).isoformat().replace("+00:00", "Z")
    return {
        "metadata": {"name": name, "namespace": namespace},
        "status": {
            "conditions": [
                {
                    "type": "Ready",
                    "status": status,
                    "message": message,
                    "lastTransitionTime": stamp,
                }
            ]
        },
    }


def test_helmrelease_mid_upgrade_is_not_an_outage():
    """The regression this check shipped with.

    Its very first cycle pushed "Cluster degraded — automation/home-ops-agent:
    Running 'upgrade' action" about its own rollout. Ready=Unknown means Flux is
    reconciling, not that anything is broken, and with Renovate auto-merging
    that state is reachable most days.
    """
    items = [_flux("home-ops-agent", "automation", "Unknown", "Running 'upgrade' action", 0)]
    assert health_check._not_ready(items) == []


def test_reconcile_that_never_finishes_is_reported_as_stuck():
    """The other side of it: Unknown forever is a real problem."""
    items = [_flux("some-app", "media", "Unknown", "Running 'upgrade' action", 45)]

    (line,) = health_check._not_ready(items)
    assert "media/some-app" in line
    assert "stuck reconciling for 45m" in line


def test_brief_failure_is_left_for_flux_to_retry():
    items = [_flux("some-app", "media", "False", "upgrade retries exhausted", 1)]
    assert health_check._not_ready(items) == []


def test_failure_that_outlives_the_retry_window_is_reported():
    items = [_flux("some-app", "media", "False", "upgrade retries exhausted", 30)]

    (line,) = health_check._not_ready(items)
    assert "media/some-app" in line
    assert "retries exhausted" in line
    assert "stuck reconciling" not in line, "a failure is not a slow reconcile"


def test_ready_resources_are_never_reported():
    items = [_flux("fine", "default", "True", "Release reconciliation succeeded", 0)]
    assert health_check._not_ready(items) == []


def test_missing_transition_time_is_reported_rather_than_assumed_fresh():
    """Staying quiet on the unknown case is the failure mode this worker exists
    to avoid, so an un-timestamped condition counts."""
    items = [
        {
            "metadata": {"name": "odd", "namespace": "default"},
            "status": {"conditions": [{"type": "Ready", "status": "False", "message": "boom"}]},
        }
    ]
    assert len(health_check._not_ready(items)) == 1


# --- notification transitions ------------------------------------------------


@pytest.mark.asyncio
async def test_notifies_once_on_becoming_degraded_then_stays_quiet():
    """A degraded cluster sends one push, not one per cycle."""
    state = HealthState(healthy=False, findings=[Finding(DEGRADED, "nodes", "k8s-1 cordoned")])

    with (
        patch.object(health_check, "_is_enabled", AsyncMock(return_value=True)),
        patch.object(health_check, "collect", AsyncMock(return_value=state)),
        patch.object(health_check, "_setting_int", AsyncMock(return_value=99999)),
        patch.object(health_check.notifications, "notify", AsyncMock()) as notify,
    ):
        await health_check.check_health()
        assert notify.await_count == 1

        await health_check.check_health()
        await health_check.check_health()
        assert notify.await_count == 1, "still-degraded cycles must not re-notify"


@pytest.mark.asyncio
async def test_degraded_uses_attention_so_no_level_can_silence_it():
    """This is the notification that was missing for three hours; it must not be
    suppressible by the notify_level setting."""
    from home_ops_agent.workers import notifications

    state = HealthState(healthy=False, findings=[Finding(DEGRADED, "nodes", "k8s-1 cordoned")])

    with (
        patch.object(health_check, "_is_enabled", AsyncMock(return_value=True)),
        patch.object(health_check, "collect", AsyncMock(return_value=state)),
        patch.object(health_check, "_setting_int", AsyncMock(return_value=99999)),
        patch.object(health_check.notifications, "notify", AsyncMock()) as notify,
    ):
        await health_check.check_health()

    kind = notify.await_args.args[0]
    assert kind == notifications.ATTENTION
    for level in notifications.LEVELS:
        assert notifications.should_send(kind, level), level


@pytest.mark.asyncio
async def test_recovery_notifies_once():
    healthy = HealthState(healthy=True, findings=[])
    degraded = HealthState(healthy=False, findings=[Finding(DEGRADED, "nodes", "down")])

    with (
        patch.object(health_check, "_is_enabled", AsyncMock(return_value=True)),
        patch.object(health_check, "_setting_int", AsyncMock(return_value=99999)),
        patch.object(health_check.notifications, "notify", AsyncMock()) as notify,
    ):
        with patch.object(health_check, "collect", AsyncMock(return_value=degraded)):
            await health_check.check_health()
        with patch.object(health_check, "collect", AsyncMock(return_value=healthy)):
            await health_check.check_health()
            await health_check.check_health()

    assert notify.await_count == 2
    assert "recovered" in notify.await_args.args[1]["title"].lower()


@pytest.mark.asyncio
async def test_healthy_from_the_start_never_notifies():
    """No push on startup just because the worker began running."""
    state = HealthState(healthy=True, findings=[])

    with (
        patch.object(health_check, "_is_enabled", AsyncMock(return_value=True)),
        patch.object(health_check, "collect", AsyncMock(return_value=state)),
        patch.object(health_check, "_setting_int", AsyncMock(return_value=99999)),
        patch.object(health_check.notifications, "notify", AsyncMock()) as notify,
    ):
        await health_check.check_health()
        await health_check.check_health()

    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_still_degraded_nags_once_the_renag_window_passes():
    state = HealthState(healthy=False, findings=[Finding(DEGRADED, "nodes", "down")])

    with (
        patch.object(health_check, "_is_enabled", AsyncMock(return_value=True)),
        patch.object(health_check, "collect", AsyncMock(return_value=state)),
        patch.object(health_check, "_setting_int", AsyncMock(return_value=0)),  # nag immediately
        patch.object(health_check.notifications, "notify", AsyncMock()) as notify,
    ):
        await health_check.check_health()
        await health_check.check_health()

    assert notify.await_count == 2


@pytest.mark.asyncio
async def test_kill_switch_stops_the_check():
    with (
        patch.object(health_check, "_is_enabled", AsyncMock(return_value=False)),
        patch.object(health_check.notifications, "notify", AsyncMock()) as notify,
    ):
        assert (await health_check.check_health())["status"] == "disabled"

    notify.assert_not_awaited()


# --- warnings are not degradation --------------------------------------------


@pytest.mark.asyncio
async def test_warning_only_findings_do_not_mark_the_cluster_degraded():
    """A stale backup is worth reporting but is not an outage."""
    state = HealthState(healthy=True, findings=[Finding(WARNING, "backup", "stale")])

    with (
        patch.object(health_check, "_is_enabled", AsyncMock(return_value=True)),
        patch.object(health_check, "collect", AsyncMock(return_value=state)),
        patch.object(health_check, "_setting_int", AsyncMock(return_value=99999)),
        patch.object(health_check.notifications, "notify", AsyncMock()) as notify,
    ):
        result = await health_check.check_health()

    assert result["healthy"] is True
    assert result["degraded"] == 0
    notify.assert_not_awaited()


# --- message shape -----------------------------------------------------------


def test_message_puts_cause_before_effect():
    """The upgrade failure must be printed above the pods it stranded — reading
    it the other way round is what costs the twenty minutes."""
    state = HealthState(
        healthy=False,
        findings=[
            Finding(DEGRADED, "pods", "6 pods Pending", cause="pinned to k8s-1"),
            Finding(DEGRADED, "upgrade", "talosupgrade 'talos' is in phase Failed"),
        ],
    )

    body = format_message(state)
    assert body.index("phase Failed") < body.index("6 pods Pending")
    assert "→ pinned to k8s-1" in body


def test_message_reports_a_collection_failure_plainly():
    state = HealthState(healthy=False, findings=[], error="connection refused")
    assert "connection refused" in format_message(state)
