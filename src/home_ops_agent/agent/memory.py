"""Persistent memory — extract and recall facts across conversations."""

import json
import logging

import anthropic
from sqlalchemy import select

from home_ops_agent.auth.oauth import get_claude_credentials
from home_ops_agent.database import Memory, async_session

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are a memory extraction system. Given a conversation between a user and an agent \
about a Kubernetes home lab cluster, extract key facts worth remembering for future \
conversations.

Extract ONLY facts that would be useful in future conversations, such as:
- Recurring issues (e.g., "Sonarr pod frequently OOMs")
- User preferences (e.g., "User prefers to be notified about all restarts")
- Cluster knowledge (e.g., "Jellyfin uses GPU transcoding on k8s-1")
- Fixes applied (e.g., "Fixed radarr by increasing memory limit to 512Mi")
- Configuration details (e.g., "AdGuard upstream DNS changed to 1.1.1.1")

Do NOT extract:
- Transient status checks ("pods are running" — this changes constantly)
- Greetings or small talk
- Information already in the system prompt

Return a JSON array of objects with "content" and "category" fields.
Categories: "issue", "preference", "knowledge", "fix", "config"

If there is nothing worth remembering, return an empty array: []

Example output:
[
  {"content": "Sonarr pod OOMs when scanning large libraries", "category": "fix"},
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

    api_key, oauth_token = await get_claude_credentials()
    if not api_key and not oauth_token:
        return []

    if oauth_token:
        client = anthropic.Anthropic(auth_token=oauth_token)
    else:
        client = anthropic.Anthropic(api_key=api_key)

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
        response = client.messages.create(
            model="claude-haiku-4-5",
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


async def load_memories(limit: int = 20) -> str:
    """Load recent memories and format them for the system prompt."""
    async with async_session() as session:
        result = await session.execute(
            select(Memory).order_by(Memory.created_at.desc()).limit(limit)
        )
        memories = result.scalars().all()

    if not memories:
        return ""

    lines = ["## Agent Memory", "Things I remember from previous conversations:", ""]
    for mem in reversed(memories):  # Show oldest first
        lines.append(f"- [{mem.category}] {mem.content}")

    return "\n".join(lines)
