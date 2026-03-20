# Home-Ops Agent

An autonomous operator for home Kubernetes clusters. Runs inside your cluster and uses Claude to review PRs, diagnose alerts, fix issues, and provide an interactive chat interface.

Built for GitOps setups using [Flux Operator](https://github.com/controlplaneio-fluxcd/flux-operator), but works with any Kubernetes cluster that has Prometheus, Loki, and ntfy.

## Features

- **PR Review** — Monitors your GitHub repo for open PRs (primarily Renovate dependency updates). Posts review comments with risk assessment. Optional auto-merge for safe patches.
- **Alert Investigation** — Subscribes to ntfy topics (Alertmanager, Gatus) and automatically investigates when alerts fire. Checks pods, logs, metrics, events, and Flux status.
- **Auto-Fix** — Can restart stuck pods, reconcile Flux resources, create fix branches, commit changes, and open PRs. Every action is logged and reported via ntfy.
- **Interactive Chat** — Web UI where you can ask questions about cluster state, run diagnostics, or issue commands. Conversations persist across page refreshes.
- **Persistent Memory** — Extracts key facts from conversations (issues, fixes, preferences, architectural knowledge) and remembers them across future interactions. Memories are viewable and deletable in the UI.
- **Per-Task Models** — Assign different Claude models to each agent (e.g., Haiku for cheap PR reviews, Sonnet for complex fixes). Configurable via the Settings UI.
- **Customizable Prompts** — Edit system prompts per agent through modal editors to describe your specific cluster setup.
- **Activity History** — View all agent actions and chat conversations with full reasoning and tool call details. Click a conversation to reopen it.
- **Kill Switch** — Instantly disable all agent activity from the Settings UI. One click to stop, one click to resume.
- **Safety Guardrails** — Code-level protections prevent commits to main, modifications outside `kubernetes/apps/`, and destructive actions in system namespaces.

## Architecture

![Architecture](architecture.png)

Single Python container. Single async process. Background workers as asyncio tasks.

## Prerequisites

- Kubernetes cluster with Flux Operator (or any GitOps tool)
- [CloudNativePG](https://cloudnative-pg.io/) (PostgreSQL) — for conversations, memories, settings, and task logs
- [ntfy](https://ntfy.sh/) — for alert subscriptions and notifications
- Prometheus + Loki — for metrics and log queries (optional, via Grafana MCP sidecar)
- [Anthropic API key](https://console.anthropic.com/settings/keys) — for Claude access
- GitHub fine-grained personal access token — with `Contents: Read/Write` and `Pull requests: Read/Write` on your repo

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

Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**:

- **Repository access**: Only select your GitOps repo
- **Permissions**: Contents (Read/Write), Pull requests (Read/Write)

### 3. Create an ntfy user (if auth is enabled)

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
  NTFY_TOKEN: "tk_..."
```

Encrypt with SOPS if using Flux.

### 5. Deploy

Use the [bjw-s app-template](https://github.com/bjw-s-labs/helm-charts/tree/main/charts/library/common) Helm chart. See the [example manifests](kubernetes/) for reference.

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
| `BASE_URL` | Public URL of the agent web UI | — |

### 6. Configure via the web UI

Open the agent's web UI and go to **Settings**:

1. **API Key** — Paste your Anthropic API key
2. **Cluster Context** — Describe your cluster (nodes, IPs, domain, infrastructure). This is prepended to all agent prompts.
3. **Agents** — Choose which Claude model each agent uses and customize their prompts
4. **PR Mode** — Start with "Comment Only", switch to "Auto-Merge" once you trust the reviews
5. **Kill Switch** — Disable/enable all agent activity instantly

### 7. Subscribe to notifications

In the ntfy mobile app, subscribe to the `home-ops-agent` topic on your ntfy server to receive agent reports.

## Agents

| Agent | Default Model | What it does |
|-------|--------------|-------------|
| **PR Review** | Haiku 4.5 | Reviews open PRs, posts comments with risk assessment |
| **Alert Triage** | Haiku 4.5 | First responder — checks pods, logs, metrics |
| **Alert Fix** | Sonnet 4.6 | Takes corrective action — restarts pods, reconciles Flux |
| **Code Fix** | Sonnet 4.6 | Creates branches, commits fixes, opens PRs |
| **Chat** | Sonnet 4.6 | Interactive conversation about cluster state |

All models and prompts are configurable via the Settings UI.

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

Memories are viewable and deletable from the **Memories** page in the sidebar.

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
| `github_create_pr` | Open a pull request |

## Safety

Code-level guardrails that cannot be bypassed by the LLM:

- **Protected branches**: Cannot commit directly to `main` or `master`
- **Branch naming**: Can only create branches starting with `fix/`, `feat/`, or `agent/`
- **Path restrictions**: Can only modify files under `kubernetes/apps/`
- **Protected namespaces**: Cannot restart or delete pods in `kube-system`, `flux-system`, `cert-manager`
- **RBAC**: ClusterRole has no `create` or `delete` on deployments/namespaces — only `get`, `list`, `watch`, `patch`
- **Rate limiting**: Max 3 PR reviews per cycle
- **Duplicate protection**: Won't re-review a PR unless the head SHA changes
- **Kill switch**: Instantly disable all agent activity from the UI
- **Merge logging**: Every merge attempt is logged at WARNING level

## Development

```bash
# Install dependencies
uv pip install -e ".[dev]"

# Run linting
uvx ruff check src/
uvx ruff format --check src/

# Run tests
pytest
```

## Releasing

Images are built on version tags only:

```bash
git tag v0.6.0
git push origin v0.6.0
```

This triggers GitHub Actions to build and push to `ghcr.io/<your-username>/home-ops-agent:0.6.0`.

Update the image tag in your HelmRelease to deploy.

## License

MIT
