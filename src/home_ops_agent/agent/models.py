"""Per-task model resolution — checks DB settings, falls back to env config."""

from sqlalchemy import select

from home_ops_agent.config import settings
from home_ops_agent.database import Setting, async_session

# Map task names to config defaults
_DEFAULTS = {
    "pr_review": settings.model_pr_review,
    "alert_triage": settings.model_alert_triage,
    "alert_fix": settings.model_alert_fix,
    "code_fix": settings.model_code_fix,
    "chat": settings.model_chat,
}


async def get_model_for_task(task: str) -> str:
    """Get the model to use for a given task.

    Checks the DB settings first (key: model_<task>), then falls back
    to the environment variable config.
    """
    setting_key = f"model_{task}"

    async with async_session() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == setting_key)
        )
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            return setting.value

    return _DEFAULTS.get(task, settings.model_chat)
