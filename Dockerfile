FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
COPY src/ ./src/

RUN uv pip install --system --no-cache-dir .

FROM base AS runtime

COPY --from=builder /usr/local /usr/local
COPY --from=builder /app/src /app/src

ENV PYTHONPATH=/app/src

RUN useradd --create-home --uid 1000 agent
USER agent

EXPOSE 8000

ENTRYPOINT ["python", "-m", "uvicorn", "home_ops_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
