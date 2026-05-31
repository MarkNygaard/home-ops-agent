"""Multi-provider credential resolution.

Replaces the old single-provider ``auth_method`` toggle. All three providers
can hold credentials simultaneously; the model picked for a task determines
which provider (and therefore which credential) is used.

Credentials are stored as ``settings`` rows (same store as API keys), so no
schema migration is required:

- ``anthropic_api_key``
- ``kimi_api_key``
- ``openai_access_token`` / ``openai_refresh_token`` / ``openai_account_id``
  / ``openai_expires_at`` (ISO-8601)
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from home_ops_agent.agent import providers
from home_ops_agent.config import settings
from home_ops_agent.database import Setting, async_session

logger = logging.getLogger(__name__)

# OpenAI credential setting keys (also the names refreshed tokens are stored under).
OPENAI_ACCESS_TOKEN_KEY = "openai_access_token"
OPENAI_REFRESH_TOKEN_KEY = "openai_refresh_token"
OPENAI_ACCOUNT_ID_KEY = "openai_account_id"
OPENAI_EXPIRES_AT_KEY = "openai_expires_at"


@dataclass
class Credentials:
    """All configured provider credentials. Any subset may be present."""

    anthropic_api_key: str | None = None
    kimi_api_key: str | None = None
    openai_access_token: str | None = None
    openai_refresh_token: str | None = None
    openai_account_id: str | None = None
    openai_expires_at: datetime | None = None

    def available_providers(self) -> set[str]:
        """Return the set of providers that have usable credentials."""
        available: set[str] = set()
        if self.anthropic_api_key:
            available.add(providers.ANTHROPIC)
        if self.kimi_api_key:
            available.add(providers.KIMI)
        if self.openai_access_token:
            available.add(providers.OPENAI)
        return available

    def has_provider(self, provider: str) -> bool:
        return provider in self.available_providers()

    def has_any(self) -> bool:
        return bool(self.available_providers())


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def build_credentials() -> Credentials:
    """Load all provider credentials from the DB (falling back to env)."""
    async with async_session() as session:
        result = await session.execute(select(Setting))
        db = {s.key: s.value for s in result.scalars().all()}

    return Credentials(
        anthropic_api_key=db.get("anthropic_api_key") or settings.anthropic_api_key or None,
        kimi_api_key=db.get("kimi_api_key") or settings.kimi_api_key or None,
        openai_access_token=db.get(OPENAI_ACCESS_TOKEN_KEY) or None,
        openai_refresh_token=db.get(OPENAI_REFRESH_TOKEN_KEY) or None,
        openai_account_id=db.get(OPENAI_ACCOUNT_ID_KEY) or None,
        openai_expires_at=_parse_dt(db.get(OPENAI_EXPIRES_AT_KEY)),
    )


async def _store_settings(values: dict[str, str]) -> None:
    async with async_session() as session:
        for key, value in values.items():
            result = await session.execute(select(Setting).where(Setting.key == key))
            existing = result.scalar_one_or_none()
            if existing:
                existing.value = value
                existing.updated_at = datetime.now(UTC)
            else:
                session.add(Setting(key=key, value=value))
        await session.commit()


async def refresh_openai_token(creds: Credentials) -> str | None:
    """Refresh the OpenAI access token using the stored refresh token.

    Persists the new tokens and updates ``creds`` in place. Returns the new
    access token, or ``None`` if refresh failed (the caller should treat the
    OpenAI provider as unavailable).
    """
    if not creds.openai_refresh_token:
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                providers.OPENAI_TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "client_id": providers.OPENAI_CLIENT_ID,
                    "refresh_token": creds.openai_refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.exception("Failed to refresh OpenAI access token")
        return None

    access_token = data.get("access_token")
    if not access_token:
        logger.error("OpenAI token refresh returned no access_token")
        return None

    refresh_token = data.get("refresh_token", creds.openai_refresh_token)
    expires_in = int(data.get("expires_in", 3600))
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    creds.openai_access_token = access_token
    creds.openai_refresh_token = refresh_token
    creds.openai_expires_at = expires_at

    await _store_settings(
        {
            OPENAI_ACCESS_TOKEN_KEY: access_token,
            OPENAI_REFRESH_TOKEN_KEY: refresh_token,
            OPENAI_EXPIRES_AT_KEY: expires_at.isoformat(),
        }
    )
    logger.info("Refreshed OpenAI access token (expires %s)", expires_at.isoformat())
    return access_token


async def ensure_openai_token(creds: Credentials) -> str | None:
    """Return a valid OpenAI access token, refreshing if it is near expiry."""
    if not creds.openai_access_token:
        return None

    # Refresh proactively with a 5-minute buffer.
    if creds.openai_expires_at and creds.openai_expires_at <= datetime.now(UTC) + timedelta(
        minutes=5
    ):
        refreshed = await refresh_openai_token(creds)
        return refreshed or creds.openai_access_token

    return creds.openai_access_token
