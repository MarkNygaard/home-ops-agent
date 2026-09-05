"""Scheduled cluster health check — notices degradation nobody is alerting on.

A failed Talos upgrade once left a node cordoned for three hours with six pods
stranded and **no notification fired at all**. The reason is worth stating
plainly, because it is the thing this worker exists to route around: the
alerting stack shares a failure domain with the failures it reports. ntfy,
alertmanager and gatus each sit behind a local-path PVC, so whichever node they
are pinned to takes all three down with it. Prometheus was healthy and had
nothing to say, because from its point of view nothing was firing.

This worker is deliberately *not* another alert rule. Two properties make it
different:

**It is stateless.** Every cycle recomputes the whole picture from live cluster
state and keeps no progress of its own. The agent has no PVC and reschedules
freely, so it survives the node reboots it is watching for — but only if a
restart mid-incident costs nothing. Anything it remembered would be exactly the
thing that vanishes when it moves.

**It correlates rather than lists.** "Six pods Pending" is a symptom list.
"Six pods Pending because they are pinned by local-path PVCs to a node that is
cordoned because a Talos upgrade failed" is a diagnosis, and only the second one
saves anyone the twenty minutes of clicking. Resolving a Pending pod through its
PVC to the PV's ``nodeAffinity`` is what buys that, and is why this needs
``persistentvolumes`` read access.

It reports and never acts. Recovery from a stuck upgrade means
``talosctl upgrade --drain=false`` against a node, which needs a talosconfig and
is not reversible the way a pod restart is — the same reasoning that keeps
``agent/tools/talos.py`` read-only applies here.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from sqlalchemy import select

from home_ops_agent.config import settings
from home_ops_agent.database import Setting, async_session
from home_ops_agent.workers import notifications

logger = logging.getLogger(__name__)

try:
    config.load_incluster_config()
except config.ConfigException:
    try:
        config.load_kube_config()
    except config.ConfigException:
        pass

core_api = client.CoreV1Api()
custom_api = client.CustomObjectsApi()

FLUX_GROUP_KUSTOMIZE = "kustomize.toolkit.fluxcd.io"
FLUX_GROUP_HELM = "helm.toolkit.fluxcd.io"
TUPPR_GROUP = "tuppr.home-operations.com"
CNPG_GROUP = "postgresql.cnpg.io"
VOLSYNC_GROUP = "volsync.backube"

# Re-send a still-degraded summary at most this often, so a long incident nags
# rather than either spamming every cycle or going quiet after the first push.
DEFAULT_RENAG_SECONDS = 4 * 60 * 60

# How long a Flux resource must stay un-Ready before it counts as a problem.
# A reconcile in flight is normal and says nothing about cluster health, so
# `Unknown` gets the longer window — Flux's own HelmRelease upgrade timeout is
# 5m, and anything still going at 15m is genuinely wedged rather than working.
# `False` is a real failure, but Flux retries, so give a short window for one
# that clears on its own before treating it as worth a push.
FLUX_RECONCILING_GRACE = timedelta(minutes=15)
FLUX_FAILED_GRACE = timedelta(minutes=5)

# Severity ordering for the summary line.
DEGRADED = "degraded"
WARNING = "warning"


@dataclass
class Finding:
    """One thing that is wrong, with enough context to act on it."""

    severity: str
    area: str
    summary: str
    detail: list[str] = field(default_factory=list)
    # Populated when this finding explains another one — the upgrade failure
    # that caused the cordon that stranded the pods.
    cause: str | None = None


@dataclass
class HealthState:
    """The result of one cycle."""

    healthy: bool
    findings: list[Finding]
    # Set when the check itself could not run — distinct from "cluster is fine".
    error: str | None = None

    @property
    def degraded(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == DEGRADED]


# Module state. A restart re-notifies once, which is the behaviour we want: if
# the agent was rescheduled mid-incident, saying so again is better than assuming
# someone saw the push that went out before it moved.
_last_notified_at: datetime | None = None
_last_healthy: bool | None = None
_degraded_since: datetime | None = None


async def _is_enabled() -> bool:
    """Respect the same kill switch as every other worker."""
    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == "agent_enabled"))
        setting = result.scalar_one_or_none()
        if setting is None:
            return True
        return setting.value.lower() in ("true", "1", "yes")


async def _setting_int(key: str, fallback: int) -> int:
    """DB settings override env, as everywhere else in this codebase."""
    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            try:
                return int(setting.value)
            except ValueError:
                logger.warning("Setting %s is not an integer: %r", key, setting.value)
    return fallback


def _node_pin_map() -> dict[tuple[str, str], str]:
    """Map ``(namespace, pvc_name) -> node`` for every node-pinned PV.

    local-path PVs carry a ``nodeAffinity`` naming the node whose disk backs
    them, which is what makes a pod using one unschedulable anywhere else. This
    lookup is the whole reason the check can say *why* a pod is Pending.

    Returns an empty map on 403 rather than raising — losing the correlation
    degrades the report, it should not lose the report.
    """
    try:
        volumes = core_api.list_persistent_volume().items
    except ApiException as exc:
        if exc.status == 403:
            logger.warning(
                "No RBAC for persistentvolumes; Pending pods will be reported "
                "without the node they are pinned to."
            )
        else:
            logger.warning("Could not list persistent volumes: %s", exc.status)
        return {}

    pins: dict[tuple[str, str], str] = {}
    for volume in volumes:
        claim = volume.spec.claim_ref
        affinity = volume.spec.node_affinity
        if not claim or not affinity or not affinity.required:
            continue
        for term in affinity.required.node_selector_terms or []:
            for expression in term.match_expressions or []:
                if expression.key == "kubernetes.io/hostname" and expression.values:
                    pins[(claim.namespace, claim.name)] = expression.values[0]
    return pins


def _check_nodes() -> tuple[list[Finding], set[str]]:
    """Nodes that are not Ready, or cordoned. Returns findings and bad node names."""
    try:
        nodes = core_api.list_node().items
    except ApiException as exc:
        return [Finding(DEGRADED, "nodes", f"Cannot list nodes: {exc.status} {exc.reason}")], set()

    unhealthy: set[str] = set()
    detail: list[str] = []
    for node in nodes:
        ready = next(
            (c.status for c in (node.status.conditions or []) if c.type == "Ready"), "Unknown"
        )
        cordoned = bool(node.spec.unschedulable)
        taints = [t.key for t in (node.spec.taints or []) if t.key.startswith(TUPPR_GROUP)]

        if ready != "True":
            unhealthy.add(node.metadata.name)
            detail.append(f"{node.metadata.name}: NotReady")
        elif cordoned:
            unhealthy.add(node.metadata.name)
            suffix = f" (taint: {', '.join(taints)})" if taints else ""
            detail.append(f"{node.metadata.name}: cordoned{suffix}")

    if not detail:
        return [], set()
    return [
        Finding(
            DEGRADED,
            "nodes",
            f"{len(detail)} node(s) unhealthy or cordoned",
            detail,
        )
    ], unhealthy


def _check_pods(pending_minutes: int, pins: dict[tuple[str, str], str], bad_nodes: set[str]):
    """Pods stuck Pending, and pods crash-looping.

    A Pending pod is correlated back to the node its PVC pins it to, which is
    what turns the list into an explanation.
    """
    try:
        pods = core_api.list_pod_for_all_namespaces().items
    except ApiException as exc:
        return [Finding(DEGRADED, "pods", f"Cannot list pods: {exc.status} {exc.reason}")]

    cutoff = datetime.now(UTC) - timedelta(minutes=pending_minutes)
    pending: list[str] = []
    stranded_on: dict[str, int] = {}
    crashing: list[str] = []

    for pod in pods:
        name = f"{pod.metadata.namespace}/{pod.metadata.name}"
        phase = pod.status.phase

        if phase == "Pending":
            created = pod.metadata.creation_timestamp
            if created and created < cutoff:
                node = None
                for volume in pod.spec.volumes or []:
                    pvc = getattr(volume, "persistent_volume_claim", None)
                    if pvc:
                        node = pins.get((pod.metadata.namespace, pvc.claim_name))
                        if node:
                            break
                if node:
                    pending.append(f"{name} (pinned to {node})")
                    stranded_on[node] = stranded_on.get(node, 0) + 1
                else:
                    pending.append(name)

        for status in pod.status.container_statuses or []:
            waiting = status.state.waiting if status.state else None
            if waiting and waiting.reason == "CrashLoopBackOff":
                crashing.append(f"{name} ({status.restart_count} restarts)")

    findings: list[Finding] = []
    if pending:
        cause = None
        # If every stranded pod points at a node we already flagged, say so —
        # that sentence is the difference between a symptom and a diagnosis.
        explained = {n for n in stranded_on if n in bad_nodes}
        if explained:
            nodes = ", ".join(sorted(explained))
            cause = f"pinned by local-path PVCs to {nodes}, which is unschedulable"
        findings.append(
            Finding(
                DEGRADED,
                "pods",
                f"{len(pending)} pod(s) Pending for over {pending_minutes}m",
                pending[:12],
                cause=cause,
            )
        )
    if crashing:
        findings.append(
            Finding(WARNING, "pods", f"{len(crashing)} pod(s) crash-looping", crashing[:12])
        )
    return findings


def _condition_age(condition: dict) -> timedelta | None:
    """How long a condition has held its current status, per the cluster.

    Read from ``lastTransitionTime`` rather than remembered between cycles, so
    the grace periods below cost this worker no state of its own.
    """
    stamp = condition.get("lastTransitionTime")
    if not stamp:
        return None
    try:
        return datetime.now(UTC) - datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _not_ready(items: list[dict]) -> list[str]:
    """Flux resources that are actually broken, with the reason.

    Flux's Ready condition is three-state and the middle one is not a fault:
    ``True`` reconciled, ``False`` failed, ``Unknown`` reconciling right now.
    Treating ``Unknown`` as degradation reports every routine upgrade as an
    outage — with Renovate auto-merging, that is most days, and the first thing
    this check ever did was page about its own rollout.

    ``False`` is a real failure but not always a durable one, because Flux
    retries. Both statuses therefore have to persist past a grace period before
    they count, which is what separates "mid-reconcile" from "stuck".
    """
    out = []
    for item in items:
        conditions = (item.get("status") or {}).get("conditions") or []
        ready = next((c for c in conditions if c.get("type") == "Ready"), None)
        if not ready:
            continue
        status = ready.get("status")
        if status == "True":
            continue

        age = _condition_age(ready)
        grace = FLUX_RECONCILING_GRACE if status == "Unknown" else FLUX_FAILED_GRACE
        # No timestamp means we cannot tell fresh from stuck. Report it: a
        # missing lastTransitionTime is rare, and staying quiet on the unknown
        # case is the failure mode this whole worker exists to avoid.
        if age is not None and age < grace:
            continue

        name = f"{item['metadata']['namespace']}/{item['metadata']['name']}"
        detail = (ready.get("message") or ready.get("reason") or "")[:140]
        if status == "Unknown":
            mins = int(age.total_seconds() // 60) if age else 0
            detail = f"stuck reconciling for {mins}m: {detail}"
        out.append(f"{name}: {detail}")
    return out


def _check_flux() -> list[Finding]:
    """Kustomizations and HelmReleases that are not Ready."""
    findings = []
    for group, version, plural, label in (
        (FLUX_GROUP_KUSTOMIZE, "v1", "kustomizations", "Kustomization"),
        (FLUX_GROUP_HELM, "v2", "helmreleases", "HelmRelease"),
    ):
        try:
            items = custom_api.list_cluster_custom_object(group, version, plural).get("items", [])
        except ApiException as exc:
            logger.warning("Cannot list %s: %s", plural, exc.status)
            continue
        broken = _not_ready(items)
        if broken:
            findings.append(
                Finding(DEGRADED, "flux", f"{len(broken)} {label}(s) not Ready", broken[:8])
            )
    return findings


def _check_upgrades() -> list[Finding]:
    """tuppr upgrade CRs sitting in a failed phase.

    This is the one that would have caught the three-hour outage: the
    TalosUpgrade sat in ``phase: Failed`` the entire time, saying exactly what
    was wrong, and nothing was reading it.
    """
    findings = []
    for plural in ("talosupgrades", "kubernetesupgrades"):
        try:
            items = custom_api.list_cluster_custom_object(TUPPR_GROUP, "v1alpha1", plural).get(
                "items", []
            )
        except ApiException as exc:
            if exc.status not in (403, 404):
                logger.warning("Cannot list %s: %s", plural, exc.status)
            continue
        for item in items:
            status = item.get("status") or {}
            if status.get("phase") != "Failed":
                continue
            detail = []
            for node in status.get("failedNodes") or []:
                detail.append(f"{node.get('nodeName', '?')}: {node.get('lastError', '')[:200]}")
            findings.append(
                Finding(
                    DEGRADED,
                    "upgrade",
                    f"{plural[:-1]} '{item['metadata']['name']}' is in phase Failed",
                    detail,
                )
            )
    return findings


def _check_databases() -> list[Finding]:
    """CNPG clusters with fewer ready instances than desired.

    Invisible to the pod checks: a cluster with broken replication keeps every
    pod Running and only says so in its own status.
    """
    try:
        items = custom_api.list_cluster_custom_object(CNPG_GROUP, "v1", "clusters").get("items", [])
    except ApiException as exc:
        if exc.status not in (403, 404):
            logger.warning("Cannot list CNPG clusters: %s", exc.status)
        return []

    findings = []
    for item in items:
        spec, status = item.get("spec") or {}, item.get("status") or {}
        desired, ready = spec.get("instances", 0), status.get("readyInstances", 0)
        name = f"{item['metadata']['namespace']}/{item['metadata']['name']}"
        if ready < desired:
            findings.append(
                Finding(
                    DEGRADED,
                    "database",
                    f"CNPG {name}: {ready}/{desired} instances ready",
                    [status.get("phase", "")],
                )
            )
    return findings


def _check_backups(max_age_hours: int) -> list[Finding]:
    """Volsync sources that have not synced recently.

    Backups fail silently — nothing else in the cluster notices when a nightly
    restic snapshot stops happening, and the failure surfaces only when someone
    needs a restore.
    """
    try:
        items = custom_api.list_cluster_custom_object(
            VOLSYNC_GROUP, "v1alpha1", "replicationsources"
        ).get("items", [])
    except ApiException as exc:
        if exc.status not in (403, 404):
            logger.warning("Cannot list ReplicationSources: %s", exc.status)
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    stale = []
    for item in items:
        name = f"{item['metadata']['namespace']}/{item['metadata']['name']}"
        last = (item.get("status") or {}).get("lastSyncTime")
        if not last:
            stale.append(f"{name}: never synced")
            continue
        try:
            when = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < cutoff:
            age = int((datetime.now(UTC) - when).total_seconds() // 3600)
            stale.append(f"{name}: last sync {age}h ago")

    if not stale:
        return []
    return [
        Finding(
            WARNING,
            "backup",
            f"{len(stale)} backup(s) have not run in over {max_age_hours}h",
            stale[:8],
        )
    ]


async def collect() -> HealthState:
    """Run every check and return the combined picture."""
    pending_minutes = await _setting_int("health_check_pending_pod_minutes", 10)
    backup_hours = await _setting_int("health_check_backup_max_age_hours", 36)

    try:
        pins = _node_pin_map()
        node_findings, bad_nodes = _check_nodes()
        findings = [
            *node_findings,
            *_check_pods(pending_minutes, pins, bad_nodes),
            *_check_flux(),
            *_check_upgrades(),
            *_check_databases(),
            *_check_backups(backup_hours),
        ]
    except Exception as exc:  # noqa: BLE001 — the check must not take the worker down
        logger.exception("Health check failed to collect")
        return HealthState(healthy=False, findings=[], error=str(exc))

    return HealthState(healthy=not any(f.severity == DEGRADED for f in findings), findings=findings)


def format_message(state: HealthState, degraded_for: timedelta | None = None) -> str:
    """Render findings as an ntfy body, cause first.

    Ordering is deliberate: an upgrade failure is printed before the pods it
    stranded, because reading it the other way round is what costs the twenty
    minutes.
    """
    if state.error:
        return f"Health check could not run: {state.error}"

    order = {"upgrade": 0, "nodes": 1, "pods": 2, "flux": 3, "database": 4, "backup": 5}
    lines: list[str] = []

    if degraded_for:
        minutes = int(degraded_for.total_seconds() // 60)
        lines.append(f"Degraded for ~{minutes}m\n")

    for finding in sorted(state.findings, key=lambda f: order.get(f.area, 9)):
        lines.append(f"● {finding.summary}")
        if finding.cause:
            lines.append(f"  → {finding.cause}")
        for item in finding.detail:
            if item:
                lines.append(f"    {item}")
        lines.append("")

    return "\n".join(lines).strip()


async def check_health() -> dict:
    """One cycle: collect, decide whether to notify, notify.

    Notifies on transitions rather than every cycle — a degraded cluster that
    stays degraded sends one push, then nags at ``health_check_renag_seconds``.
    """
    global _last_notified_at, _last_healthy, _degraded_since

    if not await _is_enabled():
        return {"status": "disabled"}

    state = await collect()
    now = datetime.now(UTC)
    renag = await _setting_int("health_check_renag_seconds", DEFAULT_RENAG_SECONDS)

    became_degraded = not state.healthy and _last_healthy is not False
    recovered = state.healthy and _last_healthy is False
    still_degraded_and_due = (
        not state.healthy
        and _last_healthy is False
        and _last_notified_at is not None
        and (now - _last_notified_at).total_seconds() >= renag
    )

    if became_degraded:
        _degraded_since = now
    if state.healthy:
        _degraded_since = None

    if became_degraded or still_degraded_and_due:
        await notifications.notify(
            notifications.ATTENTION,
            {
                "title": "Cluster degraded",
                "message": format_message(
                    state, (now - _degraded_since) if _degraded_since else None
                ),
                "priority": "high",
                "tags": "rotating_light",
            },
        )
        _last_notified_at = now
    elif recovered:
        await notifications.notify(
            notifications.OUTCOME,
            {
                "title": "Cluster recovered",
                "message": "All checks passing again.",
                "priority": "default",
                "tags": "white_check_mark",
            },
        )
        _last_notified_at = now

    _last_healthy = state.healthy

    return {
        "status": "completed",
        "healthy": state.healthy,
        "findings": len(state.findings),
        "degraded": len(state.degraded),
        "summary": [f.summary for f in state.findings],
    }


def _record_cycle_result(result: dict | None) -> None:
    """Publish the cycle outcome for the status endpoint."""
    from home_ops_agent.api import status as status_api

    status_api._health_check_last_result = {
        **(result or {"status": "completed"}),
        "at": datetime.now(UTC).isoformat(),
    }


async def run_health_monitor():
    """Background task: periodically check cluster health."""
    logger.info(
        "Health monitor started (default interval: %ds)",
        settings.health_check_interval_seconds,
    )

    while True:
        try:
            _record_cycle_result(await check_health())
        except Exception:
            logger.exception("Health check cycle failed")

        interval = await _setting_int(
            "health_check_interval_seconds", settings.health_check_interval_seconds
        )
        await asyncio.sleep(interval)


__all__ = ["check_health", "collect", "format_message", "run_health_monitor"]
