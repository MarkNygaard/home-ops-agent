# Claude Context — Home-Ops Agent

## What this is

A Python/FastAPI application that runs inside a Kubernetes cluster as an autonomous operator. Uses the Anthropic Claude API to review PRs, diagnose cluster alerts, fix issues, and provide an interactive chat interface.

## Project structure

```
src/home_ops_agent/
├── main.py                 # FastAPI app entry point, lifespan, background workers
├── config.py               # pydantic-settings from env vars
├── database.py             # SQLAlchemy async models (Conversation, Message, Memory, AgentTask, Setting)
├── agent/
│   ├── core.py             # Agent class — provider-aware tool-use loop (Anthropic/Kimi + OpenAI)
│   ├── claude_code.py      # Claude Code CLI backend (Claude subscription, tools via in-process MCP)
│   ├── workspace.py        # Git worktree checkout + guarded commit/push (claude-code only)
│   ├── providers.py        # Model → provider resolution + provider constants
│   ├── prompts.py          # System prompts with DB overrides, memory loading
│   ├── models.py           # Per-task model resolution (DB → env fallback, includes deep_review)
│   ├── memory.py           # Memory extraction (Haiku) and loading
│   ├── skills.py           # Skills registry — groups tools into enable/disable-able bundles
│   └── tools/
│       ├── kubernetes.py   # K8s API tools (pods, logs, events, restart, delete) — built-in
│       ├── github.py       # GitHub API tools (PRs, commits, branches, files, releases) — built-in
│       ├── ntfy.py         # ntfy publish with auth — built-in
│       ├── prometheus.py   # PromQL queries, metrics, alerts — optional skill
│       ├── loki.py         # LogQL queries, label listing — optional skill
│       ├── flux.py         # Flux Kustomization/HelmRelease management — optional skill
│       └── talos.py        # Talos/tuppr upgrade diagnostics (read-only) — optional skill
├── workers/
│   ├── pr_monitor.py       # Periodic PR review (4-tier mode, deep review, code fix auto-merge)
│   └── alert_subscriber.py # ntfy JSON stream — two-stage alert pipeline (triage → fix)
├── auth/
│   ├── credentials.py      # Multi-provider credential resolution + OpenAI token refresh
│   └── session.py          # Simple in-memory session store (legacy)
├── mcp/
│   ├── client.py           # MCP stdio client for sidecar servers
│   ├── bridge.py           # Converts MCP tools to Claude tool definitions
│   └── server.py           # Read-only MCP-over-HTTP endpoint (/mcp) exposing the agent's own record
├── api/
│   ├── chat.py             # WebSocket chat endpoint with memory extraction
│   ├── status.py           # REST: health, history, conversations, memories
│   ├── settings.py         # REST: settings CRUD, prompts CRUD, provider credential import
│   └── skills.py           # REST: skill listing, enable/disable, config updates
└── static/                 # Next.js static export (built from web/, served by FastAPI)
web/                        # Next.js frontend (shadcn/ui) — builds to static/ via Dockerfile
```

## Key commands

```bash
# Lint (ALWAYS run both before committing — CI will fail otherwise)
uvx ruff check src/ tests/
uvx ruff format --check src/ tests/

# Auto-fix formatting
uvx ruff format src/ tests/

# Run tests
uv run python -m pytest tests/ -v

# Run locally (needs DATABASE_URL and ANTHROPIC_API_KEY env vars)
uvicorn home_ops_agent.main:app --host 0.0.0.0 --port 8000

# Frontend (web/) — ONLY when changing the Next.js app. Its deps live in web/,
# not the repo root, so install there separately (a root-level install does NOT
# cover the frontend). Run lint + build to validate web changes.
cd web && pnpm install --frozen-lockfile
pnpm lint     # eslint (eslint.config.mjs)
pnpm build    # next build → static export into ../src/home_ops_agent/static

# Build Docker image
docker build -t home-ops-agent .

# Release (triggers CI build + push to GHCR)
git tag v0.x.y && git push origin v0.x.y
```

## Commit workflow

1. Run `uvx ruff check src/ tests/` and `uvx ruff format --check src/ tests/` — fix any issues before committing
2. Commit on a feature branch (never push directly to main)
3. **Always tag** the commit with the next patch version (e.g. `v0.10.11`) — the tag triggers the CI build that pushes the Docker image to GHCR. Without a tag, no image is built. Check existing tags with `git tag --sort=-creatordate | head -5` and increment accordingly.
4. Push both the branch and tag, then create a PR — CI runs lint then build (build is skipped if lint fails)
5. After merge, Renovate detects the new GHCR image and opens a PR in home-ops to update the deployment

## Important patterns

