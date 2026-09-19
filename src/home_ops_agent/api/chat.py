"""WebSocket chat endpoint for interactive conversation with the agent."""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from home_ops_agent.agent.core import Agent, AgentResult, Thinking
from home_ops_agent.agent.costs import record_usage
from home_ops_agent.agent.memory import extract_memories
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.agent.skills import registry
from home_ops_agent.auth.credentials import build_credentials
from home_ops_agent.config import settings
from home_ops_agent.database import Conversation, Message, async_session

logger = logging.getLogger(__name__)
router = APIRouter()

# The chat is the one place someone is already reading the answer. The cluster
# context tells every agent to report what it did over ntfy -- right for an
# unattended alert at 3am, and in a chat it means a push notification carrying
# the reply that is already on screen.
#
# Withheld rather than asked for in the prompt, because a prompt is advice: the
# instruction to notify lives in cluster_context, which is editable and this
# deployment has customised, so a fix in the default text would miss it.
WITHHELD_FROM_CHAT = frozenset({"ntfy_publish"})


# How hard the chat model is asked to think. Off by default: reasoning costs
# latency and tokens on every message, and it is only worth paying for when
# someone is watching the run.
DEFAULT_THINKING_LEVEL = "off"
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


async def _thinking_level() -> str:
    """The configured reasoning level, or the default on any problem."""
    try:
        from sqlalchemy import select

        from home_ops_agent.database import Setting, async_session

        async with async_session() as session:
            result = await session.execute(select(Setting).where(Setting.key == "thinking_level"))
            setting = result.scalar_one_or_none()
            if setting and setting.value in THINKING_LEVELS:
                return setting.value
    except Exception:
        logger.warning("Could not read thinking_level; using %s", DEFAULT_THINKING_LEVEL)
    return DEFAULT_THINKING_LEVEL


# Store MCP tools reference (set during app startup)
_mcp_tools: list = []


def set_mcp_tools(tools: list):
    """Set the MCP tools available for chat."""
    global _mcp_tools
    _mcp_tools = tools


def _origin_allowed(origin: str | None) -> bool:
    """Whether a browser at ``origin`` may open this socket.

    WebSockets are not covered by the same-origin policy -- the browser will
    happily connect to a different host and hand the page the result -- so the
    server has to check `Origin` itself. Without this, any page open in a
    browser on the trusted VLAN could drive the agent, and the chat can now call
    every tool the agent has, including deleting pods and merging PRs.

    A missing `Origin` is allowed. Browsers always send it on a WebSocket
    handshake, so its absence means a non-browser client -- a script, a probe --
    and those are not what cross-site request forgery is about. Refusing them
    would break local tooling while stopping nothing.
    """
    if not origin:
        return True

    allowed = {"http://localhost:3000", "http://127.0.0.1:3000"}
    if settings.base_url:
        allowed.add(settings.base_url.rstrip("/"))
    return origin.rstrip("/") in allowed


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat with the agent."""
    origin = websocket.headers.get("origin")
    if not _origin_allowed(origin):
        logger.warning("Refused a chat socket from origin %r", origin)
        # Closed before accept, so the handshake fails rather than the page
        # getting an open socket it is then told off for using.
        await websocket.close(code=1008)
        return

    await websocket.accept()

    conversation_id = None

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            user_text = msg.get("message", "")
            conversation_id = msg.get("conversation_id")

            if not user_text:
                continue

            # Get or create conversation
            async with async_session() as session:
                if conversation_id:
                    result = await session.execute(
                        select(Conversation).where(Conversation.id == conversation_id)
                    )
                    conversation = result.scalar_one_or_none()
                else:
                    conversation = None

                if conversation is None:
                    conversation = Conversation(
                        title=user_text[:100],
                        source="chat",
                        status="active",
                    )
                    session.add(conversation)
                    await session.flush()
                    conversation_id = conversation.id

                # Save user message
                user_msg = Message(
                    conversation_id=conversation_id,
                    role="user",
                    content={"text": user_text},
                )
                session.add(user_msg)
                await session.commit()

            # Send typing indicator
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "typing",
                        "conversation_id": conversation_id,
                    }
                )
            )

            # Build message history from DB
            async with async_session() as session:
                result = await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at)
                )
                db_messages = result.scalars().all()

            messages = []
            for m in db_messages:
                if m.role == "user":
                    messages.append({"role": "user", "content": m.content.get("text", "")})
                elif m.role == "assistant":
                    messages.append({"role": "assistant", "content": m.content.get("text", "")})

            # Run agent
            credentials = await build_credentials()
            if not credentials.has_any():
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "message": (
                                "No model credentials configured."
                                " Please add an API key or connect a provider in Settings."
                            ),
                        }
                    )
                )
                continue

            agent = Agent(credentials)
            skill_tools = await registry.get_all_enabled_tools()
            agent.register_tools(
                [tool for tool in skill_tools if tool.name not in WITHHELD_FROM_CHAT]
            )
            agent.register_tools(_mcp_tools)

            try:
                chat_model = await get_model_for_task("chat")
                chat_prompt = await get_prompt("chat")

                async def on_tool_start(name: str, idx: int):
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "tool_start",
                                "conversation_id": conversation_id,
                                "tool": name,
                                "tool_index": idx,
                            }
                        )
                    )

                async def on_tool_end(name: str, idx: int):
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "tool_end",
                                "conversation_id": conversation_id,
                                "tool": name,
                                "tool_index": idx,
                            }
                        )
                    )

                result = None
                async for item in agent.run_streaming(
                    system_prompt=chat_prompt,
                    messages=messages,
                    model=chat_model,
                    max_turns=15,
                    on_tool_start=on_tool_start,
                    on_tool_end=on_tool_end,
                    thinking=await _thinking_level(),
                ):
                    if isinstance(item, str):
                        await websocket.send_text(
                            json.dumps({"type": "stream_delta", "delta": item})
                        )
                    elif isinstance(item, Thinking):
                        # Its own message type, so the UI cannot render it as
                        # the answer and it is never saved as one.
                        await websocket.send_text(
                            json.dumps({"type": "thinking_delta", "delta": item.text})
                        )
                    elif isinstance(item, AgentResult):
                        result = item

                if result is None:
                    result = AgentResult(response="[No response from agent]")

                # Save assistant response
                async with async_session() as session:
                    assistant_msg = Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content={
                            "text": result.response,
                            "tool_calls": result.tool_calls,
                            "tokens": result.total_tokens,
                        },
                    )
                    session.add(assistant_msg)
                    await session.commit()

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "stream_end",
                            "conversation_id": conversation_id,
                            "content": result.response,
                            "tool_calls": result.tool_calls,
                            "tokens": result.total_tokens,
                        }
                    )
                )

                # Record API usage
                await record_usage(
                    model=result.model,
                    task_type="chat",
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )

                # Extract memories in the background (don't block the chat)
                asyncio.create_task(
                    extract_memories(
                        conversation_id,
                        [
                            {"role": "user", "content": {"text": user_text}},
                            {
                                "role": "assistant",
                                "content": {"text": result.response},
                            },
                        ],
                    )
                )
            except Exception as e:
                logger.exception("Chat agent failed")
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "message": f"Agent error: {e}",
                        }
                    )
                )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for conversation %s", conversation_id)
    except Exception:
        logger.exception("WebSocket error")
