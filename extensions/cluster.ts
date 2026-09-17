import { readFileSync } from "node:fs";
import { request } from "node:https";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

// Cluster tools for models running on the pi harness.
//
// The Python tool registry in `agent/tools/` is not reachable from here -- pi
// executes tools in its own process -- so the diagnostic tools are written
// natively against the Kubernetes API, using the pod's own ServiceAccount. That
// is the same credential and the same API the Python tools use; only the client
// differs.
//
// Read-only, plus `flux_reconcile`. Restart, delete, suspend and resume are
// deliberately not here yet: reconcile is idempotent and is what a stuck
// Kustomization actually needs, while the destructive four want their
// protected-namespace guard ported alongside them rather than after them.

const SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount";
const HOST = process.env.KUBERNETES_SERVICE_HOST;
const PORT =
  process.env.KUBERNETES_SERVICE_PORT_HTTPS ?? process.env.KUBERNETES_SERVICE_PORT ?? "443";

// Serialized tool output is read by a model. A describe of a busy Deployment
// runs to tens of thousands of characters, which crowds out the rest of the
// investigation rather than informing it.
const MAX_RESULT_CHARS = 20000;

function readSaFile(name: string): string | undefined {
  try {
    return readFileSync(`${SA_DIR}/${name}`, "utf8");
  } catch {
    return undefined;
  }
}

const CA = readSaFile("ca.crt");

// Read per request, never cached. Projected ServiceAccount tokens are
// short-lived and rewritten in place by the kubelet, so a copy taken at module
// load starts failing with 401 partway through the life of a long-running pod --
// which would present as the cluster tools breaking for no reason rather than
// as a token expiring.
function bearerToken(): string | undefined {
  return readSaFile("token")?.trim();
}

interface ApiInit {
  method?: string;
  body?: string;
  contentType?: string;
  accept?: string;
  signal?: AbortSignal;
}

function api(path: string, init: ApiInit = {}): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const token = bearerToken();
    if (!token) {
      reject(new Error(`no ServiceAccount token at ${SA_DIR}/token`));
      return;
    }
    const headers: Record<string, string | number> = {
      authorization: `Bearer ${token}`,
      accept: init.accept ?? "application/json",
    };
    if (init.body !== undefined) {
      headers["content-type"] = init.contentType ?? "application/json";
      headers["content-length"] = Buffer.byteLength(init.body);
    }
    const req = request(
      { host: HOST, port: PORT, path, method: init.method ?? "GET", ca: CA, headers },
      (res) => {
        let data = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => resolve({ status: res.statusCode ?? 0, body: data }));
      }
    );
    req.on("error", reject);
    init.signal?.addEventListener("abort", () => req.destroy(new Error("aborted")), { once: true });
    if (init.body !== undefined) req.write(init.body);
    req.end();
  });
}

/** The API server's own `message` beats a status line: `pods "x" not found`
 *  tells the model what to do next, "HTTP 404" does not. */
async function apiRaw(path: string, init: ApiInit = {}): Promise<string> {
  const res = await api(path, init);
  if (res.status >= 400) {
    let message = res.body.slice(0, 300);
    try {
      message = JSON.parse(res.body).message ?? message;
    } catch {
      // A non-JSON error body (a proxy page, or plain text from the log
      // endpoint); the snippet above is what there is.
    }
    throw new Error(`HTTP ${res.status}: ${message}`);
  }
  return res.body;
}

async function apiJson(path: string, init: ApiInit = {}): Promise<any> {
  return JSON.parse(await apiRaw(path, init));
}

function text(value: string) {
  const body =
    value.length > MAX_RESULT_CHARS
      ? `${value.slice(0, MAX_RESULT_CHARS)}\n... [truncated, ${value.length} chars total]`
      : value;
  return { content: [{ type: "text" as const, text: body }] };
}

function json(value: unknown, details?: Record<string, unknown>) {
  return { ...text(JSON.stringify(value, null, 1)), ...(details ? { details } : {}) };
}

/** Tool errors are returned, not thrown: the model can correct a wrong
 *  namespace on its own, whereas a thrown error ends the run. */
function failed(action: string, err: unknown) {
  return text(`Failed to ${action}: ${err instanceof Error ? err.message : String(err)}`);
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const pairs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return pairs.length ? `?${pairs.join("&")}` : "";
}

