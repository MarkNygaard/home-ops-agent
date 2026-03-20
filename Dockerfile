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

FROM base AS runtime

# Copy installed dependencies from builder
COPY --from=builder /usr/local /usr/local
# Copy application source
COPY --from=builder /app/src /app/src

# Add source to Python path so home_ops_agent is importable
ENV PYTHONPATH=/app/src

# Verify the module is importable
RUN python -c "import home_ops_agent; print('OK:', home_ops_agent.__file__)"

RUN useradd --create-home --uid 1000 agent
USER agent

EXPOSE 8000

ENTRYPOINT ["python", "-m", "uvicorn", "home_ops_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
