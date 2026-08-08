from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlmodel import Session, select

from app.db.models import (
    AgentEvent,
    CapabilityEvolutionProposal,
    ChatSession,
    GeneralSkill,
    HumanHandoffRequest,
    Message,
    ScheduledTask,
    ScheduledTaskRun,
    User,
)
from app.operations.schema import (
    AgentCapabilityChangeRead,
    AgentOperationsItemRead,
    AgentOperationsMetricsRead,
    AgentOperationsSummaryRead,
    AgentOperationsTrendPointRead,
)
from app.security.permissions import is_admin_user


def build_agent_operations_summary(
    db: Session,
    *,
    tenant_id: str,
    agent_id: str,
    current_user: User,
    timezone: ZoneInfo,
    timezone_name: str,
    period_days: int,
    now: datetime,
) -> AgentOperationsSummaryRead:
    local_now = _as_utc(now).astimezone(timezone)
    first_local_day = local_now.date() - timedelta(days=period_days - 1)
    range_start = datetime.combine(first_local_day, time.min, tzinfo=timezone).astimezone(UTC)
    range_start_naive = range_start.replace(tzinfo=None)
    admin_scope = is_admin_user(current_user)

    session_conditions = [
        ChatSession.tenant_id == tenant_id,
        ChatSession.agent_id == agent_id,
    ]
    if not admin_scope:
        session_conditions.append(ChatSession.user_id == current_user.id)
    sessions = db.exec(select(ChatSession).where(*session_conditions)).all()
    sessions_by_id = {row.id: row for row in sessions}

    run_conditions = [
        ScheduledTaskRun.tenant_id == tenant_id,
        ScheduledTaskRun.agent_id == agent_id,
    ]
    if not admin_scope:
        run_conditions.append(ScheduledTaskRun.user_id == current_user.id)
    runs = db.exec(select(ScheduledTaskRun).where(*run_conditions)).all()
    scheduled_session_ids = {row.session_id for row in runs if row.session_id}

    message_conditions = [
        Message.tenant_id == tenant_id,
        Message.role == "assistant",
        Message.created_at >= range_start_naive,
        ChatSession.tenant_id == tenant_id,
        ChatSession.agent_id == agent_id,
    ]
    if scheduled_session_ids:
        message_conditions.append(Message.session_id.notin_(scheduled_session_ids))
    if not admin_scope:
        message_conditions.append(ChatSession.user_id == current_user.id)
    completed_message_rows = db.exec(
        select(Message)
        .join(ChatSession, Message.session_id == ChatSession.id)
        .where(*message_conditions)
        .order_by(Message.created_at.asc())
    ).all()
    completed_messages = [
        row
        for row in completed_message_rows
        if str((row.metadata_json or {}).get("status") or "").lower()
        not in {"cancelled", "interrupted", "failed", "error"}
    ]

    task_conditions = [
        ScheduledTask.tenant_id == tenant_id,
        ScheduledTask.agent_id == agent_id,
        ScheduledTask.status != "archived",
    ]
    if not admin_scope:
        task_conditions.append(ScheduledTask.created_by_user_id == current_user.id)
    tasks = db.exec(select(ScheduledTask).where(*task_conditions)).all()
    tasks_by_id = {row.id: row for row in tasks}

    handoff_conditions = [
        HumanHandoffRequest.tenant_id == tenant_id,
        HumanHandoffRequest.agent_id == agent_id,
        HumanHandoffRequest.status == "pending",
    ]
    if not admin_scope:
        handoff_conditions.append(
            or_(
                HumanHandoffRequest.assignee_user_id == current_user.id,
                HumanHandoffRequest.assignee_user_id.is_(None),
            )
        )
    handoffs = db.exec(select(HumanHandoffRequest).where(*handoff_conditions)).all()

    running_runs = [row for row in runs if row.status == "running"]
    completed_runs = [
        row
        for row in runs
        if row.status == "succeeded" and _event_time(row.finished_at, row.updated_at) >= range_start_naive
    ]
    failed_runs = [
        row
        for row in runs
        if row.status == "failed" and _event_time(row.finished_at, row.updated_at) >= range_start_naive
    ]
    running_sessions = [
        row
        for row in sessions
        if row.status == "running" and row.id not in scheduled_session_ids
    ]

    proposals = db.exec(
        select(CapabilityEvolutionProposal)
        .where(
            CapabilityEvolutionProposal.tenant_id == tenant_id,
            CapabilityEvolutionProposal.agent_id == agent_id,
        )
        .order_by(CapabilityEvolutionProposal.created_at.desc())
    ).all()
    pending_proposals = [row for row in proposals if row.status == "pending"]
    applied_proposals = [
        row
        for row in proposals
        if row.status == "applied"
        and row.applied_at is not None
        and _as_utc(row.applied_at) >= range_start
    ]
    reuse_counts = _proposal_reuse_counts(db, tenant_id, agent_id, applied_proposals)
    changes = [
        AgentCapabilityChangeRead(
            id=proposal.id,
            kind="skill",
            label=proposal.target_label,
            timestamp=_iso_utc(proposal.applied_at),
            instruction=proposal.instruction,
            reuse_count=reuse_counts.get(proposal.id, 0),
        )
        for proposal in applied_proposals
    ]
    changes.sort(key=lambda item: item.timestamp, reverse=True)

    completion_days = {
        (first_local_day + timedelta(days=index)).isoformat(): 0
        for index in range(period_days)
    }
    for row in completed_messages:
        _increment_local_day(completion_days, row.created_at, timezone)
    for row in completed_runs:
        _increment_local_day(
            completion_days,
            _event_time(row.finished_at, row.updated_at),
            timezone,
        )

    attention_items = [
        AgentOperationsItemRead(
            id=f"handoff-{row.id}",
            kind="handoff",
            title=row.pending_question or "有一项工作需要人工确认",
            description=row.context_summary or "打开对话查看完整上下文与执行证据",
            status="待确认",
            timestamp=_iso_utc(row.updated_at),
            session_id=row.session_id,
        )
        for row in handoffs
    ]
    attention_items.extend(
        AgentOperationsItemRead(
            id=f"evolution-{row.id}",
            kind="evolution_proposal",
            title=row.title,
            description=row.summary or row.instruction,
            status="待确认",
            timestamp=_iso_utc(row.updated_at),
        )
        for row in pending_proposals
    )
    attention_items.extend(
        AgentOperationsItemRead(
            id=f"session-{row.id}",
            kind="session",
            title=row.title or "未命名对话",
            description=row.summary or row.last_agent_question or "数字员工正在处理该会话",
            status="进行中",
            timestamp=_iso_utc(row.updated_at),
            session_id=row.id,
        )
        for row in running_sessions
    )
    attention_items.extend(
        _scheduled_run_item(row, tasks_by_id.get(row.scheduled_task_id), status="进行中")
        for row in running_runs
    )
    attention_items.extend(
        _scheduled_run_item(row, tasks_by_id.get(row.scheduled_task_id), status="执行异常")
        for row in failed_runs
    )
    attention_items.sort(key=lambda item: item.timestamp, reverse=True)

    today = local_now.date()
    today_items = [
        item
        for item in attention_items
        if item.kind in {"session", "scheduled_run"} and item.status == "进行中"
    ]
    running_session_ids = {row.id for row in running_sessions}
    completed_today_by_session: dict[str, Message] = {}
    for row in completed_messages:
        if row.session_id in running_session_ids:
            continue
        if _as_utc(row.created_at).astimezone(timezone).date() != today:
            continue
        previous = completed_today_by_session.get(row.session_id)
        if previous is None or _as_utc(row.created_at) > _as_utc(previous.created_at):
            completed_today_by_session[row.session_id] = row
    today_items.extend(
        AgentOperationsItemRead(
            id=f"completed-session-{row.session_id}",
            kind="session",
            title=(sessions_by_id.get(row.session_id).title if sessions_by_id.get(row.session_id) else None)
            or "已完成对话",
            description=(row.content or "数字员工已完成本次回复").strip()[:240],
            status="已完成",
            timestamp=_iso_utc(row.created_at),
            session_id=row.session_id,
        )
        for row in completed_today_by_session.values()
    )
    today_items.extend(
        AgentOperationsItemRead(
            id=f"scheduled-task-{row.id}",
            kind="scheduled_task",
            title=row.title,
            description=row.description or "今天计划执行的定时任务",
            status="今日计划",
            timestamp=_iso_utc(row.next_run_at),
        )
        for row in tasks
        if row.status == "active"
        and row.next_run_at is not None
        and _as_utc(row.next_run_at).astimezone(timezone).date() == today
    )
    today_items.sort(key=lambda item: item.timestamp, reverse=True)

    return AgentOperationsSummaryRead(
        agent_id=agent_id,
        timezone=timezone_name,
        period_days=period_days,
        generated_at=_iso_utc(now),
        metrics=AgentOperationsMetricsRead(
            running=len(running_sessions) + len(running_runs),
            awaiting_confirmation=len(handoffs) + len(pending_proposals),
            completed=len(completed_messages) + len(completed_runs),
            capability_changes=len(changes),
        ),
        attention_items=attention_items[:8],
        today_items=today_items[:8],
        completion_trend=[
            AgentOperationsTrendPointRead(date=day, value=value)
            for day, value in completion_days.items()
        ],
        capability_changes=changes[:20],
    )


