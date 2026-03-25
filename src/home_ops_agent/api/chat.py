"""WebSocket chat endpoint for interactive conversation with the agent."""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from home_ops_agent.agent.core import Agent, AgentResult
from home_ops_agent.agent.memory import extract_memories
from home_ops_agent.agent.models import get_model_for_task
from home_ops_agent.agent.prompts import get_prompt
from home_ops_agent.agent.skills import registry
from home_ops_agent.auth.oauth import get_claude_credentials
from home_ops_agent.database import Conversation, Message, async_session

logger = logging.getLogger(__name__)
router = APIRouter()

# Store MCP tools reference (set during app startup)
_mcp_tools: list = []


def set_mcp_tools(tools: list):
    """Set the MCP tools available for chat."""
    global _mcp_tools
    _mcp_tools = tools


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat with the agent."""
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
            api_key, oauth_token = await get_claude_credentials()
            if not api_key and not oauth_token:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "message": (
                                "No Claude credentials configured."
                                " Please set up OAuth or an API key in Settings."
                            ),
                        }
                    )
                )
                continue

            agent = Agent(api_key=api_key, oauth_token=oauth_token)
            skill_tools = await registry.get_all_enabled_tools()
            agent.register_tools(skill_tools)
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
                ):
                    if isinstance(item, str):
                        await websocket.send_text(
                            json.dumps({"type": "stream_delta", "delta": item})
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