const seg = encodeURIComponent;

// --- Flux ---------------------------------------------------------------

const KUSTOMIZE = { group: "kustomize.toolkit.fluxcd.io", version: "v1", plural: "kustomizations" };
const HELM = { group: "helm.toolkit.fluxcd.io", version: "v2", plural: "helmreleases" };

type FluxKind = typeof KUSTOMIZE | typeof HELM;

function fluxPath(kind: FluxKind, namespace?: string, name?: string): string {
  const base = namespace
    ? `/apis/${kind.group}/${kind.version}/namespaces/${seg(namespace)}/${kind.plural}`
    : `/apis/${kind.group}/${kind.version}/${kind.plural}`;
  return name ? `${base}/${seg(name)}` : base;
}

// Flux's Ready condition is three-state, and the third state is the interesting
// one: Unknown means "reconciliation in progress, or never completed", which is
// not the same as failing. Collapsing it to a boolean -- as a plain
// `status == "True"` check does -- reports a Kustomization that has never
// reconciled as a clean failure, and one mid-retry as broken.
function readyStatus(conditions: any[] | undefined): string {
  return (conditions ?? []).find((c) => c?.type === "Ready")?.status ?? "Missing";
}

function briefConditions(conditions: any[] | undefined) {
  return (conditions ?? []).map((c) => ({
    type: c?.type,
    status: c?.status,
    reason: c?.reason,
    message: String(c?.message ?? "").slice(0, 300),
    lastTransitionTime: c?.lastTransitionTime,
  }));
}

function fluxSummary(item: any, extra: Record<string, unknown>) {
  const ready = readyStatus(item?.status?.conditions);
  return {
    name: item?.metadata?.name,
    namespace: item?.metadata?.namespace,
    ready,
    suspended: item?.spec?.suspend ?? false,
    lastAppliedRevision: item?.status?.lastAppliedRevision ?? "",
    ...extra,
    // Conditions are carried only when they explain something. Attaching them
    // to every healthy object turns a cluster-wide listing into pages of
    // "Applied revision: main@sha1:..." that bury the one that is not ready.
    ...(ready === "True" ? {} : { conditions: briefConditions(item?.status?.conditions) }),
  };
}

// --- helpers used by the tools ------------------------------------------

function describeState(state: any): string {
  // The Python tools stringify the client object here, which yields a hundred
  // characters of repr per container. What matters is which of the three states
  // is set, and why.
  if (!state) return "unknown";
  if (state.running) return `running since ${state.running.startedAt ?? "?"}`;
  if (state.waiting) {
    const why = String(state.waiting.message ?? "").slice(0, 200);
    return `waiting: ${state.waiting.reason ?? "?"}${why ? ` -- ${why}` : ""}`;
  }
  if (state.terminated) {
    return `terminated: ${state.terminated.reason ?? "?"} (exit ${state.terminated.exitCode})`;
  }
  return "unknown";
}

/** Drop the two fields that are pure noise in a described object.
 *  managedFields alone is routinely larger than the spec it annotates. */
function strip(obj: any): any {
  const meta = obj?.metadata;
  if (meta) {
    delete meta.managedFields;
    if (meta.annotations) delete meta.annotations["kubectl.kubernetes.io/last-applied-configuration"];
  }
  return obj;
}

