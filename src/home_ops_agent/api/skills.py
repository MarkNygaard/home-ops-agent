"""Skills API — manage agent skill enable/disable and configuration."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from home_ops_agent.agent.skills import registry

router = APIRouter()


class UpdateSkill(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None


@router.get("/api/skills")
async def list_skills():
    """Get all skills with their enabled state and config."""
    skills = registry.get_all()
    result = []
    for skill in skills:
        state = await registry.get_skill_state(skill.id)
        result.append(state)
    return result


@router.put("/api/skills/{skill_id}")
async def update_skill(skill_id: str, body: UpdateSkill):
    """Update a skill's enabled state and/or config."""
    skill = registry.get(skill_id)
    if skill is None:
        return {"error": f"Unknown skill: {skill_id}"}

    if skill.builtin and body.enabled is False:
        return {"error": f"Cannot disable built-in skill: {skill_id}"}

    try:
        await registry.update_skill(skill_id, enabled=body.enabled, config=body.config)
    except ValueError as e:
        return {"error": str(e)}

    state = await registry.get_skill_state(skill_id)
    return {"status": "ok", "skill": state}
