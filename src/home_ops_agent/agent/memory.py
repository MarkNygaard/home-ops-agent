"""Persistent memory — extract and recall facts across conversations."""

import json
import logging
from datetime import UTC, datetime, timedelta

import anthropic
from sqlalchemy import select

from home_ops_agent.agent import providers
from home_ops_agent.auth.credentials import build_credentials
from home_ops_agent.database import Memory, async_session

logger = logging.getLogger(__name__)

MEMORY_CATEGORIES = {"issue", "preference", "knowledge", "fix", "config", "general"}

# Categories whose entries describe a *live* condition rather than a property of
# the cluster. They are useful while the condition holds and actively misleading
# once it does not: rendered without a date into every system prompt, a month-old
# "k8s-1 is currently cordoned" reads as present tense and can contradict what
# the live tools report. An issue that is still true gets re-observed and
# re-extracted; one that is not should fall out on its own.
PERISHABLE_CATEGORIES = {"issue"}
PERISHABLE_MAX_AGE = timedelta(days=7)

EXTRACTION_PROMPT = """\
You are a memory extraction system. Given a conversation between a user and an agent \
about a Kubernetes home lab cluster, extract key facts worth remembering for future \
conversations.

Extract ONLY structural, long-lived facts, such as:
- Recurring issues (e.g., "Sonarr pod frequently OOMs under heavy load")
- User preferences (e.g., "User prefers to be notified about all restarts")
- Architectural knowledge (e.g., "Apps with local-path PVCs are pinned to the node \
where the PVC was created — if scheduled elsewhere, mount fails")
- Fixes applied (e.g., "Fixed radarr by increasing memory limit to 512Mi")
- Configuration details (e.g., "AdGuard upstream DNS changed to 1.1.1.1")
- Resource constraints (e.g., "Nodes have 16GB RAM, GPU only on k8s-1")

Do NOT extract:
- Current pod/node placement ("Jellyfin is on k8s-1") — pods move between nodes
- Transient status ("pods are running", "no open PRs") — this changes constantly
- Pod counts, IP addresses, or resource usage numbers — stale within minutes
- Greetings, small talk, or simple questions
- Information already in the cluster context prompt

Focus on WHY things happen, not WHERE things currently are.

### The incident trap — read this before using "issue"

Everything you write is injected into every future system prompt WITHOUT a \
timestamp, so it is read as present tense forever. A snapshot of what is broken \
right now becomes a lie tomorrow, and can contradict what the agent's live tools \
report.

"issue" means a RECURRING pattern ("zigbee2mqtt loses its Ember adapter after \
every restart"), never a current incident ("zigbee2mqtt is not Ready").

Reject any statement that:
- uses "currently", "recently", "right now", "at the moment", or "still"
- is written in the present or perfect tense about one occurrence \
("X is down", "X failed", "X is stuck in rollback", "X is cordoned")
- would need re-checking to know whether it is still true

When a conversation is about an incident, do not record the incident. Record \
what it TAUGHT you about how the cluster is built, and say it as a permanent \
property. Examples of that conversion:
- "ntfy pod is pending, publishing failed" -> "ntfy runs behind a local-path PVC \
pinned to k8s-1, so it cannot start while that node is unschedulable — and it is \
the agent's own alert channel, so alerts are lost exactly when the cluster is \
broken" (knowledge)
- "k8s-1 is cordoned with the tuppr outdated taint" -> nothing; this is live state
- "the drain hung, postgres PDB allows 0 disruptions" -> "k8s-1 cannot be drained \
normally: the CNPG postgres-primary PDB is minAvailable:1 with one replica" (knowledge)

If an incident taught you nothing structural, return [].

Return a JSON array of objects with "content" and "category" fields.
Categories: "issue", "preference", "knowledge", "fix", "config"

If there is nothing worth remembering, return an empty array: []

Example output:
[
  {"content": "Sonarr PVC uses local-path on k8s-0, pod must run there", "category": "knowledge"},
  {"content": "User wants ntfy notifications for all restarts", "category": "preference"}
]
"""


