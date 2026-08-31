"""Tests for api/analytics.py.

This replaced a cost page. Every provider is plan-billed now, so cost is $0
everywhere and a view organised around it showed nothing — its proportion bars
were a share of a total that is zero. Token volume and run outcomes carry the
signal instead, with cost gated behind `is_billed` so it reappears on its own if
a metered provider is ever added back.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from home_ops_agent.api.analytics import router
from home_ops_agent.database import AgentTask, ApiUsage


@pytest.fixture
async def client(db_session):
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _aged(days=0, hours=1):
    return datetime.now(UTC) - timedelta(days=days, hours=hours)


async def _usage(db_session, model, task_type, inp, out, cost=0.0, days=0):
    db_session.add(
        ApiUsage(
            model=model,
            task_type=task_type,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=cost,
            created_at=_aged(days=days),
        )
    )
    await db_session.flush()


# --- the conditional that motivated the change ---


async def test_is_billed_is_false_when_nothing_costs_money(client, db_session):
    await _usage(db_session, "claude-code/sonnet", "pr_review", 50_000, 2_000)

    body = (await client.get("/api/analytics")).json()

    assert body["is_billed"] is False
    assert body["total_cost_usd"] == 0.0
    # The usage itself is still reported — that is the point.
    assert body["total_tokens"] == 52_000
    assert body["total_requests"] == 1


async def test_is_billed_flips_when_a_metered_model_appears(client, db_session):
    """No code change should be needed to bring cost back into view."""
    await _usage(db_session, "claude-code/sonnet", "pr_review", 10, 10)
    await _usage(db_session, "some-metered-model", "chat", 10, 10, cost=0.42)

    body = (await client.get("/api/analytics")).json()

    assert body["is_billed"] is True
    assert body["total_cost_usd"] == 0.42


# --- token aggregation ---


async def test_tokens_aggregate_by_model_and_sort_by_volume(client, db_session):
    await _usage(db_session, "claude-code/haiku", "pr_review", 1_000, 100)
    await _usage(db_session, "claude-code/haiku", "pr_review", 2_000, 200)
    await _usage(db_session, "claude-code/opus", "deep_review", 500, 50)

    body = (await client.get("/api/analytics")).json()

    assert [m["model"] for m in body["by_model"]] == [
        "claude-code/haiku",
        "claude-code/opus",
    ]
    haiku = body["by_model"][0]
    assert haiku["input_tokens"] == 3_000
    assert haiku["output_tokens"] == 300
    assert haiku["total_tokens"] == 3_300
    assert haiku["requests"] == 2


async def test_by_task_carries_tokens_not_just_cost(client, db_session):
    """The old endpoint only reported cost per task, which is now always zero."""
    await _usage(db_session, "claude-code/haiku", "alert_triage", 18_000, 400)

    body = (await client.get("/api/analytics")).json()

    task = body["by_task"][0]
    assert task["task_type"] == "alert_triage"
    assert task["total_tokens"] == 18_400


# --- run outcomes: the only place a failure is visible ---


async def test_runs_report_completed_and_failed(client, db_session):
    for status in ("completed", "completed", "failed"):
        db_session.add(
            AgentTask(task_type="pr_review", trigger="t", status=status, created_at=_aged())
        )
    await db_session.flush()

    body = (await client.get("/api/analytics")).json()

    run = next(r for r in body["runs"] if r["task_type"] == "pr_review")
    assert run["completed"] == 2
    assert run["failed"] == 1
    assert run["total"] == 3
    assert body["total_runs"] == 3
    assert body["total_failed"] == 1


async def test_a_type_with_runs_but_no_successes_is_visible(client, db_session):
    """The shape that revealed 75 alert triages and zero alert fixes."""
    for _ in range(5):
        db_session.add(
            AgentTask(task_type="alert_triage", trigger="t", status="completed", created_at=_aged())
        )
    await db_session.flush()

    body = (await client.get("/api/analytics")).json()

    assert [r["task_type"] for r in body["runs"]] == ["alert_triage"]
    assert not any(r["task_type"] == "alert_fix" for r in body["runs"])


async def test_runs_are_sorted_by_volume(client, db_session):
    for _ in range(3):
        db_session.add(
            AgentTask(task_type="pr_review", trigger="t", status="completed", created_at=_aged())
        )
    db_session.add(
        AgentTask(task_type="code_fix", trigger="t", status="completed", created_at=_aged())
    )
    await db_session.flush()

    body = (await client.get("/api/analytics")).json()
    assert [r["task_type"] for r in body["runs"]] == ["pr_review", "code_fix"]


# --- windowing and empty states ---


async def test_the_window_excludes_older_rows(client, db_session):
    await _usage(db_session, "claude-code/sonnet", "chat", 100, 10, days=90)

    body = (await client.get("/api/analytics?days=7")).json()

    assert body["total_requests"] == 0
    assert body["total_tokens"] == 0


async def test_no_activity_reports_zeroes_rather_than_failing(client, db_session):
    body = (await client.get("/api/analytics")).json()

    assert body["total_requests"] == 0
    assert body["total_runs"] == 0
    assert body["is_billed"] is False
    assert body["by_model"] == []
    assert body["runs"] == []


async def test_days_is_validated(client, db_session):
    assert (await client.get("/api/analytics?days=0")).status_code == 422
    assert (await client.get("/api/analytics?days=400")).status_code == 422
