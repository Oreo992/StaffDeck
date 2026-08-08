from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import agents as agents_api
from app.api.agents import get_agent_operations_summary
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    CapabilityEvolutionProposal,
    ChatSession,
    GeneralSkill,
    HumanHandoffRequest,
    Message,
    ScheduledTask,
    ScheduledTaskRun,
    Tenant,
    User,
)


NOW = datetime(2026, 8, 8, 12, 0, 0)


def test_operations_summary_uses_canonical_execution_records_without_double_count(
    monkeypatch,
) -> None:
    with _test_session() as db:
        admin, owner, other, agent = _seed_scope(db)
        _seed_operations(db, owner, other, agent)
        monkeypatch.setattr(agents_api, "utc_now", lambda: NOW)

        result = get_agent_operations_summary(
            agent.id,
            tenant_id="tenant_demo",
            period_days=7,
            timezone="Asia/Shanghai",
            db=db,
            current_user=admin,
        )

        assert result.metrics.running == 2
        assert result.metrics.awaiting_confirmation == 2
        assert result.metrics.completed == 3
        assert result.metrics.capability_changes == 1
        assert sum(point.value for point in result.completion_trend) == 3
        assert {item.kind for item in result.attention_items} >= {
            "handoff",
            "session",
            "scheduled_run",
            "evolution_proposal",
        }
        assert {item.kind for item in result.today_items} >= {"session", "scheduled_run"}
        assert any(
            item.session_id == "session_owner" and item.status == "已完成"
            for item in result.today_items
        )
        assert result.capability_changes[0].label == "竞品研究"
        assert result.capability_changes[0].kind == "skill"
        assert result.capability_changes[0].phase == "applied"


def test_operations_summary_admin_sees_team_scope_and_owner_sees_own_scope(monkeypatch) -> None:
    with _test_session() as db:
        admin, owner, other, agent = _seed_scope(db)
        _seed_operations(db, owner, other, agent)
        monkeypatch.setattr(agents_api, "utc_now", lambda: NOW)

        admin_result = get_agent_operations_summary(
            agent.id,
            tenant_id="tenant_demo",
            period_days=7,
            timezone="Asia/Shanghai",
            db=db,
            current_user=admin,
        )
        owner_result = get_agent_operations_summary(
            agent.id,
            tenant_id="tenant_demo",
            period_days=7,
            timezone="Asia/Shanghai",
            db=db,
            current_user=owner,
        )

        assert admin_result.metrics.completed == 3
        assert owner_result.metrics.completed == 2
        assert owner_result.metrics.awaiting_confirmation == 2


def _seed_scope(db: Session) -> tuple[User, User, User, AgentProfile]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    admin = User(
        id="user_admin",
        tenant_id="tenant_demo",
        username="admin",
        password_hash="x",
        role="admin",
    )
    owner = User(
        id="user_owner",
        tenant_id="tenant_demo",
        username="owner",
        password_hash="x",
    )
    other = User(
        id="user_other",
        tenant_id="tenant_demo",
        username="other",
        password_hash="x",
    )
    agent = AgentProfile(
        id="agent_operations",
        tenant_id="tenant_demo",
        name="经营员工",
        metadata_json={"owner_user_id": owner.id, "owner_username": owner.username},
    )
    db.add_all([admin, owner, other, agent])
    db.commit()
    return admin, owner, other, agent


