from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from app.core.harness_run_store import HarnessRunConflict, HarnessRunStore
from app.core.harness_task_frame_store import TaskFrameStore
from app.db.models import ChatSession, HarnessRunRecord, utc_now
from app.harness.task_schema import PlannedTaskFrame


def _memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _claimed_frame(db: Session, owner: str = "worker-1"):
    chat_session = ChatSession(id="session-1", tenant_id="tenant-demo")
    db.add(chat_session)
    db.commit()
    frame = TaskFrameStore(db).persist_plan(
        chat_session,
        "turn-1",
        [PlannedTaskFrame(task_id="task-1", user_intent="研究 A1")],
    )[0]
    return TaskFrameStore(db).claim(frame.id, owner)


def test_run_start_is_idempotent_for_same_frame_attempt_and_owner() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        frame = _claimed_frame(db)
        store = HarnessRunStore(db)

        first = store.start(
            frame,
            lease_owner="worker-1",
            requirement={"goal": "研究 A1"},
            capability_snapshot={"snapshot_revision": "sha256:one"},
        )
        repeated = store.start(
            frame,
            lease_owner="worker-1",
            requirement={"goal": "研究 A1"},
            capability_snapshot={"snapshot_revision": "sha256:one"},
        )
        rows = db.exec(select(HarnessRunRecord)).all()

    assert repeated.id == first.id
    assert len(rows) == 1
    assert first.attempt_no == frame.attempt_no == 1


def test_run_renew_and_finish_require_matching_owner_and_attempt() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        frame = _claimed_frame(db)
        store = HarnessRunStore(db)
        run = store.start(
            frame,
            lease_owner="worker-1",
            requirement={},
            capability_snapshot={},
        )

        with pytest.raises(HarnessRunConflict):
            store.renew(run.id, lease_owner="worker-2", attempt_no=1)

        renewed = store.renew(run.id, lease_owner="worker-1", attempt_no=1)
        assert renewed.lease_expires_at is not None
        finished = store.finish(
            run.id,
            lease_owner="worker-1",
            attempt_no=1,
            status="completed",
            action_count=3,
            result={"reply": "done"},
        )

    assert finished.status == "completed"
    assert finished.action_count == 3
    assert finished.result_json == {"reply": "done"}
    assert finished.lease_owner is None


def test_expired_task_frame_lease_cannot_start_run() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        frame = _claimed_frame(db)
        frame.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.add(frame)
        db.commit()

        with pytest.raises(HarnessRunConflict):
            HarnessRunStore(db).start(
                frame,
                lease_owner="worker-1",
                requirement={},
                capability_snapshot={},
            )


def test_cancel_is_fenced_and_terminal() -> None:
    engine = _memory_engine()
    with Session(engine) as db:
        frame = _claimed_frame(db)
        store = HarnessRunStore(db)
        run = store.start(
            frame,
            lease_owner="worker-1",
            requirement={},
            capability_snapshot={},
        )
        cancelled = store.cancel(run.id, lease_owner="worker-1", attempt_no=1)
        assert cancelled.status == "cancelled"

        with pytest.raises(HarnessRunConflict):
            store.finish(
                run.id,
                lease_owner="worker-1",
                attempt_no=1,
                status="completed",
                action_count=1,
            )
