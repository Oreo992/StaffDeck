from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.models import ChatSession, HarnessTaskFrameRecord, new_id, utc_now
from app.harness.task_schema import PlannedTaskFrame

TASK_FRAME_LEASE_SECONDS = 900
FinishStatus = Literal[
    "awaiting_user",
    "blocked",
    "completed",
    "handoff",
    "failed",
    "cancelled",
]


class TaskFrameClaimConflict(RuntimeError):
    pass


class TaskFrameStore:
    """Durable TaskFrame plans with lease-owner fencing.

    This migration-stage Store intentionally does not project state into the
    legacy ``ChatSession`` fields. Production routing remains unchanged until
    the shadow records have been compared against existing behavior.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def persist_plan(
        self,
        session: ChatSession,
        source_turn_id: str,
        frames: list[PlannedTaskFrame],
    ) -> list[HarnessTaskFrameRecord]:
        rows: list[HarnessTaskFrameRecord] = []
        for sequence, frame in enumerate(frames):
            task_id = str(frame.task_id or new_id("task"))
            existing = self._find_task(session.id, task_id)
            if existing is not None:
                rows.append(existing)
                continue
            row = HarnessTaskFrameRecord(
                tenant_id=session.tenant_id,
                session_id=session.id,
                source_turn_id=source_turn_id,
                task_id=task_id,
                kind=frame.kind,
                decision=frame.decision,
                status=frame.status,
                sequence=sequence,
                skill_id=frame.target_skill_id,
                step_id=frame.target_step_id,
                user_intent=frame.user_intent,
                requirements_json=list(frame.requirements),
                slots_json=dict(frame.slot_hints),
                depends_on_json=list(frame.depends_on_task_ids),
            )
            self.db.add(row)
            rows.append(row)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return [
                self._required_task(session.id, str(frame.task_id))
                for frame in frames
                if frame.task_id
            ]
        for row in rows:
            self.db.refresh(row)
        return rows

    def claim(
        self,
        record_id: str,
        lease_owner: str,
    ) -> HarnessTaskFrameRecord:
        now = utc_now()
        result = self.db.exec(
            update(HarnessTaskFrameRecord)
            .where(
                HarnessTaskFrameRecord.id == record_id,
                or_(
                    HarnessTaskFrameRecord.status == "queued",
                    (
                        (HarnessTaskFrameRecord.status == "running")
                        & (HarnessTaskFrameRecord.lease_expires_at <= now)
                    ),
                ),
            )
            .values(
                status="running",
                attempt_no=HarnessTaskFrameRecord.attempt_no + 1,
                state_version=HarnessTaskFrameRecord.state_version + 1,
                lease_owner=lease_owner,
                lease_expires_at=now + timedelta(seconds=TASK_FRAME_LEASE_SECONDS),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            self.db.rollback()
            raise TaskFrameClaimConflict(
                "TaskFrame 已被其他执行者领取或不处于可执行状态。"
            )
        self.db.commit()
        return self._required_record(record_id)

    def finish(
        self,
        record_id: str,
        lease_owner: str,
        *,
        status: FinishStatus,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> HarnessTaskFrameRecord:
        now = utc_now()
        update_result = self.db.exec(
            update(HarnessTaskFrameRecord)
            .where(
                HarnessTaskFrameRecord.id == record_id,
                HarnessTaskFrameRecord.status == "running",
                HarnessTaskFrameRecord.lease_owner == lease_owner,
                HarnessTaskFrameRecord.lease_expires_at > now,
            )
            .values(
                status=status,
                result_json=dict(result or {}),
                error_json=dict(error or {}),
                state_version=HarnessTaskFrameRecord.state_version + 1,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(update_result, "rowcount", 0) != 1:
            self.db.rollback()
            raise TaskFrameClaimConflict(
                "TaskFrame lease 已失效或由其他执行者持有，不能提交结果。"
            )
        self.db.commit()
        return self._required_record(record_id)

    def dependencies_satisfied(self, row: HarnessTaskFrameRecord) -> bool:
        dependencies = [str(item) for item in (row.depends_on_json or []) if str(item)]
        if not dependencies:
            return True
        completed = self.db.exec(
            select(HarnessTaskFrameRecord).where(
                HarnessTaskFrameRecord.session_id == row.session_id,
                HarnessTaskFrameRecord.task_id.in_(dependencies),
                HarnessTaskFrameRecord.status == "completed",
            )
        ).all()
        return {item.task_id for item in completed} == set(dependencies)

    def dependency_results(self, row: HarnessTaskFrameRecord) -> dict[str, dict[str, Any]]:
        dependencies = [str(item) for item in (row.depends_on_json or []) if str(item)]
        if not dependencies:
            return {}
        completed = self.db.exec(
            select(HarnessTaskFrameRecord).where(
                HarnessTaskFrameRecord.session_id == row.session_id,
                HarnessTaskFrameRecord.task_id.in_(dependencies),
                HarnessTaskFrameRecord.status == "completed",
            )
        ).all()
        return {item.task_id: dict(item.result_json or {}) for item in completed}

    def _find_task(self, session_id: str, task_id: str) -> HarnessTaskFrameRecord | None:
        return self.db.exec(
            select(HarnessTaskFrameRecord).where(
                HarnessTaskFrameRecord.session_id == session_id,
                HarnessTaskFrameRecord.task_id == task_id,
            )
        ).first()

    def _required_task(self, session_id: str, task_id: str) -> HarnessTaskFrameRecord:
        row = self._find_task(session_id, task_id)
        if row is None:
            raise TaskFrameClaimConflict("TaskFrame 持久化发生并发冲突且记录不可用。")
        return row

    def _required_record(self, record_id: str) -> HarnessTaskFrameRecord:
        row = self.db.get(HarnessTaskFrameRecord, record_id)
        if row is None:
            raise TaskFrameClaimConflict("TaskFrame 不存在。")
        self.db.refresh(row)
        return row


__all__ = ["TaskFrameClaimConflict", "TaskFrameStore"]
