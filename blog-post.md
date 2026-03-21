# Building an Agentic Kubernetes Home Lab

I run a 3-node Kubernetes cluster at home for media, automation, and learning. Recently I built an autonomous AI agent that lives inside the cluster, reviews pull requests, diagnoses alerts, fixes issues, and lets me chat with my infrastructure through a web UI. This post covers the full setup — the cluster itself, the GitOps workflow, and the agent that operates it.

## The Cluster

The hardware is three Minisforum MS-01 mini PCs, each with a 13th-gen Intel CPU, 16 GB DDR5 RAM, and a 1 TB Samsung 990 Pro NVMe. They run Talos Linux — a minimal, immutable OS purpose-built for Kubernetes. No SSH, no shell, no package manager. You manage it entirely through an API.

All three nodes serve as both control plane and worker. There are no dedicated workers — every node runs the full Kubernetes stack plus application workloads.

<!-- DIAGRAM: Photo or diagram of the 3 MS-01 nodes with specs -->

### Networking

The nodes sit on a dedicated SERVERS VLAN (192.168.42.0/24) behind a UniFi Dream Machine Pro. Key IPs are reserved:

- .10 for the Kubernetes API
- .11 for internal DNS (k8s-gateway)
- .12/.13 for internal/external ingress (Envoy Gateway)
- .14 for AdGuard Home (DNS for the whole network)
- .100-.102 for the three nodes

Cilium handles CNI and L2 announcements. Envoy Gateway provides ingress with automatic TLS via cert-manager and Let's Encrypt. External access goes through a Cloudflare Tunnel — nothing is exposed directly to the internet.

## GitOps with Flux

Everything in the cluster is defined as code in a single GitHub repository. The Flux Operator watches the repo and reconciles the desired state continuously. Push a change to main, and Flux deploys it within minutes.

<!-- DIAGRAM: GitOps flow — GitHub repo → Flux Operator → Kubernetes cluster, with Renovate creating PRs -->

### How applications are structured

Every application follows the same pattern using the bjw-s app-template Helm chart:

```
kubernetes/apps/<namespace>/<app>/
├── ks.yaml              # Flux Kustomization
└── app/
    ├── kustomization.yaml
    ├── ocirepository.yaml
    ├── helmrelease.yaml  # The actual app config
    └── secret.sops.yaml  # Encrypted secrets
```

Secrets are encrypted with SOPS using age keys — they live safely in the git repo, encrypted at rest, and Flux decrypts them at deploy time.

### Dependency updates with Renovate

Renovate runs on a schedule, scanning the repo for outdated container images, Helm charts, and GitHub Actions. It creates pull requests automatically — sometimes 5-10 per week. Each PR runs through flux-local validation in CI to catch manifest errors before merging.

This works well, but reviewing and merging these PRs manually is tedious. Most are straightforward patch bumps that just need someone to check CI is green and click merge. That's what led me to build the agent.

## What's Running

The cluster hosts a complete home infrastructure stack:

**Media:** Jellyfin, Sonarr, Radarr, Prowlarr, Bazarr, qBittorrent, Recyclarr, Seerr, FlareSolverr

**Monitoring:** Prometheus, Grafana, Loki, Alloy, ntfy, Gatus, smartctl-exporter, Unpoller

**Automation & AI:** n8n, Qdrant (vector DB), crawl4ai, and the home-ops-agent

**Infrastructure:** AdGuard Home, k8s-gateway, Cloudflare Tunnel, external-dns, Envoy Gateway, cert-manager, Authentik (SSO)

**Database:** CloudNativePG (PostgreSQL 16 with pgvecto.rs), Valkey (Redis-compatible cache)

**Backup:** Volsync replicates PVCs to Cloudflare R2 for off-site backup

**Storage:** local-path-provisioner for now — each PVC is pinned to the node where it was created. A NAS upgrade with democratic-csi is planned for the future.

## The Problem: Manual Operations

The GitOps setup is great for deployment, but day-to-day operations still required manual work:

