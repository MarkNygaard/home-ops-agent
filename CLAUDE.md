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
│       └── flux.py         # Flux Kustomization/HelmRelease management — optional skill
├── workers/
│   ├── pr_monitor.py       # Periodic PR review (4-tier mode, deep review, code fix auto-merge)
│   └── alert_subscriber.py # ntfy JSON stream — two-stage alert pipeline (triage → fix)
├── auth/
│   ├── credentials.py      # Multi-provider credential resolution + OpenAI token refresh
│   └── session.py          # Simple in-memory session store (legacy)
├── mcp/
│   ├── client.py           # MCP stdio client for sidecar servers
│   └── bridge.py           # Converts MCP tools to Claude tool definitions
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
- **Multi-provider routing** — `agent/core.py` `Agent` is provider-aware: a single agent can run Claude, Kimi, and GPT/Codex models. `agent/providers.py` resolves a model ID to its provider by prefix (`claude-*`→anthropic, `kimi-*`→kimi, `gpt-*`/`codex-*`/`o3*`→openai). Anthropic and Kimi share the Anthropic wire protocol (Kimi via its Anthropic-compatible endpoint, base URL `https://api.kimi.com/coding/`); OpenAI/Codex use the ChatGPT-backend Responses API. Provider is resolved per `run()` call, so workers build one agent and use any model.
- **Credentials** — `auth/credentials.py` `build_credentials()` loads all provider creds from `settings` rows (no global auth toggle). Anthropic & Kimi use API keys; OpenAI uses an imported ChatGPT-subscription OAuth token (`openai_access_token`/`openai_refresh_token`/`openai_account_id`) that the server keeps refreshed via `auth.openai.com/oauth/token`.
- **Model IDs** — Use short form: `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-8`, `kimi-for-coding`, plus OpenAI IDs (e.g. `gpt-5.5`, `codex-5.3`). No date suffixes.
- **Tool-use loop** — `agent/core.py` implements: send message → get tool_use → execute → send tool_result → repeat until text response.
- **DB settings override env** — Settings stored in PostgreSQL take priority over environment variables. The UI writes to DB.
- **Memory extraction** — Runs in background after each chat using Haiku. Extracts structural facts, not transient state.
- **Skills system** — Tools are grouped into skills (`agent/skills.py`). Built-in skills (kubernetes, github, ntfy) are always enabled. Optional skills (prometheus, loki, flux) can be toggled and configured via the Settings UI. Each skill defines its own tools and config fields.
- **4-tier PR mode** — `comment_only` → `auto_merge` (patch) → `auto_merge_minor` → `auto_merge_all` (fully autonomous). In `auto_merge_all`, PRs flagged `NEEDS_REVIEW` are escalated to the `deep_review` model (Opus) for a second opinion.
- **Two-stage alert pipeline** — Alerts go through triage (Haiku, cheap/fast) first. Triage returns `fix`, `notify`, or `ignore`. Only `fix` escalates to the Alert Fix agent (Sonnet).
- **Code fix auto-merge** — When a PR review flags `NEEDS_FIX`, the Code Fix agent pushes a commit to the PR branch, then polls CI for up to 5 minutes. If CI passes, it auto-merges.

## Safety guardrails (code-level, not prompt-level)

- `PROTECTED_BRANCHES = {"main", "master"}` — cannot commit to these
- `ALLOWED_COMMIT_PATHS = {"kubernetes/apps/"}` — can only modify manifests
- `PROTECTED_NAMESPACES = {"kube-system", "flux-system", "cert-manager"}` — no pod restarts/deletes
- Branch names must start with `fix/`, `feat/`, or `agent/`
- Max 3 PR reviews per cycle (rate limit)
- Kill switch: `agent_enabled` setting disables all workers

## Authentication

Three providers can be configured simultaneously (any subset). The model assigned to each task picks the provider.

- **Anthropic** — API key only. (The old Max/Pro OAuth flow was removed; Anthropic does not allow third-party apps to use Consumer subscription OAuth tokens.)
- **Kimi for Coding** — API key from the Kimi Code Console, used against the Anthropic-compatible endpoint `https://api.kimi.com/coding/` (model `kimi-for-coding`).
- **OpenAI / ChatGPT** — ChatGPT-subscription OAuth tokens. Because the app is a hosted server (it cannot receive the Codex `localhost:1455` redirect), tokens are **imported** via `POST /api/auth/openai` (authenticate locally first, e.g. `codex login`, then paste `access_token`/`refresh_token`/`account_id`). The server refreshes them automatically using the Codex public client (`OPENAI_CLIENT_ID` in `agent/providers.py`).

Credentials are stored as `settings` rows; disconnect a provider with `DELETE /api/auth/{provider}`.

## Database

PostgreSQL via CloudNativePG. Tables auto-created by SQLAlchemy on startup (`init_db`).

Key tables:
- `settings` — key/value store for all config (models, prompts, PR mode, skill configs, etc.)
- `conversations` — chat threads, PR reviews, alert investigations
- `messages` — individual messages within conversations (user, assistant, tool_use, tool_result)
- `memories` — extracted facts from conversations (content, category, source_conversation_id)
- `agent_tasks` — tracked background tasks (PR reviews, alert responses, code fixes)

Provider credentials (API keys, imported OpenAI tokens) live in `settings` rows — there is no dedicated tokens table.

Task types used in `agent_tasks.task_type`: `pr_review`, `pr_merge`, `pr_deep_review`, `alert_triage`, `alert_fix`, `code_fix`, `chat`. Note: the database uses a PostgreSQL enum for `task_type` — adding new types requires an ALTER TYPE migration on the enum.

Model keys (used in `models.py` defaults and DB `model_*` settings): `pr_review`, `alert_triage`, `alert_fix`, `code_fix`, `deep_review`, `chat`.

## Deployment

Deployed via Flux in the home-ops repo under `kubernetes/apps/automation/home-ops-agent/`. Uses bjw-s app-template HelmRelease with:
- ServiceAccount + ClusterRole RBAC
- SOPS-encrypted secret (GITHUB_TOKEN, DATABASE_URL, SESSION_SECRET, NTFY_TOKEN)
- HTTPRoute on envoy-internal
- Gatus health check enabled