- **AsyncAnthropic** — Must use `anthropic.AsyncAnthropic` (not `Anthropic`) since the app is fully async (FastAPI + asyncio workers). Synchronous client blocks the event loop. The OpenAI provider likewise uses `openai.AsyncOpenAI`.
- **Multi-provider routing** — `agent/core.py` `Agent` is provider-aware: a single agent can run Claude, Kimi, and GPT/Codex models. `agent/providers.py` resolves a model ID to its provider by prefix (`claude-code/*`→claude_code, `claude-*`→anthropic, `kimi-*`→kimi, `gpt-*`/`codex-*`/`o3*`→openai). Anthropic and Kimi share the Anthropic wire protocol (Kimi via its Anthropic-compatible endpoint, base URL `https://api.kimi.com/coding/`); OpenAI/Codex use the ChatGPT-backend Responses API. Provider is resolved per `run()` call, so workers build one agent and use any model.
- **Claude Code backend** — `agent/claude_code.py` runs a task on a **Claude Pro/Max subscription** instead of API credit, by driving the Claude Code CLI through `claude-agent-sdk`. Selected with a `claude-code/` model prefix (`claude-code/sonnet`, `claude-code/opus`); the suffix is passed to the CLI as `--model`. Registered tools are handed to the CLI as an **in-process MCP server** (`mcp__homeops__*`), so tool handlers and their guardrails are unchanged. Claude Code's own built-ins are removed (`tools=[]`), so the agent can only call our tools — it never gets a shell or filesystem. Runs are recorded at $0 in `costs.py` (subscription, not metered). Two differences from the API backends: message history is flattened into one prompt (the CLI takes a single prompt), and `ANTHROPIC_API_KEY` is blanked in the child env so a stray key can't silently bill API credit.
- **Credentials** — `auth/credentials.py` `build_credentials()` loads all provider creds from `settings` rows (no global auth toggle). Anthropic & Kimi use API keys; OpenAI uses an imported ChatGPT-subscription OAuth token (`openai_access_token`/`openai_refresh_token`/`openai_account_id`) that the server keeps refreshed via `auth.openai.com/oauth/token`.
- **Model IDs** — Use short form: `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-8`, `kimi-for-coding`, plus OpenAI IDs (e.g. `gpt-5.5`, `codex-5.3`) and subscription IDs (`claude-code/sonnet`). No date suffixes.
- **Tool-use loop** — `agent/core.py` implements: send message → get tool_use → execute → send tool_result → repeat until text response.
- **DB settings override env** — Settings stored in PostgreSQL take priority over environment variables. The UI writes to DB.
- **Memory extraction** — Runs in background after each chat using Haiku. Extracts structural facts, not transient state. Note the asymmetry: memories are **written only by chat** (`api/chat.py` is the sole caller of `extract_memories`) but **read by every agent** through `get_prompt()`. Facts learned during an alert investigation, a PR review, or outside this agent entirely have no automatic path in — that is what `POST /api/memories` is for.
- **Memory staleness** — Memories are injected into every system prompt, so an incident snapshot stored as a fact reads as present tense forever and can contradict what the live tools report. Two guards: `memory.PERISHABLE_CATEGORIES` (`issue`) is dropped from the prompt after `PERISHABLE_MAX_AGE` (7 days), and every rendered line carries its age. Filtering happens **before** the row limit, so a burst of incidents cannot evict durable knowledge out of the window. The extraction prompt separately rejects "currently"/"recently"-shaped statements and requires `issue` to mean a *recurring* pattern.
- **Skills system** — Tools are grouped into skills (`agent/skills.py`). Built-in skills (kubernetes, github, ntfy) are always enabled. Optional skills (prometheus, loki, flux, talos) can be toggled and configured via the Settings UI. Each skill defines its own tools and config fields.
- **Talos diagnostics are read-only on purpose** — `agent/tools/talos.py` inspects tuppr `TalosUpgrade`/`KubernetesUpgrade` CRs, upgrade Jobs, node taint/cordon state, and *why a node cannot be drained* (the PDB-allows-zero-disruptions case that makes an upgrade hang rather than fail). It deliberately exposes **no** mutating tool. Recovery means `talosctl upgrade --drain=false` against a node, which needs a talosconfig (effectively cluster-root) and is not reversible the way a pod restart or a PR-branch commit is — a different risk class from every other guardrail in this codebase. The agent diagnoses and hands the user the command; `prompt_alert_response` states this explicitly. Promote an action here only once the `agent_tasks` history shows the same diagnosis leading to the same command repeatedly.
- **4-tier PR mode** — `comment_only` → `auto_merge` (patch) → `auto_merge_minor` → `auto_merge_all` (fully autonomous). In `auto_merge_all`, PRs flagged `NEEDS_REVIEW` are escalated to the `deep_review` model (Opus) for a second opinion.
- **Two-stage alert pipeline** — Alerts go through triage (Haiku, cheap/fast) first. Triage returns `fix`, `notify`, or `ignore`. Only `fix` escalates to the Alert Fix agent (Sonnet).
- **Code fix auto-merge** — When a PR review flags `NEEDS_FIX`, the Code Fix agent pushes a commit to the PR branch, then polls CI for up to 5 minutes. If CI passes, it auto-merges.
- **Two code-fix paths** — The Code Fix agent has two modes, chosen in `workers/pr_fix.py` by whether the resolved `code_fix` model is a `claude-code/*` one:
  - **Checkout mode** (`claude-code/*` only) — `agent/workspace.py` clones home-ops and adds a `git worktree` on the PR branch. The Claude Code CLI runs with `cwd` set to it and its `Read`/`Write`/`Edit`/`Glob`/`Grep`/`Bash` tools enabled, so the agent can grep the whole repo, edit several files, and validate with `kubeconform` before committing. The only way out is the `workspace_commit` tool, which re-applies `PROTECTED_BRANCHES` and `ALLOWED_COMMIT_PATHS` **to the whole staged diff** (with `--no-renames`, so a rename can't smuggle a file out of an allowed path) and unstages everything on a violation.
  - **API mode** (everything else) — the original single-file `github_create_commit` path.
- **Subscription billing is enforced by env, not hope** — the Claude Code CLI has a documented [authentication precedence](https://code.claude.com/docs/en/authentication#authentication-precedence) in which several credential sources outrank `CLAUDE_CODE_OAUTH_TOKEN`. `claude_code._PRECEDENCE_ENV` blanks all of them (`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_PROFILE`, `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`) on **every** `claude-code/*` run, so a key left in the pod env cannot silently move the run onto metered API credit. The `CLAUDE_CODE_USE_BEDROCK`/`VERTEX`/`FOUNDRY` flags are read as flags rather than values — blanking one could read as "set" — so those are logged as a warning instead (`_PROVIDER_FLAGS`).
- **Workspace secret masking** — Enabling `Bash` gives the agent a shell in the pod, so `claude_code._MASKED_ENV` additionally blanks `DATABASE_URL`, `SESSION_SECRET`, `NTFY_TOKEN`, `GITHUB_TOKEN` and the other provider keys. The GitHub token is passed to `git` in a per-invocation URL and never written to `.git/config`, so `git remote -v` inside the worktree does not reveal it.

## Safety guardrails (code-level, not prompt-level)

- `PROTECTED_BRANCHES = {"main", "master"}` — cannot commit to these
- `ALLOWED_COMMIT_PATHS = {"kubernetes/apps/"}` — can only modify manifests
- `PROTECTED_NAMESPACES = {"kube-system", "flux-system", "cert-manager"}` — no pod restarts/deletes
- Branch names must start with `fix/`, `feat/`, or `agent/`
- Max 3 PR reviews per cycle (rate limit)
- Kill switch: `agent_enabled` setting disables all workers

## Authentication

Four providers can be configured simultaneously (any subset). The model assigned to each task picks the provider.

- **Anthropic** — API key only. (The old Max/Pro OAuth flow was removed; Anthropic does not allow third-party apps to use Consumer subscription OAuth tokens.)
- **Kimi for Coding** — API key from the Kimi Code Console, used against the Anthropic-compatible endpoint `https://api.kimi.com/coding/` (model `kimi-for-coding`).
- **Claude Code** — a Claude Pro/Max **subscription**, via the Claude Code CLI. `claude setup-token` mints a long-lived (1 year) token from an interactive browser flow this hosted server cannot run, so the user runs it locally and pastes the token into Settings (stored as `claude_code_oauth_token`, passed to the CLI as `CLAUDE_CODE_OAUTH_TOKEN`). Unlike the OpenAI tokens it is not refreshable and needs no keep-warm loop. Per the CLI docs the token "authenticates with your Claude subscription and requires a Pro, Max, Team, or Enterprise plan" — it does **not** bill API credit. What does bill API credit is `ANTHROPIC_API_KEY` (and the other sources in `_PRECEDENCE_ENV`) outranking it, which is why those are blanked per run. Pasting `~/.claude/.credentials.json` instead was considered and rejected: its refresh token is single-use and rotates, so sharing it with a developer machine logs one of the two out.
- **OpenAI / ChatGPT** — ChatGPT-subscription OAuth tokens. Because the app is a hosted server (it cannot receive the Codex `localhost:1455` redirect), tokens are **imported** via `POST /api/auth/openai` (authenticate locally first, e.g. `codex login`, then paste `access_token`/`refresh_token`/`account_id`). The server refreshes them automatically using the Codex public client (`OPENAI_CLIENT_ID` in `agent/providers.py`).

Credentials are stored as `settings` rows; disconnect a provider with `DELETE /api/auth/{provider}`.

## MCP server (`/mcp`)

Read-only MCP-over-HTTP for inspecting what the agent has done from a coding session — the record was previously only reachable by clicking through the web UI.

- **Nine tools.** Read: `agent_tasks`, `task_detail` (the full conversation and every tool call — the one that actually diagnoses a bad run), `conversations`, `conversation_detail`, `memories`, `agent_status`, `costs`. Write: `create_memory`, `delete_memory` — and nothing else. No settings, models, prompts, or triggering runs.
- **Why chats need their own tools** — a chat creates a `Conversation` but no `agent_tasks` row, so `agent_tasks` alone misses every chat. `conversations` / `conversation_detail` are the only view of them.
- **The write exception is deliberate and pinned.** Memories are injected into every future system prompt, including agents that can commit to the repo and restart pods, and they are instruction-shaped — so a memory tool is persistent influence on behaviour, not just a note. It earns its place because curating memories by hand is the recurring chore, and the damage is visible in the UI and reversible with one delete. `delete_memory` takes a single id (no bulk delete). `test_write_surface_is_exactly_two_tools` fails if a third write tool is added, so widening it has to be a deliberate act.
- **Disabled unless `MCP_API_TOKEN` is set** — with no token the endpoint is never mounted, so it cannot be exposed by accident. When set, every request needs `Authorization: Bearer <token>`.
- **Two mounting subtleties**, both of which fail at runtime rather than at import:
  - Starlette does not run a mounted sub-app's lifespan, so `main.py` enters `mcp.server.lifespan()` to start the session manager. Without it every request returns "Task group is not initialized".
  - The MCP SDK enables DNS-rebinding protection with an *empty* host allowlist, which rejects everything. `allowed_hosts()` derives the public host from `base_url`, plus localhost and anything in `MCP_ALLOWED_HOSTS`.
- Mounted before the static catch-all in `main.py`, which would otherwise swallow `/mcp`.

## Database

PostgreSQL via CloudNativePG. Tables auto-created by SQLAlchemy on startup (`init_db`).

Key tables:
- `settings` — key/value store for all config (models, prompts, PR mode, skill configs, etc.)
- `conversations` — chat threads, PR reviews, alert investigations
- `messages` — individual messages within conversations (user, assistant, tool_use, tool_result)
- `memories` — extracted facts from conversations (content, category, source_conversation_id)
- `agent_tasks` — tracked background tasks (PR reviews, alert responses, code fixes)

Provider credentials (API keys, imported OpenAI tokens) live in `settings` rows — there is no dedicated tokens table.

Task types used in `agent_tasks.task_type` (the actual Postgres enum in `database.py`): `pr_review`, `pr_merge`, `alert_response`, `alert_triage`, `alert_fix`, `user_chat`, `cluster_fix`, `code_fix`. Note: the database uses a PostgreSQL enum for `task_type` — adding new types requires an ALTER TYPE migration on the enum.

Model keys (used in `models.py` defaults and DB `model_*` settings): `pr_review`, `alert_triage`, `alert_fix`, `code_fix`, `deep_review`, `chat`.

## Deployment

The `claude-agent-sdk` wheel bundles the Claude Code CLI binary and prefers it over any copy on `PATH`, so the image installs no Node/npm for it. The Dockerfile asserts the bundled binary is present and executable at build time.

Checkout mode needs `git` and `kubeconform` (both installed in the runtime stage) and a writable `AGENT_WORKSPACE_DIR` (defaults to `/home/agent/workspace`). No volume is required — the clone is re-created when missing — but mounting one over that path avoids re-cloning on every restart.

The `talos` skill needs `get`/`list` on `poddisruptionbudgets` (policy), `jobs` (batch) and the `tuppr.home-operations.com` CRDs, on top of the existing node/pod permissions. Missing RBAC surfaces as a 403 the tools report as "the agent's ClusterRole is missing get/list permission" rather than a silent empty result.

Deployed via Flux in the home-ops repo under `kubernetes/apps/automation/home-ops-agent/`. Uses bjw-s app-template HelmRelease with:
- ServiceAccount + ClusterRole RBAC
- SOPS-encrypted secret (GITHUB_TOKEN, DATABASE_URL, SESSION_SECRET, NTFY_TOKEN)
- HTTPRoute on envoy-internal
- Gatus health check enabled
