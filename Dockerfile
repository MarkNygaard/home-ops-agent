# Stage 1: Build Next.js static export
FROM node:22-slim AS frontend
WORKDIR /web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# Stage 2: Python builder
# pi, the agent harness for every model that is not claude-code. Installed in a
# node stage and copied in, because the runtime is python:slim and pi is a Node
# program -- this keeps npm and its cache out of the final image.
#
# Pinned rather than latest. pi's model registry decides which models are
# reachable at all: on 0.75.3 every ChatGPT model returned
# "not supported when using Codex with a ChatGPT account", and 0.85.1 is what
# made gpt-6-astra work. So the pin is load-bearing in both directions -- too
# old and models disappear, unpinned and they change without a commit.
FROM node:22-slim AS pi
RUN npm install -g @earendil-works/pi-coding-agent@0.85.1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# uv.lock is copied on purpose: without it `uv pip install .` re-resolves every
# dependency at build time, so the image can ship versions the test suite never
# ran against. That is exactly how an unpinned `mcp>=1.0.0` put 2.x in the image
# while tests passed on 1.26, and crashed the pod on startup.
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install the locked dependency set (not the package itself, we use PYTHONPATH).
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Stage 3: Runtime
FROM base AS runtime

# Copy installed dependencies from builder
COPY --from=builder /usr/local /usr/local

# Tooling for checkout-based code fixes on the `claude-code/*` backend: git
# clones home-ops into a per-run worktree the agent edits directly, and
# kubeconform is the validation step it runs before committing. Installed after
# the /usr/local copy above so the layer is not clobbered.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
    && curl -fsSL -o /tmp/kubeconform.tar.gz \
        https://github.com/yannh/kubeconform/releases/download/v0.7.0/kubeconform-linux-amd64.tar.gz \
    && tar -xzf /tmp/kubeconform.tar.gz -C /usr/local/bin kubeconform \
    && rm /tmp/kubeconform.tar.gz \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# node plus pi. Only the interpreter and the installed package are copied; npm
# itself is not needed at runtime.
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /usr/local/lib/node_modules/@earendil-works /usr/local/lib/node_modules/@earendil-works
RUN printf '#!/bin/sh
exec /usr/local/bin/node /usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/cli.js "$@"
'         > /usr/local/bin/pi     && chmod +x /usr/local/bin/pi

# The cluster tools pi exposes to the model. Extensions are loaded explicitly
# with -e rather than by discovery, so nothing on the filesystem can add a tool
# the agent did not ship.
COPY extensions/ /app/extensions/

# Copy application source
COPY --from=builder /app/src /app/src
# Replace static files with Next.js export output
COPY --from=frontend /web/out /app/src/home_ops_agent/static

# Add source to Python path so home_ops_agent is importable
ENV PYTHONPATH=/app/src

# Verify the module is importable
RUN python -c "import home_ops_agent; print('OK:', home_ops_agent.__file__)"

# Verify the Claude Code CLI that claude-agent-sdk bundles landed in the image
# and is executable. A wheel without a binary for this platform would otherwise
# only surface as a failed `claude-code/*` run in the cluster.
RUN python -c "import os, pathlib, claude_agent_sdk; p = pathlib.Path(claude_agent_sdk.__file__).parent / '_bundled' / 'claude'; assert p.exists(), f'bundled Claude Code CLI missing at {p}'; assert os.access(p, os.X_OK), f'bundled Claude Code CLI not executable at {p}'; print('OK: claude code cli', p)"

RUN useradd --create-home --uid 1000 agent
USER agent

# `claude-code/*` models spawn the Claude Code CLI, which the `claude-agent-sdk`
# wheel bundles (and prefers over any copy on PATH), so nothing extra is
# installed for it. It does write session state under $HOME.
#
# AGENT_WORKSPACE_DIR is where the home-ops clone and its per-run worktrees
# live. It defaults to a path under $HOME so it works with no volume at all
# (the clone is re-created when missing); mount a volume over it to persist the
# clone across restarts.
ENV HOME=/home/agent \
    AGENT_WORKSPACE_DIR=/home/agent/workspace

EXPOSE 8000

ENTRYPOINT ["python", "-m", "uvicorn", "home_ops_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
