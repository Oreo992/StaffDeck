from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.capability_evolution.service import CapabilityEvolutionService
from app.db.models import (
    AgentEvent,
    AgentProfile,
    AgentResourceBinding,
    CapabilityEvolutionProposal,
    ChatSession,
    GeneralSkill,
    MessageFeedback,
    Tenant,
    User,
)


NOW = datetime(2026, 8, 8, 12, 0, 0)


def test_learning_creates_reviewable_proposal_without_mutating_skill() -> None:
    with _test_session() as db:
        owner, agent, skill = _seed_learning_evidence(db)

        proposals = CapabilityEvolutionService(db).learn_from_recent_feedback(
            tenant_id="tenant_demo",
            agent_id=agent.id,
            current_user=owner,
            now=NOW,
        )

        assert len(proposals) == 1
        proposal = proposals[0]
        assert proposal.status == "pending"
        assert proposal.target_resource_id == skill.id
        assert proposal.target_label == "竞品研究"
        assert proposal.before_content == "# 竞品研究\n\n先核对 ASIN。\n"
        assert "最终结论前核对 90 天价格趋势" in proposal.after_content
        assert proposal.evidence_json[0]["feedback_id"] == "feedback_1"
        assert db.get(GeneralSkill, skill.id).skill_markdown == proposal.before_content


def test_applying_proposal_updates_skill_and_tracks_later_reuse() -> None:
    with _test_session() as db:
        owner, agent, skill = _seed_learning_evidence(db)
        service = CapabilityEvolutionService(db)
        proposal = service.learn_from_recent_feedback(
            tenant_id="tenant_demo",
            agent_id=agent.id,
            current_user=owner,
            now=NOW,
        )[0]

        applied = service.apply_proposal(
            tenant_id="tenant_demo",
            agent_id=agent.id,
            proposal_id=proposal.id,
            current_user=owner,
            now=NOW,
        )
        skill_after = db.get(GeneralSkill, skill.id)

        assert applied.status == "applied"
        assert applied.applied_at == NOW
        assert skill_after is not None
        assert skill_after.skill_markdown == proposal.after_content
        assert skill_after.skill_files_json[0]["content"] == proposal.after_content

        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="claude_skill_loaded",
                payload_json={"slug": "competitor-research"},
                created_at=NOW + timedelta(hours=1),
            )
        )
        db.commit()

        read = service.list_proposals(
            tenant_id="tenant_demo",
            agent_id=agent.id,
            current_user=owner,
        )[0]
        assert read.reuse_count == 1
        assert read.last_reused_at == NOW + timedelta(hours=1)


def test_apply_rejects_stale_proposal_instead_of_overwriting_newer_skill() -> None:
    with _test_session() as db:
        owner, agent, skill = _seed_learning_evidence(db)
        service = CapabilityEvolutionService(db)
        proposal = service.learn_from_recent_feedback(
            tenant_id="tenant_demo",
            agent_id=agent.id,
            current_user=owner,
            now=NOW,
        )[0]
        skill.skill_markdown = "# 竞品研究\n\n管理员刚刚更新的版本。\n"
        db.add(skill)
        db.commit()

        with pytest.raises(HTTPException) as error:
            service.apply_proposal(
                tenant_id="tenant_demo",
                agent_id=agent.id,
                proposal_id=proposal.id,
                current_user=owner,
                now=NOW,
            )

        assert error.value.status_code == 409
        assert db.get(GeneralSkill, skill.id).skill_markdown.endswith("管理员刚刚更新的版本。\n")
        assert db.get(CapabilityEvolutionProposal, proposal.id).status == "pending"


def test_learning_requires_agent_manager() -> None:
    with _test_session() as db:
        _owner, agent, _skill = _seed_learning_evidence(db)
        other = User(
            id="user_other",
            tenant_id="tenant_demo",
            username="other",
            password_hash="x",
        )
        db.add(other)
        db.commit()

        with pytest.raises(HTTPException) as error:
            CapabilityEvolutionService(db).learn_from_recent_feedback(
                tenant_id="tenant_demo",
                agent_id=agent.id,
                current_user=other,
                now=NOW,
            )

        assert error.value.status_code == 403
        assert db.exec(select(CapabilityEvolutionProposal)).all() == []