def _seed_operations(
    db: Session,
    owner: User,
    other: User,
    agent: AgentProfile,
) -> None:
    sessions = [
        ChatSession(
            id="session_owner",
            tenant_id="tenant_demo",
            user_id=owner.id,
            agent_id=agent.id,
            title="Owner 研究",
            status="active",
            runtime_mode="claude_supervised",
            runtime_state_json={"status": "completed"},
        ),
        ChatSession(
            id="session_other",
            tenant_id="tenant_demo",
            user_id=other.id,
            agent_id=agent.id,
            title="团队研究",
            status="active",
        ),
        ChatSession(
            id="session_running",
            tenant_id="tenant_demo",
            user_id=owner.id,
            agent_id=agent.id,
            title="正在分析价格",
            status="running",
            updated_at=NOW,
        ),
        ChatSession(
            id="session_interrupted",
            tenant_id="tenant_demo",
            user_id=owner.id,
            agent_id=agent.id,
            title="被中断的研究",
            status="active",
        ),
        ChatSession(
            id="session_handoff",
            tenant_id="tenant_demo",
            user_id=owner.id,
            agent_id=agent.id,
            title="等待确认",
            status="handoff",
            updated_at=NOW,
        ),
        ChatSession(
            id="session_scheduled_success",
            tenant_id="tenant_demo",
            user_id=owner.id,
            agent_id=agent.id,
            title="自动任务：日报",
            status="active",
        ),
        ChatSession(
            id="session_scheduled_running",
            tenant_id="tenant_demo",
            user_id=owner.id,
            agent_id=agent.id,
            title="自动任务：监控",
            status="running",
            updated_at=NOW,
        ),
    ]
    db.add_all(sessions)
    completed_at = NOW - timedelta(days=1)
    completed_today_at = NOW - timedelta(hours=1)
    db.add_all(
        [
            Message(
                id="reply_owner",
                tenant_id="tenant_demo",
                session_id="session_owner",
                role="assistant",
                content="Owner 完成",
                created_at=completed_today_at,
            ),
            Message(
                id="reply_other",
                tenant_id="tenant_demo",
                session_id="session_other",
                role="assistant",
                content="团队完成",
                created_at=completed_at,
            ),
            Message(
                id="reply_scheduled",
                tenant_id="tenant_demo",
                session_id="session_scheduled_success",
                role="assistant",
                content="日报完成",
                created_at=completed_at,
            ),
            Message(
                id="reply_interrupted",
                tenant_id="tenant_demo",
                session_id="session_interrupted",
                role="assistant",
                content="响应已中断",
                metadata_json={"status": "interrupted"},
                created_at=completed_at,
            ),
        ]
    )

    task = ScheduledTask(
        id="task_daily",
        tenant_id="tenant_demo",
        agent_id=agent.id,
        created_by_user_id=owner.id,
        title="每日经营日报",
        prompt="生成日报",
        status="active",
        next_run_at=NOW + timedelta(hours=2),
    )
    db.add(task)
    db.add_all(
        [
            ScheduledTaskRun(
                id="run_succeeded",
                tenant_id="tenant_demo",
                scheduled_task_id=task.id,
                agent_id=agent.id,
                user_id=owner.id,
                session_id="session_scheduled_success",
                scheduled_for=completed_at,
                status="succeeded",
                started_at=completed_at,
                finished_at=completed_at,
                result_summary="日报已生成",
                updated_at=completed_at,
            ),
            ScheduledTaskRun(
                id="run_running",
                tenant_id="tenant_demo",
                scheduled_task_id=task.id,
                agent_id=agent.id,
                user_id=owner.id,
                session_id="session_scheduled_running",
                scheduled_for=NOW,
                status="running",
                started_at=NOW,
                updated_at=NOW,
            ),
            ScheduledTaskRun(
                id="run_failed",
                tenant_id="tenant_demo",
                scheduled_task_id=task.id,
                agent_id=agent.id,
                user_id=owner.id,
                scheduled_for=NOW - timedelta(hours=1),
                status="failed",
                started_at=NOW - timedelta(hours=1),
                finished_at=NOW - timedelta(minutes=50),
                error="模型暂时不可用",
                updated_at=NOW - timedelta(minutes=50),
            ),
        ]
    )
    db.add(
        HumanHandoffRequest(
            id="handoff_pending",
            tenant_id="tenant_demo",
            session_id="session_handoff",
            agent_id=agent.id,
            requester_user_id=owner.id,
            assignee_user_id=owner.id,
            pending_question="请确认是否发布报告",
            context_summary="报告已生成，等待人工确认",
            status="pending",
            updated_at=NOW,
        )
    )
    skill = GeneralSkill(
        id="general_competitor",
        tenant_id="tenant_demo",
        slug="competitor-research",
        name="竞品研究",
        skill_markdown="# 竞品研究",
        status="published",
    )
    db.add(skill)
    db.add(
        AgentResourceBinding(
            id="binding_competitor",
            tenant_id="tenant_demo",
            agent_id=agent.id,
            resource_type="general_skill",
            resource_id=skill.id,
            created_at=NOW - timedelta(days=2),
        )
    )
    db.add_all(
        [
            CapabilityEvolutionProposal(
                id="evolve_pending",
                tenant_id="tenant_demo",
                agent_id=agent.id,
                target_resource_id=skill.id,
                target_label=skill.name,
                title="补充价格趋势核对",
                instruction="最终结论前核对 90 天价格趋势",
                before_content="# 竞品研究",
                after_content="# 竞品研究\n\n- 最终结论前核对 90 天价格趋势\n",
                fingerprint="pending",
                status="pending",
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW - timedelta(hours=1),
            ),
            CapabilityEvolutionProposal(
                id="evolve_applied",
                tenant_id="tenant_demo",
                agent_id=agent.id,
                target_resource_id=skill.id,
                target_label=skill.name,
                title="改进竞品研究",
                instruction="先核对 ASIN",
                before_content="# 旧竞品研究",
                after_content="# 竞品研究",
                fingerprint="applied",
                status="applied",
                applied_at=NOW - timedelta(days=2),
                created_at=NOW - timedelta(days=3),
                updated_at=NOW - timedelta(days=2),
            ),
        ]
    )
    db.commit()


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