export default function (pi: ExtensionAPI) {
  if (!HOST) {
    // Registering nothing is deliberate, as in searxng.ts: outside a cluster
    // these tools cannot work, and a tool that is always present and always
    // fails is worse than one that is absent -- the model keeps choosing it and
    // reports the failure as though the cluster were down.
    console.error(
      "[cluster] KUBERNETES_SERVICE_HOST is not set; cluster tools are unavailable. " +
        "They only work from inside a pod."
    );
    return;
  }

  pi.registerTool({
    name: "flux_get_kustomizations",
    label: "Flux Kustomizations",
    description:
      "List Flux Kustomizations with their Ready status. Ready is three-state: True, " +
      "False, or Unknown (reconciling, or never reconciled) -- Unknown is not a " +
      "failure. Conditions are included for anything not Ready. Omit namespace for " +
      "the whole cluster.",
    promptSnippet: "flux_get_kustomizations -- Flux Kustomization status",
    parameters: Type.Object({
      namespace: Type.Optional(Type.String({ description: "Limit to one namespace" })),
      failing_only: Type.Optional(
        Type.Boolean({ description: "Only those whose Ready is not True (default false)" })
      ),
    }),
    async execute(_id, params, signal) {
      try {
        const result = await apiJson(fluxPath(KUSTOMIZE, params.namespace), { signal });
        let items = (result.items ?? []).map((ks: any) => fluxSummary(ks, { path: ks?.spec?.path }));
        if (params.failing_only) items = items.filter((k: any) => k.ready !== "True");
        return json(items, { count: items.length });
      } catch (err) {
        return failed("list Kustomizations", err);
      }
    },
  });

  pi.registerTool({
    name: "flux_get_helmreleases",
    label: "Flux HelmReleases",
    description:
      "List Flux HelmReleases with their Ready status, chart and version. Ready is " +
      "three-state: True, False, or Unknown. Conditions are included for anything not " +
      "Ready. Omit namespace for the whole cluster.",
    promptSnippet: "flux_get_helmreleases -- Flux HelmRelease status",
    parameters: Type.Object({
      namespace: Type.Optional(Type.String({ description: "Limit to one namespace" })),
      failing_only: Type.Optional(
        Type.Boolean({ description: "Only those whose Ready is not True (default false)" })
      ),
    }),
    async execute(_id, params, signal) {
      try {
        const result = await apiJson(fluxPath(HELM, params.namespace), { signal });
        let items = (result.items ?? []).map((hr: any) =>
          fluxSummary(hr, {
            chart: hr?.spec?.chart?.spec?.chart ?? hr?.spec?.chartRef?.name ?? "",
            version: hr?.spec?.chart?.spec?.version ?? "",
            lastAttemptedRevision: hr?.status?.lastAttemptedRevision ?? "",
          })
        );
        if (params.failing_only) items = items.filter((h: any) => h.ready !== "True");
        return json(items, { count: items.length });
      } catch (err) {
        return failed("list HelmReleases", err);
      }
    },
  });

  pi.registerTool({
    name: "flux_reconcile",
    label: "Flux reconcile",
    description:
      "Force a Flux resource to reconcile now, by setting its requestedAt annotation. " +
      "Idempotent. A Kustomization and the HelmRelease it manages reconcile separately, " +
      "so after a chart or image change both usually need this.",
    promptSnippet: "flux_reconcile -- force a Kustomization or HelmRelease to reconcile",
    parameters: Type.Object({
      kind: Type.String({ description: "kustomization or helmrelease" }),
      name: Type.String({ description: "Resource name" }),
      namespace: Type.String({ description: "Resource namespace" }),
    }),
    async execute(_id, params, signal) {
      const kind = params.kind.toLowerCase();
      const target = kind === "kustomization" ? KUSTOMIZE : kind === "helmrelease" ? HELM : null;
      if (!target) {
        return text(`Unsupported kind '${params.kind}'. Use 'kustomization' or 'helmrelease'.`);
      }
      const requestedAt = new Date().toISOString();
      try {
        await apiRaw(fluxPath(target, params.namespace, params.name), {
          method: "PATCH",
          // Merge patch, not strategic: these are custom resources, and the API
          // server rejects a strategic merge patch on those.
          contentType: "application/merge-patch+json",
          body: JSON.stringify({
            metadata: { annotations: { "reconcile.fluxcd.io/requestedAt": requestedAt } },
          }),
          signal,
        });
        return json({
          status: "ok",
          message: `Reconcile requested for ${kind}/${params.name} in ${params.namespace}`,
          requestedAt,
        });
      } catch (err) {
        return failed(`reconcile ${kind}/${params.name}`, err);
      }
    },
  });

  // --- Kubernetes -------------------------------------------------------

  pi.registerTool({
    name: "k8s_get_pods",
    label: "Pods",
    description:
      "List pods with phase, node, and per-container readiness and restart counts. " +
      "Omit namespace for the whole cluster; set problems_only to show just the pods " +
      "that are not running cleanly.",
    promptSnippet: "k8s_get_pods -- pod status and restart counts",
    parameters: Type.Object({
      namespace: Type.Optional(Type.String({ description: "Limit to one namespace" })),
      label_selector: Type.Optional(
        Type.String({ description: "e.g. app.kubernetes.io/name=jellyfin" })
      ),
      problems_only: Type.Optional(
        Type.Boolean({
          description: "Only pods not Running/Succeeded, or with an unready container",
        })
      ),
    }),
    async execute(_id, params, signal) {
      const base = params.namespace
        ? `/api/v1/namespaces/${seg(params.namespace)}/pods`
        : "/api/v1/pods";
      try {
        const result = await apiJson(base + query({ labelSelector: params.label_selector }), {
          signal,
        });
        let items = (result.items ?? []).map((pod: any) => ({
          name: pod?.metadata?.name,
          namespace: pod?.metadata?.namespace,
          phase: pod?.status?.phase,
          node: pod?.spec?.nodeName,
          created: pod?.metadata?.creationTimestamp,
          containers: (pod?.status?.containerStatuses ?? []).map((cs: any) => ({
            name: cs?.name,
            ready: cs?.ready ?? false,
            restarts: cs?.restartCount ?? 0,
            state: describeState(cs?.state),
          })),
        }));
        if (params.problems_only) {
          items = items.filter(
            (p: any) =>
              !["Running", "Succeeded"].includes(p.phase) ||
              p.containers.some((c: any) => !c.ready)
          );
        }
        return json(items, { count: items.length });
      } catch (err) {
        return failed("list pods", err);
      }
    },
  });

  pi.registerTool({
    name: "k8s_get_pod_logs",
    label: "Pod logs",
    description:
      "Read a pod's logs. Set previous=true to read the log of the last terminated " +
      "container, which is the only place a CrashLoopBackOff's cause is visible.",
    promptSnippet: "k8s_get_pod_logs -- container logs, including the previous crash",
    parameters: Type.Object({
      namespace: Type.String({ description: "Pod namespace" }),
      pod_name: Type.String({ description: "Pod name" }),
      container: Type.Optional(
        Type.String({ description: "Container name, for multi-container pods" })
      ),
      tail_lines: Type.Optional(Type.Number({ description: "Lines from the end (default 100)" })),
      previous: Type.Optional(
        Type.Boolean({ description: "Read the previous, terminated container (default false)" })
      ),
    }),
    async execute(_id, params, signal) {
      const path =
        `/api/v1/namespaces/${seg(params.namespace)}/pods/${seg(params.pod_name)}/log` +
        query({
          container: params.container,
          tailLines: params.tail_lines ?? 100,
          previous: params.previous ? "true" : undefined,
        });
      try {
        // The log endpoint serves text/plain, so this one does not go through
        // apiJson -- the body is not JSON and parsing it would throw on success.
        const logs = await apiRaw(path, { accept: "text/plain", signal });
        return text(logs.trim() || "(no logs)");
      } catch (err) {
        return failed(`read logs for ${params.pod_name}`, err);
      }
    },
  });

  pi.registerTool({
    name: "k8s_get_events",
    label: "Events",
    description:
      "Recent Kubernetes events, newest first. Events are where scheduling failures, " +
      "image pull errors and probe failures are explained. Omit namespace for the " +
      "whole cluster.",
    promptSnippet: "k8s_get_events -- recent warnings and failures",
    parameters: Type.Object({
      namespace: Type.Optional(Type.String({ description: "Limit to one namespace" })),
      resource_name: Type.Optional(Type.String({ description: "Only events about this object" })),
      warnings_only: Type.Optional(
        Type.Boolean({ description: "Only type=Warning (default false)" })
      ),
      limit: Type.Optional(Type.Number({ description: "Max events (default 20)" })),
    }),
    async execute(_id, params, signal) {
      const base = params.namespace
        ? `/api/v1/namespaces/${seg(params.namespace)}/events`
        : "/api/v1/events";
      const selectors = [
        params.resource_name ? `involvedObject.name=${params.resource_name}` : "",
        params.warnings_only ? "type=Warning" : "",
      ].filter(Boolean);
      try {
        const result = await apiJson(
          base + query({ fieldSelector: selectors.join(",") || undefined }),
          { signal }
        );
        const items = (result.items ?? [])
          .map((e: any) => ({
            type: e?.type,
            reason: e?.reason,
            message: String(e?.message ?? "").slice(0, 500),
            object: `${e?.involvedObject?.kind}/${e?.involvedObject?.name}`,
            namespace: e?.metadata?.namespace,
            count: e?.count ?? e?.series?.count ?? 1,
            // Newer events carry eventTime and leave lastTimestamp null, so
            // sorting on lastTimestamp alone silently sorts them to the bottom
            // -- the newest events end up last in a list ordered by recency.
            last_seen: e?.lastTimestamp ?? e?.eventTime ?? e?.metadata?.creationTimestamp,
          }))
          .sort((a: any, b: any) =>
            String(b.last_seen ?? "").localeCompare(String(a.last_seen ?? ""))
          )
          .slice(0, params.limit ?? 20);
        return json(items, { count: items.length });
      } catch (err) {
        return failed("list events", err);
      }
    },
  });

  pi.registerTool({
    name: "k8s_describe_resource",
    label: "Describe resource",
    description:
      "Fetch the full object for one resource. Supports pod, service, deployment, " +
      "statefulset, daemonset, node, configmap, pvc, kustomization and helmrelease.",
    promptSnippet: "k8s_describe_resource -- the full spec and status of one object",
    parameters: Type.Object({
      kind: Type.String({ description: "pod, deployment, helmrelease, ..." }),
      name: Type.String({ description: "Resource name" }),
      namespace: Type.Optional(Type.String({ description: "Namespace (not needed for node)" })),
    }),
    async execute(_id, params, signal) {
      const kind = params.kind.toLowerCase().replace(/s$/, "");
      const ns = params.namespace ?? "default";
      // Secrets are absent by design, not by oversight. A tool result is carried
      // into the model's context and then into the stored conversation, so
      // reading one would persist a credential in two places that are not meant
      // to hold any. The agent's ClusterRole denies them as well, so adding the
      // kind here would produce a 403 rather than a leak.
      const core: Record<string, string> = {
        pod: "pods",
        service: "services",
        configmap: "configmaps",
        pvc: "persistentvolumeclaims",
        persistentvolumeclaim: "persistentvolumeclaims",
      };
      const apps: Record<string, string> = {
        deployment: "deployments",
        statefulset: "statefulsets",
        daemonset: "daemonsets",
      };
      let path: string;
      if (kind === "node") path = `/api/v1/nodes/${seg(params.name)}`;
      else if (core[kind]) path = `/api/v1/namespaces/${seg(ns)}/${core[kind]}/${seg(params.name)}`;
      else if (apps[kind]) {
        path = `/apis/apps/v1/namespaces/${seg(ns)}/${apps[kind]}/${seg(params.name)}`;
      } else if (kind === "kustomization") path = fluxPath(KUSTOMIZE, ns, params.name);
      else if (kind === "helmrelease") path = fluxPath(HELM, ns, params.name);
      else return text(`Unsupported kind '${params.kind}'.`);

      try {
        return json(strip(await apiJson(path, { signal })));
      } catch (err) {
        return failed(`describe ${kind}/${params.name}`, err);
      }
    },
  });

  pi.registerTool({
    name: "k8s_get_nodes",
    label: "Nodes",
    description:
      "Node conditions, capacity, allocatable and kubelet version. Ready=False, or " +
      "MemoryPressure/DiskPressure=True on a node, explains most cluster-wide symptoms.",
    promptSnippet: "k8s_get_nodes -- node health and capacity",
    parameters: Type.Object({}),
    async execute(_id, _params, signal) {
      try {
        const result = await apiJson("/api/v1/nodes", { signal });
        const items = (result.items ?? []).map((node: any) => {
          const conditions: Record<string, string> = {};
          for (const c of node?.status?.conditions ?? []) conditions[c.type] = c.status;
          return {
            name: node?.metadata?.name,
            conditions,
            unschedulable: node?.spec?.unschedulable ?? false,
            kubelet: node?.status?.nodeInfo?.kubeletVersion,
            os: node?.status?.nodeInfo?.osImage,
            capacity: node?.status?.capacity,
            allocatable: node?.status?.allocatable,
          };
        });
        return json(items, { count: items.length });
      } catch (err) {
        return failed("list nodes", err);
      }
    },
  });
}
