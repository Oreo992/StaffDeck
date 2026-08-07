from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app.core.capability_manifest import (
    CapabilityAuthorizationError,
    CapabilityManifestBuilder,
    current_step_capability_refs,
)
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    GeneralSkill,
    KnowledgeBase,
    Skill,
    Tool,
)


def _memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _bind(db: Session, agent_id: str, resource_type: str, resource_id: str) -> None:
    db.add(
        AgentResourceBinding(
            tenant_id="tenant-demo",
            agent_id=agent_id,
            resource_type=resource_type,
            resource_id=resource_id,
            status="active",
        )
    )


def _seed_manifest_resources(db: Session) -> AgentProfile:
    agent = AgentProfile(
        id="agent-overall",
        tenant_id="tenant-demo",
        name="QQQ",
        is_overall=True,
    )
    read_tool = Tool(
        id="tool-read",
        tenant_id="tenant-demo",
        name="product.lookup",
        description="查询产品",
        method="GET",
        url="https://example.invalid/products",
        input_schema={"type": "object"},
    )
    colliding_tool = Tool(
        id="tool-send-file",
        tenant_id="tenant-demo",
        name="send_file",
        description="外部同名工具",
        method="POST",
        url="https://example.invalid/send",
        config_json={"requires_confirmation": True},
    )
    general_skill = GeneralSkill(
        id="general-research",
        tenant_id="tenant-demo",
        slug="research",
        name="研究方法",
        description="执行研究方法",
        skill_markdown="# Research",
        status="published",
    )
    knowledge = KnowledgeBase(
        id="kb-market",
        tenant_id="tenant-demo",
        name="市场资料",
        status="active",
    )
    db.add(agent)
    db.add(read_tool)
    db.add(colliding_tool)
    db.add(general_skill)
    db.add(knowledge)
    _bind(db, agent.id, "tool", read_tool.id)
    _bind(db, agent.id, "tool", colliding_tool.id)
    _bind(db, agent.id, "general_skill", general_skill.id)
    _bind(db, agent.id, "knowledge_base", knowledge.id)
    db.commit()
    return agent


def test_manifest_projects_visible_resources_with_conservative_effects() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        agent = _seed_manifest_resources(db)
        builder = CapabilityManifestBuilder(db)

        manifest = builder.build("tenant-demo", agent.id, None, None)
        repeated = builder.build("tenant-demo", agent.id, None, None)

    by_name = {item.name: item for item in manifest.available}
    assert {"send_file", "publish_file", "product.lookup", "knowledge_search"} <= set(
        by_name
    )
    assert by_name["send_file"].capability_id == "builtin.file.send"
    assert by_name["external_tool.tool-send-file"].effect_level == "destructive"
    assert by_name["product.lookup"].effect_level == "read"
    assert by_name["general_skill.research"].effect_level == "write"
    assert by_name["knowledge_search"].effect_level == "read"
    assert manifest.snapshot_revision == repeated.snapshot_revision
    assert manifest.snapshot_revision.startswith("sha256:")


def test_sop_specific_tool_is_hidden_until_explicitly_referenced() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        agent = _seed_manifest_resources(db)
        tool = db.get(Tool, "tool-read")
        assert tool is not None
        tool.config_json = {"capability_scope": "sop_specific"}
        tool.allowed_skills_json = ["amazon-sop"]
        skill = Skill(
            id="skill-amazon",
            tenant_id="tenant-demo",
            skill_id="amazon-sop",
            name="Amazon SOP",
            status="published",
            content_json={
                "start_node_id": "research",
                "nodes": [
                    {
                        "node_id": "research",
                        "capability_refs": {"tool_ids": [tool.id]},
                    }
                ],
            },
        )
        db.add(tool)
        db.add(skill)
        db.commit()
        builder = CapabilityManifestBuilder(db)

        without_sop = builder.build("tenant-demo", agent.id, None, None)
        with_sop = builder.build("tenant-demo", agent.id, skill, "research")

    assert "product.lookup" not in without_sop.allowed_names()
    assert "product.lookup" in with_sop.allowed_names()
    assert current_step_capability_refs(skill, "research")["tool_ids"] == [
        "tool-read"
    ]


def test_manifest_reports_explicit_but_unauthorized_tool() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        agent = _seed_manifest_resources(db)
        tool = db.get(Tool, "tool-read")
        assert tool is not None
        tool.config_json = {"capability_scope": "sop_specific"}
        tool.allowed_skills_json = ["other-sop"]
        skill = Skill(
            id="skill-amazon",
            tenant_id="tenant-demo",
            skill_id="amazon-sop",
            name="Amazon SOP",
            status="published",
            content_json={
                "start_node_id": "research",
                "nodes": [
                    {
                        "node_id": "research",
                        "allowed_actions": ["call_tool:product.lookup"],
                    }
                ],
            },
        )
        db.add(tool)
        db.add(skill)
        db.commit()

        manifest = CapabilityManifestBuilder(db).build(
            "tenant-demo", agent.id, skill, "research"
        )

    denied = next(
        item
        for item in manifest.unavailable_references
        if item.capability_id == "tool-read"
    )
    assert denied.available is False
    assert "allowed_skills" in str(denied.unavailable_reason)


def test_manifest_fails_closed_for_unknown_agent() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        with pytest.raises(CapabilityAuthorizationError):
            CapabilityManifestBuilder(db).build(
                "tenant-demo", "missing-agent", None, None
            )
