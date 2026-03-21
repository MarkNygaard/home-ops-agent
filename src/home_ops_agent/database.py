"""Database models and session management."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from home_ops_agent.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    """Persistent agent settings (PR mode, auth method, etc.)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OAuthToken(Base):
    """Stored OAuth tokens for Anthropic API."""

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    """A conversation thread (chat, PR review, or alert investigation)."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(
        Enum(
            "chat",
            "pr_review",
            "pr_deep_review",
            "alert",
            "alert_triage",
            "alert_fix",
            "code_fix",
            name="conversation_source",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "completed", "failed", name="conversation_status"),
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    """A single message in a conversation."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("user", "assistant", "tool_use", "tool_result", name="message_role"), nullable=False
    )
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class AgentTask(Base):
    """A tracked agent task (PR check, alert response, etc.)."""

    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_type: Mapped[str] = mapped_column(
        Enum(
            "pr_review",
            "pr_merge",
            "alert_response",
            "alert_triage",
            "alert_fix",
            "user_chat",
            "cluster_fix",
            "code_fix",
            name="task_type",
        ),
        nullable=False,
    )
    trigger: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("pending", "running", "completed", "failed", name="task_status"),
        default="pending",
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    actions_taken: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Memory(Base):
    """A persistent memory extracted from agent conversations."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="general",
    )
    source_conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def init_db():
    """Create all tables and migrate enums."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Add missing enum values that were added after initial table creation.
    # PostgreSQL ADD VALUE is idempotent with IF NOT EXISTS.
    async with engine.begin() as conn:
        for value in ("pr_merge", "alert_triage", "alert_fix", "code_fix"):
            await conn.execute(text(f"ALTER TYPE task_type ADD VALUE IF NOT EXISTS '{value}'"))
        for value in ("pr_deep_review", "alert_triage", "alert_fix", "code_fix"):
            await conn.execute(
                text(f"ALTER TYPE conversation_source ADD VALUE IF NOT EXISTS '{value}'")
            )