def test_learning_skips_unclear_feedback_without_actionable_issue() -> None:
    with _test_session() as db:
        owner, agent, _skill = _seed_learning_evidence(db)
        feedback = db.get(MessageFeedback, "feedback_1")
        assert feedback is not None
        feedback.analysis_bucket = "user_random_or_unclear"
        db.add(feedback)
        db.commit()

        proposals = CapabilityEvolutionService(db).learn_from_recent_feedback(
            tenant_id="tenant_demo",
            agent_id=agent.id,
            current_user=owner,
            now=NOW,
        )

        assert proposals == []


def test_learning_captures_repeated_successful_tool_pattern() -> None:
    with _test_session() as db:
        owner, agent, skill = _seed_learning_evidence(db)
        feedback = db.get(MessageFeedback, "feedback_1")
        assert feedback is not None
        db.delete(feedback)
        for index in (1, 2):
            session_id = f"successful_session_{index}"
            db.add(
                ChatSession(
                    id=session_id,
                    tenant_id="tenant_demo",
                    user_id=owner.id,
                    agent_id=agent.id,
                    title=f"成功研究 {index}",
                )
            )
            db.add_all(
                [
                    AgentEvent(
                        tenant_id="tenant_demo",
                        session_id=session_id,
                        event_type="claude_skill_loaded",
                        payload_json={"slug": skill.slug},
                        created_at=NOW - timedelta(hours=index),
                    ),
                    AgentEvent(
                        tenant_id="tenant_demo",
                        session_id=session_id,
                        event_type="tool_call_finished",
                        payload_json={"tool_name": "readonly.lookup", "success": True},
                        created_at=NOW - timedelta(hours=index) + timedelta(minutes=1),
                    ),
                ]
            )
        db.commit()

        proposals = CapabilityEvolutionService(db).learn_from_recent_feedback(
            tenant_id="tenant_demo",
            agent_id=agent.id,
            current_user=owner,
            now=NOW,
        )

        assert len(proposals) == 1
        proposal = proposals[0]
        assert proposal.target_resource_id == skill.id
        assert proposal.evidence_json[0]["kind"] == "successful_pattern"
        assert "只读工具" in proposal.instruction
        assert "最近 2 次" in proposal.summary


def _seed_learning_evidence(db: Session) -> tuple[User, AgentProfile, GeneralSkill]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    owner = User(
        id="user_owner",
        tenant_id="tenant_demo",
        username="owner",
        password_hash="x",
    )
    agent = AgentProfile(
        id="agent_qqq_claude",
        tenant_id="tenant_demo",
        name="QQQ · Claude",
        metadata_json={"owner_user_id": owner.id, "owner_username": owner.username},
    )
    skill = GeneralSkill(
        id="genskill_competitor",
        tenant_id="tenant_demo",
        slug="competitor-research",
        name="竞品研究",
        skill_markdown="# 竞品研究\n\n先核对 ASIN。\n",
        skill_files_json=[
            {
                "path": "SKILL.md",
                "content": "# 竞品研究\n\n先核对 ASIN。\n",
                "size": 39,
                "mime_type": "text/markdown",
            }
        ],
        status="published",
    )
    db.add_all([owner, agent, skill])
    db.add(
        AgentResourceBinding(
            tenant_id="tenant_demo",
            agent_id=agent.id,
            resource_type="general_skill",
            resource_id=skill.id,
            status="active",
        )
    )
    db.add(
        ChatSession(
            id="session_1",
            tenant_id="tenant_demo",
            user_id=owner.id,
            agent_id=agent.id,
            title="竞品复盘",
        )
    )
    db.add(
        AgentEvent(
            tenant_id="tenant_demo",
            session_id="session_1",
            event_type="claude_skill_loaded",
            payload_json={"slug": skill.slug},
            created_at=NOW - timedelta(days=1),
        )
    )
    db.add(
        MessageFeedback(
            id="feedback_1",
            tenant_id="tenant_demo",
            session_id="session_1",
            message_id="assistant_1",
            user_id=owner.id,
            rating="down",
            analysis_status="analyzed",
            analysis_bucket="skill_issue",
            analysis_summary="价格趋势证据不足。",
            analysis_reason="结论没有核对足够长的价格区间。",
            analysis_json={
                "evidence": ["报告只展示了近 7 天价格"],
                "suggested_action": "最终结论前核对 90 天价格趋势",
            },
            analyzed_at=NOW - timedelta(days=1),
            created_at=NOW - timedelta(days=1),
            updated_at=NOW - timedelta(days=1),
        )
    )
    db.commit()
    return owner, agent, skill


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