1. **PR review fatigue** — Renovate creates PRs constantly. Most are safe patch bumps, but you still need to check each one, verify CI passes, and merge manually.

2. **Alert noise** — Prometheus fires alerts through Alertmanager to ntfy on my phone. I get a notification that says something like "KubePodCrashLooping" — but then I need to SSH in (well, kubectl in), check logs, figure out what happened, and decide what to do.

3. **Context switching** — When something breaks at 10 PM, I need to open a terminal, remember which namespace the app is in, check the right logs, and piece together what went wrong. It's a lot of cognitive load for a home lab.

I wanted something that could handle the routine stuff autonomously and give me better information when it couldn't.

## Building the Home-Ops Agent

The home-ops-agent is a Python application that runs inside the cluster as a regular deployment. It uses the Anthropic Claude API to reason about the cluster and take actions through a set of tools.

<!-- DIAGRAM: The architecture diagram (architecture.png from the repo) -->

### How it works

The agent has three main operating modes:

**PR Monitor** — Every 30 minutes, it checks GitHub for open pull requests. For each new PR, it reads the diff, checks CI status, assesses the risk level, and posts a review comment. It knows which components are critical (Cilium, Flux, Envoy Gateway, cert-manager) and flags those for manual review, while simple patch bumps get a "safe to merge" recommendation.

**Alert Subscriber** — It maintains a persistent SSE connection to ntfy, listening to the alertmanager and gatus topics. When an alert fires, the agent investigates: it checks pod status, reads logs, queries Prometheus metrics, looks at Flux reconciliation status, and checks recent events. If it can fix the issue (restart a stuck pod, reconcile a Flux resource), it does so and sends me a notification explaining what happened. If it can't fix it, it sends an enriched diagnosis instead of just forwarding the raw alert.

**Interactive Chat** — A web UI where I can ask questions like "What pods are running in the media namespace?" or "Why is Sonarr using so much memory?" The agent uses its tools to get live data and responds with actual cluster state, not memorized information.

### Per-task model selection

Different tasks have different complexity, and Claude models have very different costs. The agent uses cheaper models for simple tasks:

| Task | Model | Why |
|------|-------|-----|
| PR Review | Haiku 4.5 | Reading diffs, checking labels — mechanical |
| Alert Triage | Haiku 4.5 | Check pods, read logs, pattern match |
| Alert Fix | Sonnet 4.6 | Needs reasoning for corrective actions |
| Code Fix | Sonnet 4.6 | Understanding schemas, writing YAML |
| Chat | Sonnet 4.6 | Good balance for conversation |

All models are configurable through the settings UI. I can bump Code Fix to Opus for a tricky migration, then switch it back.

### Persistent memory

The agent extracts key facts from conversations and remembers them across future interactions. After each chat, Haiku analyzes the conversation and saves structural knowledge — things like "local-path-provisioner PVCs are pinned to their creation node via PV nodeAffinity" or "user prefers ntfy notifications for all restarts."

This means the agent learns about my specific cluster over time. It won't suggest adding a `nodeSelector` to fix PVC scheduling if it already knows the provisioner handles that automatically.

Memories are stored in PostgreSQL and viewable in the UI. I can delete incorrect ones or let them accumulate naturally through conversations.

### Safety guardrails

Giving an AI agent access to your Kubernetes cluster and GitHub repo requires careful constraints. All safety measures are enforced at the code level — they cannot be bypassed by prompt injection or creative reasoning:

- **Protected branches** — Cannot commit directly to `main` or `master`
- **Branch naming** — Can only create branches starting with `fix/`, `feat/`, or `agent/`
- **Path restrictions** — Can only modify files under `kubernetes/apps/`
- **Protected namespaces** — Cannot restart or delete pods in `kube-system`, `flux-system`, or `cert-manager`
- **RBAC** — The ServiceAccount has minimal permissions: read most things, patch deployments (for restarts), delete pods (for recreation). No create, no namespace modification.
- **Rate limiting** — Maximum 3 PR reviews per cycle to prevent runaway token usage
- **Duplicate protection** — Won't re-review a PR unless the head SHA changes
- **Kill switch** — One button in the UI to disable all autonomous activity immediately

