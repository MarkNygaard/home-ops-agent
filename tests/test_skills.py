"""Tests for agent/skills.py — SkillRegistry and skill management."""

from home_ops_agent.agent.core import ToolDefinition
from home_ops_agent.agent.skills import SkillDefinition, SkillRegistry

# --- Pure SkillRegistry tests (no DB) ---


def test_registry_register_and_get():
    reg = SkillRegistry()
    skill = SkillDefinition(id="test", name="Test", description="A test skill")
    reg.register(skill)
    assert reg.get("test") is skill


def test_registry_get_unknown():
    reg = SkillRegistry()
    assert reg.get("nonexistent") is None


def test_registry_get_all():
    reg = SkillRegistry()
    s1 = SkillDefinition(id="s1", name="S1", description="Skill 1")
    s2 = SkillDefinition(id="s2", name="S2", description="Skill 2")
    reg.register(s1)
    reg.register(s2)
    all_skills = reg.get_all()
    assert len(all_skills) == 2
    assert s1 in all_skills
    assert s2 in all_skills


def test_registry_get_all_empty():
    reg = SkillRegistry()
    assert reg.get_all() == []


def test_skill_definition_defaults():
    skill = SkillDefinition(id="test", name="Test", description="desc")
    assert skill.builtin is False
    assert skill.config_fields == []
    assert skill.get_tools({}) == []


# --- DB-backed tests ---


async def test_get_skill_settings_builtin_always_enabled(db_session):
    reg = SkillRegistry()
    reg.register(SkillDefinition(id="k8s", name="K8s", description="", builtin=True))
    enabled, config = await reg._get_skill_settings("k8s")
    assert enabled is True
    assert config == {}


async def test_get_skill_settings_optional_disabled_by_default(db_session):
    reg = SkillRegistry()
    reg.register(SkillDefinition(id="prom", name="Prometheus", description=""))
    enabled, config = await reg._get_skill_settings("prom")
    assert enabled is False


async def test_get_skill_settings_optional_enabled(db_session):
    from home_ops_agent.database import Setting

    reg = SkillRegistry()
    reg.register(SkillDefinition(id="prom", name="Prometheus", description=""))
    db_session.add(Setting(key="skill_prom_enabled", value="true"))
    await db_session.flush()

    enabled, config = await reg._get_skill_settings("prom")
    assert enabled is True


async def test_update_skill_unknown_raises(db_session):
    import pytest

    reg = SkillRegistry()
    with pytest.raises(ValueError, match="Unknown skill"):
        await reg.update_skill("nonexistent", enabled=True)


async def test_update_skill_disable_builtin_raises(db_session):
    import pytest

    reg = SkillRegistry()
    reg.register(SkillDefinition(id="k8s", name="K8s", description="", builtin=True))
    with pytest.raises(ValueError, match="Cannot disable built-in"):
        await reg.update_skill("k8s", enabled=False)


async def test_get_all_enabled_tools(db_session):
    def make_tools(config):
        return [
            ToolDefinition(name="tool1", description="t1", input_schema={}, handler=lambda p: "ok")
        ]

    reg = SkillRegistry()
    reg.register(
        SkillDefinition(id="k8s", name="K8s", description="", builtin=True, get_tools=make_tools)
    )
    reg.register(
        SkillDefinition(id="prom", name="Prom", description="", builtin=False, get_tools=make_tools)
    )

    tools = await reg.get_all_enabled_tools()
    # Only builtin should be enabled
    assert len(tools) == 1
    assert tools[0].name == "tool1"


async def test_get_skill_state(db_session):
    def make_tools(config):
        return [ToolDefinition(name="t1", description="d", input_schema={}, handler=lambda p: "ok")]

    reg = SkillRegistry()
    reg.register(
        SkillDefinition(
            id="k8s",
            name="Kubernetes",
            description="K8s tools",
            builtin=True,
            get_tools=make_tools,
        )
    )
    state = await reg.get_skill_state("k8s")
    assert state["id"] == "k8s"
    assert state["enabled"] is True
    assert state["builtin"] is True
    assert state["tool_count"] == 1


async def test_get_skill_settings_unknown_skill(db_session):
    reg = SkillRegistry()
    enabled, config = await reg._get_skill_settings("unknown")
    assert enabled is False
    assert config == {}
