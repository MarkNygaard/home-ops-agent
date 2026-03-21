# Stage 1: Build Next.js static export
FROM node:22-slim AS frontend
WORKDIR /web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# Stage 2: Python builder
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
COPY src/ ./src/

# Install dependencies only (not the package itself, we'll use PYTHONPATH)
RUN uv pip install --system --no-cache-dir .

# Stage 3: Runtime
FROM base AS runtime

# Copy installed dependencies from builder
COPY --from=builder /usr/local /usr/local
# Copy application source
COPY --from=builder /app/src /app/src
# Replace static files with Next.js export output
COPY --from=frontend /web/out /app/src/home_ops_agent/static

# Add source to Python path so home_ops_agent is importable
ENV PYTHONPATH=/app/src

# Verify the module is importable
RUN python -c "import home_ops_agent; print('OK:', home_ops_agent.__file__)"

RUN useradd --create-home --uid 1000 agent
USER agent

EXPOSE 8000

ENTRYPOINT ["python", "-m", "uvicorn", "home_ops_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
