"""Settings API — manage agent configuration via web UI."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import delete, select

from home_ops_agent.agent.prompts import DEFAULTS as PROMPT_DEFAULTS
from home_ops_agent.auth import credentials as creds
from home_ops_agent.config import settings
from home_ops_agent.database import Setting, async_session

router = APIRouter()


def _mask_key(key: str) -> str:
    """Return a masked version of an API key for display."""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:7]}...{key[-4:]}"


class UpdateSetting(BaseModel):
    value: str


@router.get("/api/settings")
async def get_settings():
    """Get all agent settings."""
    async with async_session() as session:
        result = await session.execute(select(Setting))
        db_settings = {s.key: s.value for s in result.scalars().all()}

    anthropic_key = db_settings.get("anthropic_api_key") or settings.anthropic_api_key
    kimi_key = db_settings.get("kimi_api_key") or settings.kimi_api_key
    openai_token = db_settings.get(creds.OPENAI_ACCESS_TOKEN_KEY)

    return {
        "agent_enabled": db_settings.get("agent_enabled", "true").lower() in ("true", "1", "yes"),
        "pr_mode": db_settings.get("pr_mode", "comment_only"),
        # Per-provider auth status — all three can be configured simultaneously.
        "providers": {
            "anthropic": {
                "configured": bool(anthropic_key),
                "hint": _mask_key(anthropic_key),
            },
            "kimi": {
                "configured": bool(kimi_key),
                "hint": _mask_key(kimi_key),
            },
            "openai": {
                "configured": bool(openai_token),
                "account_id": db_settings.get(creds.OPENAI_ACCOUNT_ID_KEY) or None,
                "expires_at": db_settings.get(creds.OPENAI_EXPIRES_AT_KEY) or None,
            },
        },
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
        "chat_suggestions": db_settings.get(
            "chat_suggestions",
            "What pods are failing?|Show me recent alerts|List pending PRs|Check cluster health",
        ),
        "models": {
            "pr_review": db_settings.get("model_pr_review", settings.model_pr_review),
            "alert_triage": db_settings.get("model_alert_triage", settings.model_alert_triage),
            "alert_fix": db_settings.get("model_alert_fix", settings.model_alert_fix),
            "code_fix": db_settings.get("model_code_fix", settings.model_code_fix),
            "deep_review": db_settings.get("model_deep_review", settings.model_deep_review),
            "chat": db_settings.get("model_chat", settings.model_chat),
        },
    }


ALLOWED_SETTING_KEYS = {
    "agent_enabled",
    "pr_mode",
    "anthropic_api_key",
    "kimi_api_key",
    "alert_cooldown_seconds",
    "ntfy_topics",
    "pr_check_interval_seconds",
    "model_pr_review",
    "prompt_cluster_context",
    "prompt_pr_review",
    "prompt_alert_response",
    "prompt_chat",
    "model_alert_triage",
    "model_alert_fix",
    "model_code_fix",
    "model_deep_review",
    "model_chat",
    "chat_suggestions",
}


@router.put("/api/settings/{key}")
async def update_setting(key: str, body: UpdateSetting):
    """Update a single setting."""
    if key not in ALLOWED_SETTING_KEYS:
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


@router.get("/api/prompts")
async def get_prompts():
    """Get all agent prompts (custom or defaults)."""
    async with async_session() as session:
        result = await session.execute(select(Setting).where(Setting.key.like("prompt_%")))
        db_prompts = {s.key: s.value for s in result.scalars().all()}

    return {
        name: {
            "default": default_text,
            "custom": db_prompts.get(f"prompt_{name}", ""),
            "is_customized": f"prompt_{name}" in db_prompts,
        }
        for name, default_text in PROMPT_DEFAULTS.items()
    }


@router.delete("/api/prompts/{name}")
async def reset_prompt(name: str):
    """Reset a prompt to its default by removing the custom version."""
    key = f"prompt_{name}"
    if name not in PROMPT_DEFAULTS:
        return {"error": f"Unknown prompt: {name}"}

    async with async_session() as session:
        from sqlalchemy import delete

        await session.execute(delete(Setting).where(Setting.key == key))
        await session.commit()

    return {"status": "ok", "reset": name}


# --- OpenAI (ChatGPT subscription) credential import ---
#
# This app runs as a hosted server, so it cannot receive the Codex OAuth
# localhost:1455 redirect. Instead the user authenticates locally (e.g. via the
# Codex CLI), then imports the resulting tokens here; the server keeps them
# alive via the refresh endpoint (refresh needs no redirect).


class OpenAITokens(BaseModel):
    access_token: str
    refresh_token: str
    account_id: str
    expires_in: int | None = None


@router.post("/api/auth/openai")
async def import_openai_tokens(body: OpenAITokens):
    """Import ChatGPT-subscription OAuth tokens for the OpenAI provider."""
    expires_in = body.expires_in if body.expires_in is not None else 3600
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    values = {
        creds.OPENAI_ACCESS_TOKEN_KEY: body.access_token.strip(),
        creds.OPENAI_REFRESH_TOKEN_KEY: body.refresh_token.strip(),
        creds.OPENAI_ACCOUNT_ID_KEY: body.account_id.strip(),
        creds.OPENAI_EXPIRES_AT_KEY: expires_at.isoformat(),
    }
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

    return {"status": "ok", "provider": "openai", "expires_at": expires_at.isoformat()}


@router.delete("/api/auth/{provider}")
async def disconnect_provider(provider: str):
    """Remove stored credentials for a provider."""
    key_map = {
        "anthropic": ["anthropic_api_key"],
        "kimi": ["kimi_api_key"],
        "openai": [
            creds.OPENAI_ACCESS_TOKEN_KEY,
            creds.OPENAI_REFRESH_TOKEN_KEY,
            creds.OPENAI_ACCOUNT_ID_KEY,
            creds.OPENAI_EXPIRES_AT_KEY,
        ],
    }
    keys = key_map.get(provider)
    if not keys:
        return {"error": f"Unknown provider: {provider}"}

    async with async_session() as session:
        await session.execute(delete(Setting).where(Setting.key.in_(keys)))
        await session.commit()

    return {"status": "ok", "disconnected": provider}
