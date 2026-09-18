# Home-Ops Agent

An autonomous operator for home Kubernetes clusters. Runs inside your cluster and uses Claude to review PRs, diagnose alerts, fix issues, and provide an interactive chat interface.

Built for GitOps setups using [Flux Operator](https://github.com/controlplaneio-fluxcd/flux-operator), but works with any Kubernetes cluster that has Prometheus, Loki, and ntfy.

## Features

- **PR Review** — Monitors your GitHub repo for open PRs (primarily Renovate dependency updates). Posts review comments with risk assessment. 4-tier auto-merge modes from comment-only to fully autonomous.
- **Alert Investigation** — Two-stage pipeline: fast triage with Haiku determines severity, then escalates fixable issues to Sonnet for corrective action. Subscribes to ntfy topics (Alertmanager, Gatus). Alerts that have already cleared are dropped before triage rather than investigated.
- **Cluster Health Check** — A scheduled sweep that notifies when the cluster degrades, including the cases nothing else is watching: a node left cordoned after a failed upgrade, pods stranded by node-pinned storage, a stuck Flux reconcile, a database short of replicas, a backup that quietly stopped running. Correlates rather than lists — it reports *why* pods are Pending, not just how many. See [Cluster Health Check](#cluster-health-check).
- **Auto-Fix** — Can restart stuck pods, reconcile Flux resources, create fix branches, commit changes, and open PRs. Code Fix agent auto-merges after CI passes. Every action is logged and reported via ntfy.
- **Interactive Chat** — Next.js web UI (shadcn/ui) where you can ask questions about cluster state, run diagnostics, or issue commands. Conversations persist across page refreshes.
- **Persistent Memory** — Extracts durable facts from conversations and carries them into every future prompt. Add your own for anything learned elsewhere; incident-shaped entries expire so a stale snapshot cannot contradict what the live tools report.
- **Per-Task Models** — Assign a different model to each agent (e.g., Haiku for cheap PR reviews, Opus for deep review). Claude models run on your **Pro/Max subscription**, named by alias so they track the current version without you editing anything. Configurable via the Settings UI.
- **Inspect it from your editor** — A read-only [MCP endpoint](#mcp-endpoint) exposes what the agent has run, the tool calls behind each run, and what it remembers, so a coding session can diagnose a bad run directly instead of clicking through the UI.
- **Customizable Prompts** — Edit system prompts per agent through modal editors to describe your specific cluster setup.
- **Activity History** — View all agent actions and chat conversations with full reasoning and tool call details. Click a conversation to reopen it.
- **Analytics** — Token volume by model and by agent, and run counts by type with failures called out. Cost appears only if something actually costs money; with every provider on a subscription, nothing does.
- **Kill Switch** — Instantly disable all agent activity from the Settings UI. One click to stop, one click to resume.
- **Notification Levels** — Notifications are classified by how much they need you, and one setting picks the threshold. Anything that failed, or needs your review, is always sent.
- **Safety Guardrails** — Code-level protections prevent commits to main, modifications outside `kubernetes/apps/`, and destructive actions in system namespaces.

## Architecture

![Architecture](architecture.png)

Single Python container. Single async process. Background workers as asyncio tasks.

## Prerequisites

- Kubernetes cluster with Flux Operator (or any GitOps tool)
- [CloudNativePG](https://cloudnative-pg.io/) (PostgreSQL) — for conversations, memories, settings, and task logs
- [ntfy](https://ntfy.sh/) — for alert subscriptions and notifications
- Prometheus + Loki — for metrics and log queries (optional, via skills system)
- [SearXNG](https://docs.searxng.org/) — optional, for the `web_search` tool on **both** backends. Needs the JSON format enabled and `SEARXNG_URL` set; without it the tool is simply not registered. Worth having: without it the review agents can read a GitHub Release and nothing else, which is half an answer for a breaking change.
- **A Claude Pro/Max subscription** (via `claude setup-token`) — or a Kimi for Coding key, or imported ChatGPT tokens. See [Providers](#providers). There is no metered API-key option.
- GitHub personal access token — fine-grained (scoped to your repo with `Contents: Read/Write` and `Pull requests: Read/Write`) or classic with `repo` scope (required if using a dedicated bot account)

## Quick Start

### 1. Create the database

Connect to your CNPG primary pod and create the database:

```bash
kubectl exec -n database <postgres-pod> -- psql -U postgres -c \
  "CREATE USER home_ops_agent WITH PASSWORD 'your-password';"

kubectl exec -n database <postgres-pod> -- psql -U postgres -c \
  "CREATE DATABASE home_ops_agent OWNER home_ops_agent;"
```

### 2. Create a GitHub token

**Option A: Fine-grained token** (using your own account):
Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**:
- **Repository access**: Only select your GitOps repo
- **Permissions**: Contents (Read/Write), Pull requests (Read/Write)

**Option B: Classic token** (using a dedicated bot account):
Create a separate GitHub account for the agent, invite it as a collaborator to your repo, then create a classic token with `repo` scope. This is required because fine-grained tokens can only access repos owned by the token creator.

### 3. Create an ntfy user (optional)

Skip this if your ntfy server is open — the agent publishes and subscribes without
credentials. Do it if you want your notifications to be private:

```bash
kubectl exec -n monitoring <ntfy-pod> -- sh -c \
  'printf "password\npassword\n" | ntfy user add --role=user home-ops-agent'

kubectl exec -n monitoring <ntfy-pod> -- ntfy access home-ops-agent alertmanager ro
kubectl exec -n monitoring <ntfy-pod> -- ntfy access home-ops-agent gatus ro
kubectl exec -n monitoring <ntfy-pod> -- ntfy access home-ops-agent home-ops-agent rw
kubectl exec -n monitoring <ntfy-pod> -- ntfy token add home-ops-agent
```

Save the generated token.

### 4. Create the Kubernetes secret

Create a secret with your credentials:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: home-ops-agent-secret
stringData:
  GITHUB_TOKEN: "github_pat_..."
  DATABASE_URL: "postgresql+asyncpg://home_ops_agent:your-password@postgres-rw.database.svc.cluster.local:5432/home_ops_agent"
  SESSION_SECRET: "random-string-here"
  # Optional — only if your ntfy server requires authentication.
  NTFY_TOKEN: "tk_..."
```

Encrypt with SOPS if using Flux.

### 5. Deploy

Use the [bjw-s app-template](https://github.com/bjw-s-labs/helm-charts/tree/main/charts/library/common) Helm chart, or any deployment that gives the container the environment below plus a ServiceAccount with the RBAC in [Safety](#safety).

A minimal HelmRelease:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: home-ops-agent
spec:
  chart:
    spec:
      chart: app-template
      version: 3.x
      sourceRef:
        kind: HelmRepository
        name: bjw-s
        namespace: flux-system
  values:
    controllers:
      home-ops-agent:
        serviceAccount: { identifier: home-ops-agent }
        containers:
          app:
            image:
              repository: ghcr.io/<your-username>/home-ops-agent
              tag: <version>
            env:
              GITHUB_REPO: you/home-ops
              BASE_URL: https://agent.example.com
              CLUSTER_DOMAIN: example.com
              # Optional. Enables the `web_search` tool on the pi backend.
              # Use the in-cluster Service rather than a public hostname, so
              # searches do not depend on external DNS being up.
              SEARXNG_URL: http://searxng.<namespace>.svc.cluster.local:8080
            envFrom:
              - secretRef: { name: home-ops-agent-secret }
    service:
      app:
        controller: home-ops-agent
        ports: { http: { port: 8000 } }
    persistence:
      workspace:
        type: emptyDir
        globalMounts: [{ path: /home/agent/workspace }]
```

The `workspace` volume is only needed for [checkout-mode code fixes](#code-fixes); without it the agent falls back to single-file API commits.

And the ServiceAccount it runs as. Everything here is `get`/`list`/`watch` except three deliberate exceptions — `delete` on pods and `patch` on workloads and Flux resources, which is what the [auto-fix](#features) tools use to restart a pod or force a reconcile:

```yaml
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: home-ops-agent
  namespace: automation          # wherever you deploy it
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: home-ops-agent
rules:
  # Core read
  - apiGroups: [""]
    resources: ["pods", "services", "events", "nodes", "configmaps", "persistentvolumeclaims", "namespaces"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
  # Restart a stuck pod
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["delete"]
  # Workload read + restart
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets", "daemonsets", "replicasets"]
    verbs: ["get", "list", "patch"]
  # Flux — read and reconcile
  - apiGroups: ["kustomize.toolkit.fluxcd.io"]
    resources: ["kustomizations"]
    verbs: ["get", "list", "patch"]
  - apiGroups: ["helm.toolkit.fluxcd.io"]
    resources: ["helmreleases"]
    verbs: ["get", "list", "patch"]
  - apiGroups: ["source.toolkit.fluxcd.io"]
    resources: ["gitrepositories", "ocirepositories", "helmrepositories"]
    verbs: ["get", "list", "patch"]
  - apiGroups: ["gateway.networking.k8s.io"]
    resources: ["httproutes", "gateways"]
    verbs: ["get", "list"]
  - apiGroups: ["metrics.k8s.io"]
    resources: ["pods", "nodes"]
    verbs: ["get", "list"]
  # Talos upgrade diagnostics — read-only (see Skills)
  - apiGroups: ["policy"]
    resources: ["poddisruptionbudgets"]
    verbs: ["get", "list"]
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["get", "list"]
  - apiGroups: ["tuppr.home-operations.com"]
    resources: ["talosupgrades", "kubernetesupgrades"]
    verbs: ["get", "list"]
  # Cluster health check — read-only. Each of these is optional; the check
  # skips whatever it cannot read. See Cluster Health Check.
  - apiGroups: [""]
    resources: ["persistentvolumes"]
    verbs: ["get", "list"]
  - apiGroups: ["postgresql.cnpg.io"]
    resources: ["clusters"]
    verbs: ["get", "list"]
  - apiGroups: ["volsync.backube"]
    resources: ["replicationsources"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: home-ops-agent
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: home-ops-agent
subjects:
  - kind: ServiceAccount
    name: home-ops-agent
    namespace: automation
```

There is deliberately no `create`, `update` or `deletecollection` anywhere, and nothing at all for `nodes/status`, evictions or taints — see [Safety](#safety).

Key environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_REPO` | GitHub repo to monitor (e.g., `user/repo`) | — |
| `CLUSTER_DOMAIN` | Your domain | — |
| `NTFY_URL` | ntfy server URL | `http://ntfy.monitoring.svc.cluster.local` |
| `NTFY_TOKEN` | ntfy access token | — |
| `NTFY_ALERTMANAGER_TOPIC` | Topic for Alertmanager alerts | `alertmanager` |
| `NTFY_GATUS_TOPIC` | Topic for Gatus health checks | `gatus` |
| `NTFY_AGENT_TOPIC` | Topic for agent reports | `home-ops-agent` |
| `PR_CHECK_INTERVAL_SECONDS` | How often to check for PRs | `1800` (30 min) |
| `ALERT_COOLDOWN_SECONDS` | Min time between re-investigating same alert | `900` (15 min) |
| `HEALTH_CHECK_INTERVAL_SECONDS` | How often to sweep [cluster health](#cluster-health-check) | `600` (10 min) |
| `BASE_URL` | Public URL of the agent web UI | — |
| `MCP_API_TOKEN` | Bearer token for the [MCP endpoint](#mcp-endpoint). Unset disables it entirely. | — |
| `MCP_ALLOWED_HOSTS` | Extra `Host` values the MCP endpoint accepts, comma-separated. The host from `BASE_URL` and localhost are always allowed. | — |
| `SEARXNG_URL` | SearXNG base URL for the `web_search` tool. Unset means the tool is not registered at all. | — |
| `AGENT_WORKSPACE_DIR` | Where the git clone and per-run worktrees live, for [checkout-mode code fixes](#code-fixes). | `/home/agent/workspace` |

The `NTFY_*` values above are defaults only: the publish URL, topic and token can be changed from Settings → Notifications without redeploying, and a stored setting takes precedence over the environment.

### 6. Configure via the web UI

Open the agent's web UI and go to **Settings**:

1. **Authentication** — Configure at least one provider (Claude subscription token, Kimi key, or imported ChatGPT tokens)
2. **Cluster Context** — Describe your cluster (nodes, IPs, domain, infrastructure). This is prepended to all agent prompts.
3. **Agents** — Choose which Claude model each agent uses and customize their prompts
4. **Skills** — Enable optional skills (Prometheus, Loki, Flux CD, Talos) and configure their endpoints
5. **PR Mode** — Start with "Comment Only", escalate through 4 tiers as you gain trust (see below)
6. **Notifications** — Choose how much to send, and where (ntfy URL, publish topic, token, subscription topics)
7. **Kill Switch** — Disable/enable all agent activity instantly (this includes the [health check](#cluster-health-check))

### 7. Subscribe to notifications

In the ntfy mobile app, subscribe to the `home-ops-agent` topic on your ntfy server to receive agent reports.

## Agents

| Agent | Default Model | What it does |
|-------|--------------|-------------|
| **PR Review** | Haiku | Reviews open PRs, posts comments with risk assessment |
| **Alert Triage** | Haiku | First responder — checks pods, logs, metrics, determines severity |
| **Alert Fix** | Sonnet | Takes corrective action — restarts pods, reconciles Flux |
| **Code Fix** | Sonnet | Creates branches, commits fixes, opens PRs. Auto-merges after CI passes. |
| **Deep Review** | Opus | Escalation agent for critical PRs in Fully Autonomous mode |
| **Chat** | Sonnet | Interactive conversation about cluster state |

Defaults are subscription aliases (`claude-code/haiku` and so on), which resolve to the current model on their own. All models and prompts are configurable via the Settings UI.

### Providers

Models from three providers can be configured at once — each agent's assigned model is routed to its provider automatically. **All of them bill a subscription or plan; none is metered per token.**

| Provider | Auth | Models |
|----------|------|--------|
| **Claude subscription** | Long-lived token from `claude setup-token` | `claude-code/haiku`, `claude-code/sonnet`, `claude-code/opus` |
| **Kimi for Coding** | API key (Anthropic-compatible endpoint) | `kimi-for-coding` |
| **OpenAI / ChatGPT** | Sign in from the UI (PKCE), auto-refreshed | `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` |

Configure each provider independently under Settings → Authentication.

**Claude subscription.** A `claude-code/` model prefix runs the task through the local Claude Code CLI, billing your Pro/Max plan. Run `claude setup-token` locally (it mints a token valid for a year) and paste the result. The agent's own tools are handed to the CLI as an in-process MCP server, so every tool and guardrail behaves identically; Claude Code's own filesystem and shell tools are removed.

**Claude models are named by alias, not version** — `haiku`, `sonnet`, `opus`. The CLI resolves each to the current model, so nothing needs editing when a new one ships. There is deliberately no metered Anthropic API-key option: it required pinning dated IDs like `claude-sonnet-4-6` and updating them by hand every release. A setting still naming a dated ID is mapped onto its alias automatically.

**OpenAI.** GPT-5.6 comes in three tiers — Sol (flagship), Terra (everyday workhorse) and Luna (fast and cheap). `gpt-5.5` still works but is previous-generation.

Sign in under Settings → Authentication → Connect ChatGPT. The flow needs no local CLI and no callback server:

1. **Connect** opens OpenAI's sign-in page in your browser.
2. After signing in the browser is redirected to `http://localhost:1455/auth/callback?code=…`, which **will not load** — nothing is listening there. That is expected.
3. Copy that URL out of the address bar and paste it back.

It is the OAuth authorization-code flow with PKCE, deliberately not device code: OpenAI gates device-code authorisation behind a ChatGPT *workspace* security setting, and where an admin has disabled it the device flow fails with "contact your workspace admin" and the operator has no recourse. PKCE carries no such condition. Nothing is held server-side between the two steps, so a restart mid-flow costs only a retry, and it works from a phone.

`POST /api/auth/openai` still accepts tokens extracted from a local `codex` CLI, for anyone already carrying them. It is not the route to reach for — a credential obtained that way is renewed only by the refresh endpoint, so once a refresh token is spent it stays dead until somebody notices.

Models available to a ChatGPT subscription are decided by the pinned **pi** version, not by this app. On an older pi every ChatGPT model is rejected with *"not supported when using Codex with a ChatGPT account"*; `gpt-6-astra` needs pi 0.85.1 or later.

### Tools by backend

The two backends do not offer the same tools, and the difference is worth knowing before assigning a model to an agent.

| | `claude-code/*` and Kimi | `gpt-*` (pi) |
|---|---|---|
| Every enabled skill | yes | yes, over the tool bridge |
| Files and shell | Claude Code only, inside a git worktree | always (pi's own `read`/`bash`/`edit`/`write`) |
| Commit and push | yes, via the guarded `workspace_commit` | yes, the same tool |

Both backends run the **same** tool implementations. The Python registry is the
single source of truth; `pi` reaches it over a Unix socket created for the run,
rather than having its own copies.

That is a deliberate reversal of how this started. Writing tools natively for pi
was tried first, and two things made it untenable: about a third of them carry
credentials that must not enter a process with a `bash` tool, and the rest would
have existed twice forever, because the Claude Code backend still needs the
Python ones. Two copies of a tool drift — the native and Python Kubernetes tools
disagreed about how a Flux `Ready` condition is reported within a day.

So enabling or disabling a skill now changes what **both** backends can do,
which is what the Settings page always implied and did not do.

### Asking for a fix in the chat

The **Code Fix** skill adds a `code_fix` tool. Ask the chat to fix an open PR and it checks the branch out, searches the repository, edits as many files as the fix needs, validates with `kubeconform` and pushes one commit.

It delegates to a nested run rather than giving the chat itself a checkout. A worktree is checked out on one branch and lives for one run; a chat does not know when it starts whether it is about a PR at all, so attaching one up front would mean checking out a branch nobody named on every message. The nested run gets a fresh context and the model assigned to **Code Fix**, not the one assigned to **Chat** — and it is not given the `code_fix` tool, so a fix cannot start another fix.

Unlike the PR monitor, it refuses rather than falling back to the single-file API path when a checkout is not possible. That fallback is right for an unattended run, where something beats nothing; here a person asked, and is owed the reason.

Needs a GitHub token, and a model on a backend that supports a checkout (`claude-code/*` or `gpt-*`).

### How a GPT model is allowed to push

`workspace_commit` needs the GitHub push token, and pi has a `bash` tool — so a token placed in pi's environment is a token the model can `git push` with, straight past `ALLOWED_COMMIT_PATHS` and `PROTECTED_BRANCHES` rather than through them.

So pi is given a channel, not a credential. When a run has a workspace, the agent opens a Unix socket for the lifetime of that run — mode 0600, in a 0700 directory, with a random single-run token — whose only action is "commit this workspace". The push itself, and both guardrails, stay in Python and are shared with the Claude Code path.

The model *can* read that token out of its environment, and that is fine: it authorises exactly the tool the model already has, and nothing else. That is the property that makes handing it over acceptable where handing over the push token would not be. `.git/config` has never carried the token either — it is passed per git invocation and scrubbed after clone — so a shell in the worktree cannot recover it.


## PR Modes

### How a PR is routed

A review ends with two independent lines, and the routing is code, not phrasing:

```
SAFE_TO_MERGE: yes|no
FIXABLE: yes|no
```

| Verdict | What happens |
|---|---|
| `SAFE_TO_MERGE: yes` | merged on the next cycle, through the same gate as before |
| `FIXABLE: yes`, and every changed path is one a fix may commit | Code Fix: checkout, edit, validate, guarded push, **re-review**, merge |
| `FIXABLE: yes` but the PR touches `talos/`, `bootstrap/` or tooling | escalated instead — a fix could not commit those, so it is not attempted |
| neither | Deep Review (in `auto_merge_all`), which can itself hand a fix over once it has read the changelogs; otherwise it waits for you |

Three things are deliberate here.

**The scope check uses the same function as the commit guard.** Deciding to attempt a fix and deciding whether that fix may be committed are the same question. They used to be asked separately, so a PR touching `talos/` was sent to the fixer, which opened a checkout, read the repository, made the edit, and only then had the commit rejected.

**Component criticality is not hard-coded.** A cert-manager patch and a cert-manager major are not the same risk, and any list of "critical components" in code goes stale. Routing is on facts the code can verify — paths, labels, CI — while risk stays a judgement the model makes inside the review.

**A pushed fix is re-reviewed before it merges.** CI proves the manifests render; it does not prove the fix is right. Previously the fix changed the head SHA and merged within five minutes, long before the next cycle would have reviewed it, so a semantically wrong but valid manifest merged unseen.


4-tier escalation for PR handling:

| Mode | What it does |
|------|-------------|
| **Comment Only** | Reviews and posts comments. No merging. |
| **Auto-Merge Patch** | Auto-merges `type/patch` and `type/digest` PRs rated safe. |
| **Auto-Merge Minor** | Also auto-merges `type/minor` PRs rated safe. |
| **Fully Autonomous** | Auto-merges all tiers. Escalates `NEEDS_REVIEW` PRs to Deep Review (Opus) for a second opinion. |

### Code fixes

When a review flags `NEEDS_FIX`, the Code Fix agent pushes a fix commit to the PR branch, waits for CI, and auto-merges on success. It has two modes:

- **Checkout mode** (`claude-code/*` models only) — clones your repo and adds a `git worktree` on the PR branch. The agent can grep the whole repo, edit several files in one commit, and validate with `kubeconform` before committing. The only way out is a `workspace_commit` tool that re-applies the path and branch guardrails to the entire staged diff.
- **API mode** (every other model) — single-file commits through the GitHub Contents API.

## Skills

Tools are organized into skills that can be enabled/disabled from the Settings UI.

| Skill | Type | What it provides |
|-------|------|-----------------|
| **Kubernetes** | Built-in | Pod listing, logs, events, restart, delete |
| **GitHub** | Built-in | PR workflow, file content, branches, commits, releases |
| **ntfy** | Built-in | Publish notifications with auth |
| **Prometheus** | Optional | PromQL instant/range queries, metric/label listing, firing alerts |
| **Loki** | Optional | LogQL instant/range queries, label listing |
| **Flux CD** | Optional | List Kustomizations/HelmReleases, reconcile, suspend, resume |
| **Talos Upgrades** | Optional | Read-only diagnostics for node upgrades driven by [tuppr](https://github.com/home-operations/tuppr): upgrade CR status, upgrade Job failures, node taint/cordon state, and why a node cannot be drained |

Optional skills require endpoint configuration (done in the Skills settings panel).

**Talos diagnostics are read-only on purpose.** Recovering a stalled upgrade means `talosctl upgrade --drain=false` against a node, which needs a talosconfig (effectively cluster-root) and is not reversible the way a pod restart is. The agent diagnoses and hands you the command. The skill needs `get`/`list` on `poddisruptionbudgets`, `jobs`, and the `tuppr.home-operations.com` CRDs.

## Cluster Health Check

A background sweep, every 10 minutes by default, that pushes a notification when the cluster degrades and another when it recovers. It runs automatically — there is nothing to enable.

**Why it exists, and why it is not just another Alertmanager rule.** In a typical home cluster the alerting stack shares a failure domain with the failures it reports. If ntfy, Alertmanager and Gatus sit on node-pinned storage (`local-path-provisioner` and friends), whichever node holds them takes all three down together — so the one incident guaranteed to silence your alerting is a node going away, which is also the incident you most need to hear about. Silence is not evidence of health. This agent holds no PVC, so it reschedules freely and can still report.

| Area | What it catches |
|---|---|
| **Nodes** | NotReady, cordoned, upgrade taints — including a node left cordoned after a failed upgrade, which is invisible to anything watching only pods |
| **Pods** | Pending beyond a threshold, CrashLoopBackOff |
| **Flux** | Kustomizations and HelmReleases that are failing, or stuck mid-reconcile |
| **Upgrades** | [tuppr](https://github.com/home-operations/tuppr) `TalosUpgrade` / `KubernetesUpgrade` in a failed phase |
| **Database** | CNPG `readyInstances` below `instances` — invisible to pod checks, because the pods stay Running while replication is broken |
| **Backups** | Volsync `lastSyncTime` age — a nightly snapshot that stops usually surfaces only when someone needs a restore |

**It correlates rather than lists.** "6 pods Pending" is a symptom list; the diagnosis is what saves you the twenty minutes:

```
● talosupgrade 'talos' is in phase Failed
    k8s-1: Upgrade Job failed while node remained at v1.13.9

● 1 node(s) unhealthy or cordoned
    k8s-1: cordoned (taint: tuppr.home-operations.com/outdated)

● 6 pod(s) Pending for over 10m
  → pinned by local-path PVCs to k8s-1, which is unschedulable
    monitoring/ntfy-566685b584-4vctr (pinned to k8s-1)
    ...
```

Resolving each Pending pod through its PVC to the PV's `nodeAffinity` is what buys the `→` line, and is the one reason the check wants `persistentvolumes` read access. Cause is printed above effect on purpose.

**It reports and never acts.** Recovering a stalled node upgrade means `talosctl upgrade --drain=false`, which needs a talosconfig and is not reversible the way a pod restart is — the same reasoning that keeps the [Talos skill](#skills) read-only.

### What it needs from your cluster

Only `nodes` and `pods` are required. **Everything else is optional and skipped automatically** — a missing CRD (404) or absent RBAC (403) costs you that one check, never the cycle, so the worker is useful on a cluster with no CNPG, no Volsync, no tuppr and no Flux. Losing `persistentvolumes` costs the `→` explanation but not the finding, and says so in the logs.

If you are not on node-pinned storage the pod-correlation matters less, but the node, upgrade and backup checks are unaffected.

### Notification behaviour

Transitions only: one push when the cluster becomes degraded, one when it recovers, then a re-nag every 4 hours while it stays broken. A still-degraded cycle does **not** re-notify, so a 10-minute interval does not mean six pushes an hour.

Degraded pushes are classified as needing your attention, so the [notification level](#features) setting cannot silence them. Warnings — a stale backup, a crash-looping pod — are reported but do not by themselves mark the cluster degraded.

The check honours the global **Kill Switch** (`agent_enabled`) like every other worker.

> **Worth knowing:** the default `NTFY_URL` points at an in-cluster ntfy. If your ntfy is itself node-pinned, the agent will *notice* an incident it cannot *tell you about*. Pointing `NTFY_URL` at an ntfy outside the cluster (or a hosted one) removes the last shared failure domain.

### Tuning

`HEALTH_CHECK_INTERVAL_SECONDS` sets the cycle. The rest are stored settings with no UI yet — they have sane defaults and most people never touch them, but each can be overridden in the `settings` table, and a stored value beats the environment:

| Setting | Default | Meaning |
|---|---|---|
| `health_check_interval_seconds` | `600` | Cycle length |
| `health_check_pending_pod_minutes` | `10` | How long a pod may sit Pending before it counts |
| `health_check_backup_max_age_hours` | `36` | Backup age that counts as stale |
| `health_check_renag_seconds` | `14400` | Re-nag interval while still degraded |

## Memory

The agent automatically extracts key facts from conversations and stores them in PostgreSQL. Memories are loaded into the system prompt for all future interactions.

**What it remembers:**
- Recurring issues and their fixes
- Architectural knowledge (e.g., PVC node pinning behavior)
- User preferences
- Configuration details

**What it ignores:**
- Transient state (current pod placement, running status)
- Greetings and small talk
- Information already in the cluster context prompt

Memories are viewable, addable and deletable from the **Memories** page in the sidebar.

**Adding your own.** Extraction only runs on chat conversations, so a fact learned during an alert investigation — or in another repo entirely — has no automatic path in. Use **Add memory** on that page, or `create_memory` over the [MCP endpoint](#mcp-endpoint).

**Staleness.** A point-in-time observation stored as a fact would read as present tense forever and could contradict what the live tools report. Two guards: entries in the `issue` category are dropped from the prompt after 7 days (they stay visible in the UI), and every line carries its age so the model can weigh an old fact against a fresh tool result. `issue` means a *recurring* pattern, not a current incident.

## GitHub Tools

The agent has full PR workflow capabilities:

| Tool | What it does |
|------|-------------|
| `github_list_prs` | List open pull requests |
| `github_get_pr` | Get PR details (diff stats, labels, merge status) |
| `github_get_pr_files` | Get changed files with diffs |
| `github_get_check_runs` | Check CI status |
| `github_create_pr_comment` | Post review comments |
| `github_merge_pr` | Squash merge (when auto-merge enabled) |
| `github_get_file_content` | Read files from the repo |
| `github_create_branch` | Create a branch (fix/, feat/, agent/ prefixes) |
| `github_create_commit` | Push file changes to a branch |
| `github_get_release` | Get release notes for a version |
| `github_create_pr` | Open a pull request |

## MCP Endpoint

A read-only MCP-over-HTTP endpoint at `/mcp` exposes the agent's own record, so you can inspect what it has been doing from a coding session rather than clicking through the web UI. Diagnosing a bad PR review usually means reading the tool-call trace, and nothing else surfaces that.

**Disabled unless `MCP_API_TOKEN` is set** — with no token the endpoint is never mounted. When set, every request needs `Authorization: Bearer <token>`.

| Tool | What it gives you |
|------|-------------------|
| `agent_tasks` | What has run, filterable by type, status and recency |
| `task_detail` | One task in full: its conversation and every tool call and result |
| `conversations` | Conversation threads, including chats (which produce no task row) |
| `conversation_detail` | Every message in one conversation |
| `memories` | What is in the prompt, with age and whether each entry is hand-written |
| `agent_status` | Providers, assigned models, enabled skills, PR mode, last check outcome |
| `costs` | Tokens and spend by model |
| `create_memory` | Record a durable fact — the one write besides deleting one |
| `delete_memory` | Remove one memory by id |

Everything is read-only except the two memory tools. Those exist because curating memories by hand is the recurring chore, and because the result is visible in the UI and undone with a single delete. Nothing here changes settings, models, prompts, or triggers a run.

Add it to your editor's MCP config (note the trailing slash):

```json
{
  "mcpServers": {
    "home-ops-agent": {
      "type": "http",
      "url": "https://agent.example.com/mcp/",
      "headers": { "Authorization": "Bearer ${MCP_API_TOKEN}" }
    }
  }
}
```

Keep the literal token out of a committed file — use an environment variable, or gitignore the config.

## Safety

Code-level guardrails that cannot be bypassed by the LLM:

- **Protected branches**: Cannot commit directly to `main` or `master`
- **Branch naming**: Can only create branches starting with `fix/`, `feat/`, or `agent/`
- **Path restrictions**: Can only modify files under `kubernetes/apps/` — in checkout mode this is applied to the whole staged diff, with renames resolved so a `git mv` cannot smuggle a file out of an allowed path
- **Protected namespaces**: Cannot restart or delete pods in `kube-system`, `flux-system`, `cert-manager`
- **RBAC**: ClusterRole has no `create` or `delete` on deployments/namespaces — only `get`, `list`, `watch`, `patch`
- **Rate limiting**: Max 3 PR reviews per cycle
- **Duplicate protection**: Won't re-review a PR unless the head SHA changes
- **Kill switch**: Instantly disable all agent activity from the UI
- **Merge logging**: Every merge attempt is logged at WARNING level, and a failed merge notifies you
- **Secret masking**: Checkout mode enables a shell, so `DATABASE_URL`, `SESSION_SECRET`, `NTFY_TOKEN`, `GITHUB_TOKEN` and the provider keys are blanked in that environment. The GitHub token reaches `git` only as a per-invocation argument, never `.git/config`.
- **No node operations**: The Talos skill exposes no mutating tool; node upgrades stay manual. The [health check](#cluster-health-check) is read-only for the same reason — it diagnoses a stalled upgrade and hands you the command

## Development

```bash
# Lint (CI runs both; a failing lint skips the image build)
uvx ruff check src/ tests/
uvx ruff format --check src/ tests/

# Tests
uv run python -m pytest tests/ -v

# Frontend — only when changing the Next.js app. Its dependencies live in web/,
# so a root-level install does not cover it.
cd web && pnpm install --frozen-lockfile
pnpm build
```

The image installs from `uv.lock`, so the container and the test suite run the same dependency versions.

## Releasing

Images are built on version tags only:

```bash
git tag v0.10.1
git push origin v0.10.1
```

This triggers GitHub Actions to build and push to `ghcr.io/<your-username>/home-ops-agent:0.10.1`.

Update the image tag in your HelmRelease to deploy.

## License

MIT
