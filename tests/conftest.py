"""Shared test fixtures for all test modules."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Patch kubernetes config loading before any module tries to use it
_k8s_mock = MagicMock()
sys.modules.setdefault("kubernetes", _k8s_mock)
sys.modules.setdefault("kubernetes.client", _k8s_mock)
sys.modules.setdefault("kubernetes.client.rest", _k8s_mock)
sys.modules.setdefault("kubernetes.config", _k8s_mock)

# Ensure ApiException is importable
_k8s_mock.client.rest.ApiException = type("ApiException", (Exception,), {"reason": "mocked"})
_k8s_mock.config.ConfigException = type("ConfigException", (Exception,), {})
_k8s_mock.config.load_incluster_config = MagicMock()
_k8s_mock.config.load_kube_config = MagicMock()
_k8s_mock.client.CoreV1Api = MagicMock
_k8s_mock.client.AppsV1Api = MagicMock
_k8s_mock.client.CustomObjectsApi = MagicMock

from home_ops_agent.database import Base  # noqa: E402

# All modules that do `from home_ops_agent.database import async_session`
_ASYNC_SESSION_PATCHES = [
    "home_ops_agent.database.async_session",
    "home_ops_agent.agent.models.async_session",
    "home_ops_agent.agent.prompts.async_session",
    "home_ops_agent.agent.skills.async_session",
    "home_ops_agent.agent.memory.async_session",
    "home_ops_agent.workers.pr_monitor.async_session",
    "home_ops_agent.workers.pr_merge.async_session",
    "home_ops_agent.workers.pr_fix.async_session",
    "home_ops_agent.workers.alert_subscriber.async_session",
    "home_ops_agent.api.status.async_session",
    "home_ops_agent.api.settings.async_session",
    "home_ops_agent.auth.oauth.async_session",
]


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def db_engine():
    """Create an in-memory SQLite async engine for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """Provide a test database session and patch async_session everywhere."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():

            def make_session():
                return _SessionContext(session)

            # Patch async_session in all modules that import it
            patches = [patch(target, make_session) for target in _ASYNC_SESSION_PATCHES]
            for p in patches:
                p.start()
            try:
                yield session
            finally:
                for p in patches:
                    p.stop()
            await session.rollback()


class _SessionContext:
    """Context manager that returns the existing session instead of creating a new one."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        # Don't close — the fixture manages lifecycle
        pass


def make_anthropic_response(text="Hello", tool_use_blocks=None):
    """Create a mock Anthropic API response object."""
    content = []
    if tool_use_blocks:
        for tb in tool_use_blocks:
            content.append(
                SimpleNamespace(
                    type="tool_use",
                    id=tb.get("id", "tool_123"),
                    name=tb["name"],
                    input=tb.get("input", {}),
                )
            )
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))

    return SimpleNamespace(
        content=content,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        stop_reason="end_turn",
    )


@pytest.fixture
def mock_anthropic_client():
    """Provide a mock AsyncAnthropic client with configurable responses."""
    client = AsyncMock()
    client.messages = AsyncMock()
    client.messages.create = AsyncMock(return_value=make_anthropic_response())
    return client


@pytest.fixture(autouse=True)
def clean_sessions():
    """Clear the in-memory session store between tests."""
    from home_ops_agent.auth.session import _sessions

    _sessions.clear()
    yield
    _sessions.clear()


@pytest.fixture
def mock_settings():
    """Provide a mock settings object with safe defaults for testing.

    Patches settings in the config module AND in all tool modules that import it directly.
    """
    targets = [
        "home_ops_agent.config.settings",
        "home_ops_agent.agent.tools.github.settings",
        "home_ops_agent.agent.tools.ntfy.settings",
    ]
    patches = []
    mock = None
    for target in targets:
        p = patch(target)
        m = p.start()
        if mock is None:
            mock = m
        else:
            # Copy attributes from the first mock
            for attr in dir(mock):
                if not attr.startswith("_"):
                    try:
                        setattr(m, attr, getattr(mock, attr))
                    except (AttributeError, TypeError):
                        pass
        patches.append(p)

    # Set all attributes on all mocks
    attrs = {
        "anthropic_api_key": "test-api-key",
        "anthropic_client_id": "",
        "anthropic_client_secret": "",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "github_token": "test-gh-token",
        "github_repo": "test-owner/test-repo",
        "cluster_domain": "test.local",
        "ntfy_url": "http://ntfy.test",
        "ntfy_alertmanager_topic": "alertmanager",
        "ntfy_gatus_topic": "gatus",
        "ntfy_agent_topic": "home-ops-agent",
        "ntfy_token": "test-ntfy-token",
        "session_secret": "test-secret",
        "base_url": "https://test.local",
        "pr_check_interval_seconds": 1800,
        "alert_cooldown_seconds": 900,
        "model_pr_review": "claude-haiku-4-5",
        "model_alert_triage": "claude-haiku-4-5",
        "model_alert_fix": "claude-sonnet-4-6",
        "model_code_fix": "claude-sonnet-4-6",
        "model_deep_review": "claude-opus-4-6",
        "model_chat": "claude-sonnet-4-6",
    }
    for target in targets:
        import importlib

        parts = target.rsplit(".", 1)
        mod = importlib.import_module(parts[0])
        obj = getattr(mod, parts[1])
        for k, v in attrs.items():
            setattr(obj, k, v)

    yield mock

    for p in patches:
        p.stop()
