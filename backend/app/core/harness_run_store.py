from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import update
from sqlmodel import Session, select

from app.db.models import HarnessRunRecord, HarnessTaskFrameRecord, utc_now

RUN_LEASE_SECONDS = 900
RunFinishStatus = Literal[
    "awaiting_user",
    "blocked",
    "completed",
    "handoff",
    "failed",
    "cancelled",
    "action_budget",
]


class HarnessRunConflict(RuntimeError):
    pass


class HarnessRunStore:
    """Durable HarnessRun lifecycle fenced by TaskFrame owner and attempt."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def start(
        self,
        frame: HarnessTaskFrameRecord,
        *,
        lease_owner: str,
        requirement: dict[str, Any],
        capability_snapshot: dict[str, Any],
    ) -> HarnessRunRecord:
        self.db.refresh(frame)
        now = utc_now()
        if (
            frame.status != "running"
            or frame.lease_owner != lease_owner
            or frame.lease_expires_at is None
            or frame.lease_expires_at <= now
        ):
            raise HarnessRunConflict(
                "TaskFrame lease 已失效或不属于当前 worker，不能启动 HarnessRun。"
            )
        existing = self.db.exec(
            select(HarnessRunRecord).where(
                HarnessRunRecord.task_frame_record_id == frame.id,
                HarnessRunRecord.attempt_no == frame.attempt_no,
                HarnessRunRecord.lease_owner == lease_owner,
                HarnessRunRecord.status == "running",
            )
        ).first()
        if existing is not None:
            return existing

        run = HarnessRunRecord(
            tenant_id=frame.tenant_id,
            session_id=frame.session_id,
            task_frame_record_id=frame.id,
            task_id=frame.task_id,
            source_turn_id=frame.source_turn_id,
            status="running",
            attempt_no=frame.attempt_no,
            lease_owner=lease_owner,
            lease_expires_at=frame.lease_expires_at,
            task_requirement_json=dict(requirement),
            capability_snapshot_json=dict(capability_snapshot),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def renew(
        self,
        run_id: str,
        *,
        lease_owner: str,
        attempt_no: int,
    ) -> HarnessRunRecord:
        run = self._required(run_id)
        now = utc_now()
        expires_at = now + timedelta(seconds=RUN_LEASE_SECONDS)
        frame_result = self.db.exec(
            update(HarnessTaskFrameRecord)
            .where(
                HarnessTaskFrameRecord.id == run.task_frame_record_id,
                HarnessTaskFrameRecord.status == "running",
                HarnessTaskFrameRecord.lease_owner == lease_owner,
                HarnessTaskFrameRecord.attempt_no == attempt_no,
                HarnessTaskFrameRecord.lease_expires_at > now,
            )
            .values(lease_expires_at=expires_at, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        run_result = self.db.exec(
            update(HarnessRunRecord)
            .where(
                HarnessRunRecord.id == run_id,
                HarnessRunRecord.status == "running",
                HarnessRunRecord.lease_owner == lease_owner,
                HarnessRunRecord.attempt_no == attempt_no,
                HarnessRunRecord.lease_expires_at > now,
            )
            .values(lease_expires_at=expires_at, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if (
            getattr(frame_result, "rowcount", 0) != 1
            or getattr(run_result, "rowcount", 0) != 1
        ):
            self.db.rollback()
            raise HarnessRunConflict("HarnessRun 续租被其他 worker fence。")
        self.db.commit()
        return self._required(run_id)

    def finish(
        self,
        run_id: str,
        *,
        lease_owner: str,
        attempt_no: int,
        status: RunFinishStatus,
        action_count: int,
        result: dict[str, Any] | None = None,
    ) -> HarnessRunRecord:
        now = utc_now()
        update_result = self.db.exec(
            update(HarnessRunRecord)
            .where(
                HarnessRunRecord.id == run_id,
                HarnessRunRecord.status == "running",
                HarnessRunRecord.lease_owner == lease_owner,
                HarnessRunRecord.attempt_no == attempt_no,
                HarnessRunRecord.lease_expires_at > now,
            )
            .values(
                status=status,
                action_count=max(0, int(action_count)),
                result_json=dict(result or {}),
                finished_at=now,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(update_result, "rowcount", 0) != 1:
            self.db.rollback()
            raise HarnessRunConflict("HarnessRun 完成提交被其他 worker fence。")
        self.db.commit()
        return self._required(run_id)

    def cancel(
        self,
        run_id: str,
        *,
        lease_owner: str,
        attempt_no: int,
    ) -> HarnessRunRecord:
        return self.finish(
            run_id,
            lease_owner=lease_owner,
            attempt_no=attempt_no,
            status="cancelled",
            action_count=0,
            result={"status": "cancelled"},
        )

    def _required(self, run_id: str) -> HarnessRunRecord:
        row = self.db.get(HarnessRunRecord, run_id)
        if row is None:
            raise HarnessRunConflict("HarnessRun 不存在。")
        self.db.refresh(row)
        return row


__all__ = ["HarnessRunConflict", "HarnessRunStore"]