### The full PR workflow

When the agent identifies a fix (like a breaking change in a Renovate PR), it can:

1. Create a new branch (`fix/`, `feat/`, or `agent/` prefix)
2. Commit the corrected manifest to the branch
3. Open a pull request for review

It never pushes directly to main. Every change goes through the normal PR review process — which the agent itself might then review on the next cycle.

## The Web UI

The agent has a simple web interface with four sections:

**Chat** — WebSocket-based conversation with the agent. Persists across page refreshes. Responses show which tools were used (k8s_get_pods, github_list_prs, etc).

**History** — Chronological feed of all agent activity: PR reviews, alert investigations, and chat conversations. Click any entry to see the full conversation and reasoning.

**Memories** — View everything the agent has learned. Each memory is categorized (issue, fix, preference, knowledge, config) and deletable if incorrect.

**Settings** — Configure everything without restarting:
- Kill switch to disable/enable the agent
- PR review mode (comment-only or auto-merge)
- API key management
- Per-agent model selection with descriptions of what each agent does
- Customizable system prompts (cluster context shared across all agents, plus per-agent instructions)
- Alert cooldown and PR check intervals

<!-- DIAGRAM: Screenshot of the Settings page showing the agent cards with model dropdowns -->

## Lessons Learned

**Start with comment-only mode.** Let the agent review PRs and post comments before enabling auto-merge. This builds trust and lets you see how it reasons about changes.

**Teach it through conversation.** The memory system means you can correct the agent once and it remembers. When it suggested an unnecessary `nodeSelector`, I explained why PV nodeAffinity handles it automatically. It extracted that as a memory and won't make the same mistake again.

**Cheap models for cheap tasks.** Haiku is ~60x cheaper than Opus per token. PR reviews don't need the most powerful model — reading a diff and checking labels is mechanical work. Save the expensive models for tasks that actually need reasoning.

**Code-level safety, not prompt-level.** System prompts can be ignored or overridden through creative prompting. The guardrails that matter are in Python: hard-coded protected branches, path allowlists, namespace blocklists. These cannot be bypassed regardless of what the LLM is asked to do.

**The kill switch is essential.** If the agent starts doing something unexpected, you need to stop it immediately — not wait for the current operation to finish, not hope the cooldown kicks in. One button to disable everything.

## Cost

The Anthropic API costs for a home lab agent are modest. With Haiku handling PR reviews and alert triage, and Sonnet for fixes and chat, a typical week costs a few dollars. The PR monitor checks every 30 minutes but only calls Claude when there's a new PR to review. The alert subscriber only uses tokens when an alert actually fires.

## What's Next

The agent is functional but there's room to grow:

- **Grafana and Flux MCP sidecars** — Currently the agent queries Kubernetes directly. Adding the Grafana and Flux Operator MCP servers as sidecar containers would give it richer observability and GitOps tools.
- **Volsync backup monitoring** — Alert when backups fail or fall behind schedule.
- **Authentik SSO** — Protect the web UI with forward auth once SSO configuration is complete.
- **Smarter auto-merge** — Graduate from comment-only to auto-merging safe Renovate patches after enough successful reviews build confidence.

## Try It Yourself

The home-ops-agent is open source and designed to work with any Flux-based Kubernetes cluster that has Prometheus, Loki, and ntfy.

- **Source code:** [github.com/MarkNygaard/home-ops-agent](https://github.com/MarkNygaard/home-ops-agent)
- **Cluster manifests:** [github.com/MarkNygaard/home-ops](https://github.com/MarkNygaard/home-ops)

The setup requires:
- A Kubernetes cluster with CloudNativePG (PostgreSQL)
- An ntfy server for alert subscriptions
- An Anthropic API key
- A GitHub fine-grained personal access token

All configuration happens through the web UI after deployment — system prompts, models, intervals, and your cluster context. No hardcoded values to change in the source code.

---

*Built with Claude Code and deployed via Flux. The agent reviewed its own Renovate PR for the first time three hours after deployment.*