async def extract_memories(
    conversation_id: int,
    messages: list[dict],
) -> list[dict]:
    """Extract memorable facts from a conversation using a cheap model."""
    if len(messages) < 2:
        return []

    # Memory extraction uses a cheap Anthropic-protocol model. Prefer Anthropic;
    # fall back to Kimi's Anthropic-compatible endpoint. (OpenAI uses a different
    # API and is skipped — extraction is best-effort.)
    credentials = await build_credentials()
    if credentials.has_provider(providers.ANTHROPIC):
        client = anthropic.AsyncAnthropic(api_key=credentials.anthropic_api_key)
        model = "claude-haiku-4-5"
    elif credentials.has_provider(providers.KIMI):
        client = anthropic.AsyncAnthropic(
            api_key=credentials.kimi_api_key, base_url=providers.KIMI_BASE_URL
        )
        model = "kimi-for-coding"
    else:
        return []

    # Build a summary of the conversation for extraction
    conv_text = ""
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", {})
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        if text:
            conv_text += f"{role}: {text}\n\n"

    if len(conv_text) < 50:
        return []

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": conv_text[:8000]}],
        )

        result_text = response.content[0].text.strip()

        # Parse JSON, handling markdown code blocks
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0]

        memories = json.loads(result_text)
        if not isinstance(memories, list):
            return []

        # Save to database
        saved = []
        async with async_session() as session:
            for mem in memories:
                content = mem.get("content", "").strip()
                category = mem.get("category", "general")
                if not content:
                    continue
                if category not in MEMORY_CATEGORIES:
                    category = "general"

                # Check for duplicates (simple substring match)
                result = await session.execute(select(Memory).where(Memory.content == content))
                if result.scalar_one_or_none():
                    continue

                memory = Memory(
                    content=content,
                    category=category,
                    source_conversation_id=conversation_id,
                )
                session.add(memory)
                saved.append({"content": content, "category": category})

            await session.commit()

        if saved:
            logger.info("Extracted %d memories from conversation %d", len(saved), conversation_id)
        return saved

    except Exception:
        logger.exception("Failed to extract memories")
        return []


def _is_stale(memory: Memory, now: datetime) -> bool:
    """Has a perishable memory outlived its usefulness?"""
    if memory.category not in PERISHABLE_CATEGORIES:
        return False
    created = memory.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return now - created > PERISHABLE_MAX_AGE


async def load_memories(limit: int = 20) -> str:
    """Load recent memories and format them for the system prompt.

    Two things matter here beyond fetching rows:

    - **Stale incidents are dropped.** See ``PERISHABLE_CATEGORIES``. They are
      filtered *before* the limit is applied, so a burst of incident snapshots
      cannot evict durable knowledge out of the window.
    - **Every line carries its age.** Without it a five-month-old fact and a
      one-day-old one look identical to the model, and hedged language like
      "currently" reads as now.
    """
    now = datetime.now(UTC)
    async with async_session() as session:
        # Over-fetch: some rows are dropped below, and the limit should count
        # what actually reaches the prompt.
        result = await session.execute(
            select(Memory).order_by(Memory.created_at.desc()).limit(limit * 4)
        )
        rows = result.scalars().all()

    memories = [m for m in rows if not _is_stale(m, now)][:limit]
    if not memories:
        return ""

    lines = [
        "## Agent Memory",
        "Things I remember from previous conversations. The age of each is shown —"
        " prefer what your tools report now over anything old that contradicts them.",
        "",
    ]
    for mem in reversed(memories):  # Show oldest first
        lines.append(f"- [{mem.category}] {mem.content} ({_age(mem.created_at, now)})")

    return "\n".join(lines)


def _age(created: datetime | None, now: datetime) -> str:
    """Human-readable age for a memory line."""
    if created is None:
        return "age unknown"
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    days = (now - created).days
    if days < 1:
        return "today"
    if days == 1:
        return "1 day ago"
    if days < 60:
        return f"{days} days ago"
    return f"{days // 30} months ago"
