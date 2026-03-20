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
│   ├── core.py             # Agent class — AsyncAnthropic client, tool-use loop
│   ├── prompts.py          # System prompts with DB overrides, memory loading
│   ├── models.py           # Per-task model resolution (DB → env fallback)
│   └── memory.py           # Memory extraction (Haiku) and loading
│   └── tools/
│       ├── kubernetes.py   # K8s API tools (pods, logs, events, restart, delete)
│       ├── github.py       # GitHub API tools (PRs, commits, branches, files)
│       └── ntfy.py         # ntfy publish with auth
├── workers/
│   ├── pr_monitor.py       # Periodic PR review (checks enabled, rate limited, deduped)
│   └── alert_subscriber.py # ntfy SSE stream for alertmanager/gatus topics
├── auth/
│   ├── oauth.py            # Anthropic OAuth flow (not currently usable, see below)
│   └── session.py          # Simple in-memory session store
├── mcp/
│   ├── client.py           # MCP stdio client for sidecar servers
│   └── bridge.py           # Converts MCP tools to Claude tool definitions
├── api/
│   ├── chat.py             # WebSocket chat endpoint with memory extraction
│   ├── status.py           # REST: health, history, conversations, memories
│   └── settings.py         # REST: settings CRUD, prompts CRUD, OAuth flow
└── static/
    ├── index.html          # Single-page app (chat, history, memories, settings)
    ├── style.css           # Dark theme, agent cards, modal, kill switch
    └── app.js              # WebSocket client, settings, history, memories, prompts
```

## Key commands

```bash
# Lint
uvx ruff check src/
uvx ruff format --check src/

# Run locally (needs DATABASE_URL and ANTHROPIC_API_KEY env vars)
uvicorn home_ops_agent.main:app --host 0.0.0.0 --port 8000

# Build Docker image
docker build -t home-ops-agent .

# Release (triggers CI build + push to GHCR)
git tag v0.x.y && git push origin v0.x.y
```

## Important patterns

- **AsyncAnthropic** — Must use `anthropic.AsyncAnthropic` (not `Anthropic`) since the app is fully async (FastAPI + asyncio workers). Synchronous client blocks the event loop.
- **Model IDs** — Use short form: `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-6`. No date suffixes.
- **Tool-use loop** — `agent/core.py` implements: send message → get tool_use → execute → send tool_result → repeat until text response.
- **DB settings override env** — Settings stored in PostgreSQL take priority over environment variables. The UI writes to DB.
- **Memory extraction** — Runs in background after each chat using Haiku. Extracts structural facts, not transient state.

## Safety guardrails (code-level, not prompt-level)

- `PROTECTED_BRANCHES = {"main", "master"}` — cannot commit to these
- `ALLOWED_COMMIT_PATHS = {"kubernetes/apps/"}` — can only modify manifests
- `PROTECTED_NAMESPACES = {"kube-system", "flux-system", "cert-manager"}` — no pod restarts/deletes
- Branch names must start with `fix/`, `feat/`, or `agent/`
- Max 3 PR reviews per cycle (rate limit)
- Kill switch: `agent_enabled` setting disables all workers

## OAuth status

Anthropic does not allow third-party apps to use OAuth tokens from Max/Pro subscriptions (Consumer ToS, Feb 2026). The OAuth code exists but is non-functional. Use API keys only.

## Database

PostgreSQL via CloudNativePG. Tables auto-created by SQLAlchemy on startup (`init_db`).

Key tables:
- `settings` — key/value store for all config (models, prompts, PR mode, etc.)
- `conversations` — chat threads, PR reviews, alert investigations
- `messages` — individual messages within conversations (user, assistant, tool_use, tool_result)
- `memories` — extracted facts from conversations (content, category, source_conversation_id)
- `agent_tasks` — tracked background tasks (PR reviews, alert responses)
- `oauth_tokens` — stored OAuth tokens (unused currently)

## Deployment

Deployed via Flux in the home-ops repo under `kubernetes/apps/automation/home-ops-agent/`. Uses bjw-s app-template HelmRelease with:
- ServiceAccount + ClusterRole RBAC
- SOPS-encrypted secret (GITHUB_TOKEN, DATABASE_URL, SESSION_SECRET, NTFY_TOKEN)
- HTTPRoute on envoy-internal
- Gatus health check enabled
