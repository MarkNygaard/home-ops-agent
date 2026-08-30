"""Tests for hand-written memories and the staleness policy.

Memories are injected into every system prompt. A point-in-time incident stored
as a fact reads as present tense forever and can contradict what the agent's
live tools report, so the rules that keep those out are worth pinning down.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from home_ops_agent.agent import memory as mem_mod
from home_ops_agent.api.status import router
from home_ops_agent.database import Memory


@pytest.fixture
async def client(db_session):
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _aged(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


# --- staleness policy ---


def test_incidents_go_stale_but_knowledge_does_not():
    now = datetime.now(UTC)
    old_issue = Memory(content="x", category="issue", created_at=_aged(30))
    fresh_issue = Memory(content="x", category="issue", created_at=_aged(2))
    old_knowledge = Memory(content="x", category="knowledge", created_at=_aged(300))

    assert mem_mod._is_stale(old_issue, now) is True
    assert mem_mod._is_stale(fresh_issue, now) is False
    # Architectural facts never expire — that is the whole point of the category.
    assert mem_mod._is_stale(old_knowledge, now) is False


def test_naive_timestamps_are_treated_as_utc():
    """SQLite hands back naive datetimes; comparing them must not explode."""
    now = datetime.now(UTC)
    naive = Memory(content="x", category="issue", created_at=datetime.now() - timedelta(days=30))
    assert mem_mod._is_stale(naive, now) is True


def test_missing_timestamp_is_kept():
    now = datetime.now(UTC)
    assert mem_mod._is_stale(Memory(content="x", category="issue", created_at=None), now) is False


def test_age_rendering():
    now = datetime.now(UTC)
    assert mem_mod._age(now, now) == "today"
    assert mem_mod._age(now - timedelta(days=1), now) == "1 day ago"
    assert mem_mod._age(now - timedelta(days=9), now) == "9 days ago"
    assert mem_mod._age(now - timedelta(days=150), now) == "5 months ago"
    assert mem_mod._age(None, now) == "age unknown"


# --- loading ---


async def test_stale_incidents_are_not_injected(db_session):
    db_session.add(
        Memory(
            content="k8s-1 is currently cordoned",
            category="issue",
            created_at=_aged(30),
        )
    )
    db_session.add(
        Memory(
            content="Apps with local-path PVCs are pinned to their node",
            category="knowledge",
            created_at=_aged(160),
        )
    )
    await db_session.flush()

    result = await mem_mod.load_memories()

    assert "currently cordoned" not in result
    assert "local-path PVCs are pinned" in result


async def test_recent_incidents_are_kept(db_session):
    db_session.add(
        Memory(content="zigbee2mqtt loses its adapter", category="issue", created_at=_aged(1))
    )
    await db_session.flush()

    assert "zigbee2mqtt" in await mem_mod.load_memories()


async def test_every_line_carries_its_age(db_session):
    db_session.add(Memory(content="Sonarr needs 512Mi", category="knowledge", created_at=_aged(3)))
    await db_session.flush()

    result = await mem_mod.load_memories()

    assert "- [knowledge] Sonarr needs 512Mi (3 days ago)" in result
    # And the model is told what to do when memory and tools disagree.
    assert "prefer what your tools report now" in result


async def test_stale_entries_do_not_consume_the_limit(db_session):
    """Filtering must happen before the limit, or noise evicts real knowledge.

    Memories are loaded newest-first. A burst of recent-but-stale incidents
    would otherwise fill the window and push durable facts out entirely.
    """
    for i in range(6):
        db_session.add(Memory(content=f"incident {i}", category="issue", created_at=_aged(20 + i)))
    db_session.add(
        Memory(content="durable architectural fact", category="knowledge", created_at=_aged(200))
    )
    await db_session.flush()

    result = await mem_mod.load_memories(limit=3)

    assert "durable architectural fact" in result
    assert "incident" not in result


async def test_no_memories_yields_empty_string(db_session):
    assert await mem_mod.load_memories() == ""


async def test_only_stale_memories_yields_empty_string(db_session):
    db_session.add(Memory(content="old incident", category="issue", created_at=_aged(90)))
    await db_session.flush()

    assert await mem_mod.load_memories() == ""


# --- writing by hand ---


async def test_create_memory(client, db_session):
    resp = await client.post(
        "/api/memories",
        json={
            "content": "ntfy runs behind a local-path PVC pinned to k8s-1",
            "category": "knowledge",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] == "knowledge"
    assert body["source_conversation_id"] is None
    assert body["id"]

    listed = (await client.get("/api/memories")).json()
    assert any("local-path PVC pinned to k8s-1" in m["content"] for m in listed)


async def test_create_memory_defaults_to_knowledge(client, db_session):
    resp = await client.post("/api/memories", json={"content": "a durable fact"})
    assert resp.json()["category"] == "knowledge"


async def test_create_memory_rejects_empty_content(client, db_session):
    resp = await client.post("/api/memories", json={"content": "   "})
    assert "must not be empty" in resp.json()["error"]


async def test_create_memory_rejects_unknown_category(client, db_session):
    resp = await client.post("/api/memories", json={"content": "x", "category": "nonsense"})
    assert "category must be one of" in resp.json()["error"]


async def test_create_memory_rejects_duplicates(client, db_session):
    payload = {"content": "the same fact twice", "category": "knowledge"}
    assert (await client.post("/api/memories", json=payload)).json()["id"]

    second = await client.post("/api/memories", json=payload)
    assert "already exists" in second.json()["error"]


async def test_created_memory_reaches_the_prompt(client, db_session):
    """The point of the endpoint: a hand-written fact must actually be used."""
    await client.post(
        "/api/memories",
        json={"content": "k8s-1 cannot be drained normally", "category": "knowledge"},
    )

    assert "cannot be drained normally" in await mem_mod.load_memories()


# --- extraction guardrails ---


def test_extraction_prompt_forbids_incident_snapshots():
    prompt = mem_mod.EXTRACTION_PROMPT
    for word in ("currently", "recently", "right now"):
        assert word in prompt, f"the prompt should name '{word}' as a rejected hedge"
    assert "RECURRING" in prompt
    assert "injected into every future system prompt" in prompt


def test_known_categories_cover_what_the_prompt_offers():
    for category in ("issue", "preference", "knowledge", "fix", "config"):
        assert category in mem_mod.MEMORY_CATEGORIES
    assert "general" in mem_mod.MEMORY_CATEGORIES
