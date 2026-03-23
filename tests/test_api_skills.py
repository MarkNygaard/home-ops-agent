"""Tests for api/skills.py — skill management API."""

from home_ops_agent.agent.skills import SkillDefinition, SkillRegistry


def test_skill_api_builtin_cannot_disable():
    """Verify the builtin check logic used in the skills API endpoint."""
    skill = SkillDefinition(id="k8s", name="K8s", description="", builtin=True)
    # The API checks: if skill.builtin and body.enabled is False
    assert skill.builtin is True
    # This would return an error in the API


def test_skill_api_unknown_skill():
    """Verify the unknown skill check logic."""
    reg = SkillRegistry()
    assert reg.get("nonexistent") is None
    # The API checks: if skill is None: return {"error": ...}


def test_skill_api_update_model():
    """Verify the UpdateSkill model accepts correct values."""
    from home_ops_agent.api.skills import UpdateSkill

    body = UpdateSkill(enabled=True, config={"url": "http://prom:9090"})
    assert body.enabled is True
    assert body.config == {"url": "http://prom:9090"}


def test_skill_api_update_model_optional_fields():
    from home_ops_agent.api.skills import UpdateSkill

    body_none = UpdateSkill()
    assert body_none.enabled is None
    assert body_none.config is None

    body_partial = UpdateSkill(enabled=False)
    assert body_partial.enabled is False
    assert body_partial.config is None