def _scheduled_run_item(
    row: ScheduledTaskRun,
    task: ScheduledTask | None,
    *,
    status: str,
) -> AgentOperationsItemRead:
    timestamp = _event_time(row.finished_at, row.started_at, row.updated_at)
    return AgentOperationsItemRead(
        id=f"scheduled-run-{row.id}",
        kind="scheduled_run",
        title=task.title if task else "定时任务",
        description=(row.error if status == "执行异常" else row.result_summary)
        or ("定时任务正在执行" if status == "进行中" else "定时任务执行异常"),
        status=status,
        timestamp=_iso_utc(timestamp),
        session_id=row.session_id,
    )


def _proposal_reuse_counts(
    db: Session,
    tenant_id: str,
    agent_id: str,
    proposals: list[CapabilityEvolutionProposal],
) -> dict[str, int]:
    if not proposals:
        return {}
    sessions = db.exec(
        select(ChatSession).where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.agent_id == agent_id,
        )
    ).all()
    session_ids = [row.id for row in sessions]
    if not session_ids:
        return {}
    events = db.exec(
        select(AgentEvent).where(
            AgentEvent.tenant_id == tenant_id,
            AgentEvent.session_id.in_(session_ids),
            AgentEvent.event_type == "claude_skill_loaded",
        )
    ).all()
    skill_ids = {row.target_resource_id for row in proposals}
    skills_by_id = {
        row.id: row
        for row in db.exec(
            select(GeneralSkill).where(
                GeneralSkill.tenant_id == tenant_id,
                GeneralSkill.id.in_(skill_ids),
            )
        ).all()
    }
    return {
        proposal.id: sum(
            1
            for event in events
            if proposal.applied_at is not None
            and event.created_at > proposal.applied_at
            and str((event.payload_json or {}).get("slug") or "")
            == getattr(skills_by_id.get(proposal.target_resource_id), "slug", "")
        )
        for proposal in proposals
    }


def _increment_local_day(values: dict[str, int], value: datetime, timezone: ZoneInfo) -> None:
    day = _as_utc(value).astimezone(timezone).date().isoformat()
    if day in values:
        values[day] += 1


def _event_time(*values: datetime | None) -> datetime:
    return next((value for value in values if value is not None), datetime.min)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    return _as_utc(value).isoformat().replace("+00:00", "Z")
