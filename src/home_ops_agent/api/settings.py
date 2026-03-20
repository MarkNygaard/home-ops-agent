"""Settings API — manage agent configuration via web UI."""

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from home_ops_agent.auth.oauth import (
    exchange_code,
    generate_pkce_pair,
    get_authorization_url,
    get_valid_token,
    store_tokens,
)
from home_ops_agent.auth.session import create_session, get_session
from home_ops_agent.config import settings
from home_ops_agent.database import OAuthToken, Setting, async_session

router = APIRouter()


class UpdateSetting(BaseModel):
    value: str


@router.get("/api/settings")
async def get_settings():
    """Get all agent settings."""
    async with async_session() as session:
        result = await session.execute(select(Setting))
        db_settings = {s.key: s.value for s in result.scalars().all()}

    # Check OAuth status
    oauth_status = "not_configured"
    token_expires = None
    if db_settings.get("auth_method") == "oauth":
        token = await get_valid_token()
        if token:
            oauth_status = "active"
            async with async_session() as session:
                result = await session.execute(
                    select(OAuthToken).order_by(OAuthToken.created_at.desc()).limit(1)
                )
                token_record = result.scalar_one_or_none()
                if token_record:
                    token_expires = token_record.expires_at.isoformat()
        else:
            oauth_status = "expired"

    return {
        "pr_mode": db_settings.get("pr_mode", "comment_only"),
        "auth_method": db_settings.get("auth_method", "api_key"),
        "oauth_status": oauth_status,
        "oauth_token_expires": token_expires,
        "has_api_key": bool(db_settings.get("anthropic_api_key") or settings.anthropic_api_key),
        "has_oauth_credentials": bool(
            settings.anthropic_client_id and settings.anthropic_client_secret
        ),
        "alert_cooldown_seconds": int(
            db_settings.get("alert_cooldown_seconds", settings.alert_cooldown_seconds)
        ),
        "ntfy_topics": db_settings.get(
            "ntfy_topics",
            f"{settings.ntfy_alertmanager_topic},{settings.ntfy_gatus_topic}",
        ),
        "pr_check_interval_seconds": int(
            db_settings.get("pr_check_interval_seconds", settings.pr_check_interval_seconds)
        ),
        "models": {
            "pr_review": db_settings.get("model_pr_review", settings.model_pr_review),
            "alert_triage": db_settings.get("model_alert_triage", settings.model_alert_triage),
            "alert_fix": db_settings.get("model_alert_fix", settings.model_alert_fix),
            "code_fix": db_settings.get("model_code_fix", settings.model_code_fix),
            "chat": db_settings.get("model_chat", settings.model_chat),
        },
    }


@router.put("/api/settings/{key}")
async def update_setting(key: str, body: UpdateSetting):
    """Update a single setting."""
    allowed_keys = {
        "pr_mode",
        "auth_method",
        "anthropic_api_key",
        "alert_cooldown_seconds",
        "ntfy_topics",
        "pr_check_interval_seconds",
        "model_pr_review",
        "model_alert_triage",
        "model_alert_fix",
        "model_code_fix",
        "model_chat",
    }
    if key not in allowed_keys:
        return {"error": f"Unknown setting: {key}"}

    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key == key))
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = body.value
            existing.updated_at = datetime.now(UTC)
        else:
            session.add(Setting(key=key, value=body.value))

        await session.commit()

    return {"status": "ok", "key": key}


# --- OAuth flow endpoints ---


@router.get("/auth/login")
async def oauth_login():
    """Start the OAuth authorization flow."""
    state = secrets.token_urlsafe(32)
    verifier, challenge = generate_pkce_pair()

    # Store verifier in session
    session_id = create_session({"state": state, "verifier": verifier})

    url = get_authorization_url(state, challenge)

    response = RedirectResponse(url=url)
    response.set_cookie("oauth_session", session_id, httponly=True, samesite="lax", max_age=600)
    return response


@router.get("/auth/callback")
async def oauth_callback(code: str, state: str, request: Request):
    """Handle OAuth callback from Anthropic."""
    session_id = request.cookies.get("oauth_session")
    if not session_id:
        return {"error": "No OAuth session found"}

    session_data = get_session(session_id)
    if not session_data:
        return {"error": "OAuth session expired"}

    if session_data.get("state") != state:
        return {"error": "State mismatch — possible CSRF attack"}

    verifier = session_data["verifier"]

    try:
        tokens = await exchange_code(code, verifier)
        await store_tokens(
            tokens["access_token"],
            tokens["refresh_token"],
            tokens["expires_in"],
        )

        # Update auth method to oauth
        async with async_session() as session:
            result = await session.execute(select(Setting).where(Setting.key == "auth_method"))
            existing = result.scalar_one_or_none()
            if existing:
                existing.value = "oauth"
            else:
                session.add(Setting(key="auth_method", value="oauth"))
            await session.commit()

        return RedirectResponse(url="/?auth=success")
    except Exception as e:
        return {"error": f"Token exchange failed: {e}"}
