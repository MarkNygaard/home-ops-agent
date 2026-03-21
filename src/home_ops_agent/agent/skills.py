"""Skills registry — groups tools into enable/disable-able skill bundles."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from home_ops_agent.agent.core import ToolDefinition
from home_ops_agent.database import Setting, async_session

logger = logging.getLogger(__name__)


@dataclass
class SkillDefinition:
    """A skill is a named group of tools that can be enabled/disabled."""

    id: str
    name: str
    description: str
    builtin: bool = False
    config_fields: list[dict[str, Any]] = field(default_factory=list)
    get_tools: Callable[[dict[str, str]], list[ToolDefinition]] = field(
        default_factory=lambda: lambda config: []
    )


class SkillRegistry:
    """Registry of all available skills."""

    def __init__(self):
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition):
        """Register a skill."""
        self._skills[skill.id] = skill

    def get_all(self) -> list[SkillDefinition]:
        """Get all registered skills."""
        return list(self._skills.values())

    def get(self, skill_id: str) -> SkillDefinition | None:
        """Get a skill by ID."""
        return self._skills.get(skill_id)

    async def _get_skill_settings(self, skill_id: str) -> tuple[bool, dict[str, str]]:
        """Read enabled state and config for a skill from the DB."""
        skill = self._skills.get(skill_id)
        if skill is None:
            return False, {}

        # Built-in skills are always enabled
        if skill.builtin:
            return True, {}

        async with async_session() as session:
            result = await session.execute(
                select(Setting).where(
                    Setting.key.in_(
                        [
                            f"skill_{skill_id}_enabled",
                            f"skill_{skill_id}_config",
                        ]
                    )
                )
            )
            db_settings = {s.key: s.value for s in result.scalars().all()}

        enabled = db_settings.get(f"skill_{skill_id}_enabled", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        config_raw = db_settings.get(f"skill_{skill_id}_config", "{}")
        try:
            config = json.loads(config_raw)
        except (json.JSONDecodeError, TypeError):
            config = {}

        return enabled, config

    async def get_skill_state(self, skill_id: str) -> dict[str, Any]:
        """Get the full state of a skill (for API responses)."""
        skill = self._skills.get(skill_id)
        if skill is None:
            return {}

        enabled, config = await self._get_skill_settings(skill_id)

        # For built-in skills, always enabled
        if skill.builtin:
            enabled = True

        # Build config with defaults from config_fields
        full_config = {}
        for cf in skill.config_fields:
            full_config[cf["key"]] = config.get(cf["key"], cf.get("default", ""))

        return {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "builtin": skill.builtin,
            "enabled": enabled,
            "config_fields": skill.config_fields,
            "config": full_config,
            "tool_count": len(skill.get_tools(full_config)),
        }

    async def get_all_enabled_tools(self) -> list[ToolDefinition]:
        """Get tools from all enabled skills."""
        tools: list[ToolDefinition] = []

        for skill_id, skill in self._skills.items():
            enabled, config = await self._get_skill_settings(skill_id)

            if not enabled and not skill.builtin:
                continue

            # Build config with defaults
            full_config = {}
            for cf in skill.config_fields:
                full_config[cf["key"]] = config.get(cf["key"], cf.get("default", ""))

            try:
                skill_tools = skill.get_tools(full_config)
                tools.extend(skill_tools)
            except Exception:
                logger.exception("Failed to get tools for skill %s", skill_id)

        return tools

    async def update_skill(
        self, skill_id: str, enabled: bool | None = None, config: dict | None = None
    ):
        """Update a skill's enabled state and/or config in the DB."""
        skill = self._skills.get(skill_id)
        if skill is None:
            raise ValueError(f"Unknown skill: {skill_id}")

        if skill.builtin and enabled is False:
            raise ValueError(f"Cannot disable built-in skill: {skill_id}")

        async with async_session() as session:
            if enabled is not None and not skill.builtin:
                key = f"skill_{skill_id}_enabled"
                result = await session.execute(select(Setting).where(Setting.key == key))
                existing = result.scalar_one_or_none()
                if existing:
                    existing.value = str(enabled).lower()
                else:
                    session.add(Setting(key=key, value=str(enabled).lower()))

            if config is not None:
                key = f"skill_{skill_id}_config"
                value = json.dumps(config)
                result = await session.execute(select(Setting).where(Setting.key == key))
                existing = result.scalar_one_or_none()
                if existing:
                    existing.value = value
                else:
                    session.add(Setting(key=key, value=value))

            await session.commit()


# Global registry instance
registry = SkillRegistry()


def init_registry():
    """Register all built-in and optional skills."""
    from home_ops_agent.agent.tools.flux import SKILL as flux_skill
    from home_ops_agent.agent.tools.github import SKILL as github_skill
    from home_ops_agent.agent.tools.kubernetes import SKILL as kubernetes_skill
    from home_ops_agent.agent.tools.loki import SKILL as loki_skill
    from home_ops_agent.agent.tools.ntfy import SKILL as ntfy_skill
    from home_ops_agent.agent.tools.prometheus import SKILL as prometheus_skill

    registry.register(kubernetes_skill)
    registry.register(github_skill)
    registry.register(ntfy_skill)
    registry.register(prometheus_skill)
    registry.register(loki_skill)
    registry.register(flux_skill)
