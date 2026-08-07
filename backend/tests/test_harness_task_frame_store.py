from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.harness_task_frame_store import TaskFrameClaimConflict, TaskFrameStore
from app.db.models import ChatSession, HarnessTaskFrameRecord
from app.harness.task_schema import PlannedTaskFrame


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_persist_plan_is_idempotent_and_does_not_project_into_legacy_session() -> None:
    engine = _engine()
    with Session(engine) as db:
        session = ChatSession(
            id="session-1",
            tenant_id="tenant-demo",
            pending_tasks_json=[{"task_id": "legacy-task"}],
        )
        db.add(session)
        db.commit()
        store = TaskFrameStore(db)
        frames = [
            PlannedTaskFrame(
                task_id="research",
                kind="sop",
                decision="start_new_task",
                target_skill_id="amazon-research",
                target_step_id="collect",
                user_intent="研究 A1",
                slot_hints={"asin": "A1"},
            ),
            PlannedTaskFrame(
                task_id="report",
                depends_on_task_ids=["research"],
                user_intent="生成报告",
            ),
        ]

        first = store.persist_plan(session, "turn-1", frames)
        second = store.persist_plan(session, "turn-1", frames)

        assert [row.id for row in first] == [row.id for row in second]
        assert len(db.exec(select(HarnessTaskFrameRecord)).all()) == 2
        db.refresh(session)
        assert session.pending_tasks_json == [{"task_id": "legacy-task"}]


def test_claim_and_finish_are_fenced_by_lease_owner() -> None:
    engine = _engine()
    with Session(engine) as db:
        session = ChatSession(id="session-1", tenant_id="tenant-demo")
        db.add(session)
        db.commit()
        store = TaskFrameStore(db)
        row = store.persist_plan(
            session,
            "turn-1",
            [PlannedTaskFrame(task_id="research", user_intent="研究 A1")],
        )[0]

        running = store.claim(row.id, "worker-1")
        assert running.status == "running"
        assert running.attempt_no == 1
        with pytest.raises(TaskFrameClaimConflict):
            store.claim(row.id, "worker-2")
        with pytest.raises(TaskFrameClaimConflict):
            store.finish(row.id, "worker-2", status="completed", result={"ok": True})

        completed = store.finish(
            row.id,
            "worker-1",
            status="completed",
            result={"ok": True},
        )
        assert completed.status == "completed"
        assert completed.result_json == {"ok": True}
        assert completed.lease_owner is None


def test_dependencies_become_ready_only_after_all_dependencies_complete() -> None:
    engine = _engine()
    with Session(engine) as db:
        session = ChatSession(id="session-1", tenant_id="tenant-demo")
        db.add(session)
        db.commit()
        store = TaskFrameStore(db)
        research, report = store.persist_plan(
            session,
            "turn-1",
            [
                PlannedTaskFrame(task_id="research"),
                PlannedTaskFrame(task_id="report", depends_on_task_ids=["research"]),
            ],
        )

        assert store.dependencies_satisfied(report) is False
        store.claim(research.id, "worker-1")
        store.finish(research.id, "worker-1", status="completed", result={"asin": "A1"})
        db.refresh(report)

        assert store.dependencies_satisfied(report) is True
        assert store.dependency_results(report) == {"research": {"asin": "A1"}}
