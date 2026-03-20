"""Anthropic OAuth flow for Claude Max/Pro subscription access."""

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from home_ops_agent.config import settings
from home_ops_agent.database import OAuthToken, Setting, async_session

logger = logging.getLogger(__name__)

ANTHROPIC_AUTH_URL = "https://console.anthropic.com/oauth/authorize"
ANTHROPIC_TOKEN_URL = "https://console.anthropic.com/oauth/token"


def generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge."""
    verifier = secrets.token_urlsafe(64)
    challenge = hashlib.sha256(verifier.encode()).digest()
    import base64
    challenge_b64 = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
    return verifier, challenge_b64


def get_authorization_url(state: str, code_challenge: str) -> str:
    """Build the Anthropic OAuth authorization URL."""
    params = {
        "client_id": settings.anthropic_client_id,
        "redirect_uri": f"{settings.base_url}/auth/callback",
        "response_type": "code",
        "scope": "user:inference",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{ANTHROPIC_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange authorization code for tokens."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            ANTHROPIC_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.anthropic_client_id,
                "client_secret": settings.anthropic_client_secret,
                "code": code,
                "redirect_uri": f"{settings.base_url}/auth/callback",
                "code_verifier": code_verifier,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Refresh an expired access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            ANTHROPIC_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.anthropic_client_id,
                "client_secret": settings.anthropic_client_secret,
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def store_tokens(access_token: str, refresh_token: str, expires_in: int):
    """Store OAuth tokens in the database."""
    expires_at = datetime.now(timezone.utc).replace(
        second=datetime.now(timezone.utc).second + expires_in
    )
    from datetime import timedelta
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    async with async_session() as session:
        # Upsert: delete old tokens, insert new
        from sqlalchemy import delete
        await session.execute(delete(OAuthToken))
        token = OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        session.add(token)
        await session.commit()


async def get_valid_token() -> str | None:
    """Get a valid access token, refreshing if necessary."""
    async with async_session() as session:
        result = await session.execute(
            select(OAuthToken).order_by(OAuthToken.created_at.desc()).limit(1)
        )
        token = result.scalar_one_or_none()

        if token is None:
            return None

        # Check if token is expired (with 5 min buffer)
        from datetime import timedelta
        if token.expires_at < datetime.now(timezone.utc) + timedelta(minutes=5):
            try:
                new_tokens = await refresh_access_token(token.refresh_token)
                await store_tokens(
                    new_tokens["access_token"],
                    new_tokens.get("refresh_token", token.refresh_token),
                    new_tokens["expires_in"],
                )
                return new_tokens["access_token"]
            except Exception:
                logger.exception("Failed to refresh OAuth token")
                return None

        return token.access_token


async def get_auth_method() -> str:
    """Get the configured auth method (oauth or api_key)."""
    async with async_session() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "auth_method")
        )
        setting = result.scalar_one_or_none()
        return setting.value if setting else "api_key"


async def get_claude_credentials() -> tuple[str | None, str | None]:
    """Get credentials for Claude API. Returns (api_key, oauth_token)."""
    method = await get_auth_method()

    if method == "oauth":
        token = await get_valid_token()
        return None, token
    else:
        # Use API key from env or database setting
        async with async_session() as session:
            result = await session.execute(
                select(Setting).where(Setting.key == "anthropic_api_key")
            )
            setting = result.scalar_one_or_none()
            api_key = setting.value if setting else settings.anthropic_api_key
            return api_key or None, None
