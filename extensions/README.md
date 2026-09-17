# pi extensions

The cluster tools the agent exposes to models running on the `pi` harness.

## Why native extensions rather than a bridge

An earlier sketch had a single generic extension proxying every tool call back
into the Python process over localhost. That treats pi as an executor, which it
is not — `ExtensionAPI` is the agent runtime: `registerTool`, `registerCommand`,
`registerProvider`, plus hooks over the whole loop (`before_agent_start`,
`context`, `tool_call`, `before_provider_request`). A bridge would add a hop and
a second failure mode to buy nothing.

Tools are therefore written here, in TypeScript, and run in pi's process.

`workspace.ts` is the one exception, and it is not a counter-example: what it
proxies is not a tool but a *credential*. See below.

## Loading

Extensions are passed explicitly with `-e`, and discovery is disabled with
`-ne`. Nothing on the filesystem can introduce a tool the image did not ship,
and the local `~/.pi` of whoever built the image cannot leak in.

A failed extension is loud: pi prints `Error: Failed to load extension <path>`
and continues. A working one prints nothing, so silence on startup is the
success signal.

## searxng.ts

`web_search`, backed by the self-hosted SearXNG in `productivity`.

Requires `SEARXNG_URL`. There is no default: an earlier version fell back to a
Service name from one particular cluster, which for anyone else is a tool that
is always present and always fails — worse than not having it, because the model
keeps choosing it and reports the failure as though the web were down. With the
variable unset the extension registers nothing and says so on stderr.

Point it at the in-cluster Service rather than a public hostname. The agent runs
in the cluster, so a public name leaves through the gateway and comes back for
nothing — and stops working entirely when external DNS does, which is exactly
the kind of incident the agent is most likely to be asked about.

Verified end to end against `gpt-6-astra`:

    TOOLCALL   web_search {"query": "external-dns 1.22.0 release notes", "limit": 5}
    ASSISTANT  Release external-dns-helm-chart-1.22.0 · kubernetes-sigs/external-dns

Note that SearXNG's usefulness depends on its own version — engine scrapers
break upstream constantly. A stale image returns zero results for every query
while looking healthy.

## cluster.ts

The diagnostic tools: `flux_get_kustomizations`, `flux_get_helmreleases`,
`flux_reconcile`, `k8s_get_pods`, `k8s_get_pod_logs`, `k8s_get_events`,
`k8s_describe_resource`, `k8s_get_nodes`.

These talk to the Kubernetes API directly, over HTTPS, using the pod's own
ServiceAccount token and CA from `/var/run/secrets/kubernetes.io/serviceaccount`.
That is the same credential and the same API the Python tools in
`agent/tools/` use — only the client differs. Outside a pod
`KUBERNETES_SERVICE_HOST` is unset, the extension registers nothing and says so,
exactly as `searxng.ts` does without its URL.

The token is re-read on every request rather than cached at module load.
Projected ServiceAccount tokens are short-lived and rewritten in place by the
kubelet, so a copy taken at startup begins returning 401 partway through the
life of a long-running pod — which would look like the cluster tools breaking
for no reason.

### What is deliberately not here

`k8s_restart_workload`, `k8s_delete_pod`, `flux_suspend` and `flux_resume`.
`flux_reconcile` is included because it is idempotent and is what a stuck
Kustomization actually needs; the destructive four want their
protected-namespace guard ported alongside them, not after them.

Secrets are not a supported kind for `k8s_describe_resource`. A tool result
travels into the model's context and then into the stored conversation, so
reading one would persist a credential in two places not meant to hold any.
The agent's ClusterRole denies them regardless.

### Differences from the Python tools

Three, all deliberate:

- **Ready is reported as a string, not a boolean.** Flux's `Ready` condition is
  three-state, and `Unknown` — reconciling, or never reconciled — is not a
  failure. The Python tools collapse it with `status == "True"`, which reports a
  Kustomization that has never reconciled and one that is mid-retry
  identically, as broken.
- **Conditions are attached only to objects that are not Ready.** On a
  cluster-wide listing, conditions on every healthy object are pages of
  "Applied revision: main@sha1:…" that bury the one that matters.
- **Container state is summarised, not stringified.** The Python version emits
  `str(cs.state)`, a hundred characters of client-object repr per container;
  this emits `waiting: CrashLoopBackOff — back-off 5m0s restarting…`.

`namespace` is optional everywhere and means "the whole cluster" when omitted,
rather than defaulting to `default`.

## workspace.ts

`workspace_commit` — the only way changes leave a checkout.

This one does not do its own work, and the reason is the push token. pi has a
`bash` tool, so anything in its environment or on its filesystem is readable by
the model. A native TypeScript `workspace_commit` would need the GitHub token,
and a model holding that token can simply run `git push` — walking past
`ALLOWED_COMMIT_PATHS` and `PROTECTED_BRANCHES` rather than through them. The
guardrails would still exist; they would just no longer be the only way out.

So the extension is handed a channel instead: a per-run Unix socket (0600, in a
0700 directory) and a single-run token whose only action is "commit this
workspace". One JSON line in, one JSON line out, no HTTP. The push and both
guardrails stay in Python, shared with the Claude Code path.

The model can read that token via `bash`. That is fine, and it is the whole
design: the token authorises exactly the tool the model already has, so it grants
no capability it did not already possess — which is precisely what could not be
said of the push token.

Registered only when `HOMEOPS_WORKSPACE_SOCKET` and `HOMEOPS_WORKSPACE_TOKEN`
are both set, which is only on runs that have a checkout. Unlike the others it
says nothing on stderr when it registers nothing: most runs are a conversation
about cluster state with nothing to commit, so a message every time would be
noise rather than signal.

A blocked commit comes back with the rejected paths named and the change
unstaged, so the model can revert those edits and call again inside the same
run — a guardrail that ends the run teaches nothing.

## A note on the system prompt

The agent passes `--system-prompt`, which *replaces* pi's built-in prompt. That
is deliberate: pi's own prompt is mostly directions to pi's README, docs and
examples — useful to someone working on pi, an invitation to go reading
irrelevant documentation for an agent asked why a Kustomization is stuck.

The cost is that pi's "Available tools" section is skipped, and that section is
the only consumer of the `promptSnippet` each tool here registers. Tool
*selection* is unaffected — that runs off the schemas sent with the request, which
is how models picked `web_search` and the cluster tools correctly before anyone
noticed. The snippets are currently inert rather than missed; they are kept
because they are correct, and because `--system-prompt` is the only reason they
are not used.

`--append-system-prompt` composes with `--system-prompt`, and carries the
workspace note on runs that have a checkout.
